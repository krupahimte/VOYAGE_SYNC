"""
Phase 4 — provider clients and parsers, tested offline against recorded
fixtures (real Open-Meteo / Frankfurter / OSRM responses) and the synthetic
Duffel / Geoapify samples, via httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest

from app.integrations.booking import MockBookingProvider
from app.integrations.fixtures import load_fixture
from app.integrations.flights import DuffelFlights, parse_offers, parse_place_suggestions
from app.integrations.fx import FrankfurterFX, RecordedFX, parse_rates
from app.integrations.geocoding import MockGeocoder, OpenMeteoGeocoder, parse_geocoding
from app.integrations.http import HttpJson, ProviderError, UsageMeter
from app.integrations.places import GeoapifyPlaces, parse_places
from app.integrations.routing import MockRouting, OSRMRouting, parse_osrm
from app.integrations.weather import (
    ARCHIVE_URL,
    FORECAST_URL,
    MockWeather,
    OpenMeteoWeather,
    parse_daily,
    plan_request,
    wmo_condition,
)


def transport_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_response(data, status=200) -> httpx.Response:
    return httpx.Response(status, json=data)


# ── Geocoding ─────────────────────────────────────────────────────────────────

class TestGeocoding:
    def test_parse_recorded_santorini(self):
        loc = parse_geocoding(load_fixture("openmeteo_geocoding_santorini"), source="t")
        assert loc.name == "Santorini" and loc.country == "Greece"

    def test_airport_anchor_disambiguates(self):
        raw = load_fixture("openmeteo_geocoding_bali")
        assert parse_geocoding(raw, source="t").country != "Indonesia"           # API's first pick
        assert parse_geocoding(raw, source="t", near=(-8.7482, 115.167)).country == "Indonesia"

    async def test_no_nearby_candidate_uses_airport_proxy(self):
        loc = await MockGeocoder(UsageMeter()).geocode("Goa", near=(15.3808, 73.8314))
        assert loc.source == "airport-proxy" and (loc.latitude, loc.longitude) == (15.3808, 73.8314)

    async def test_live_client_requests_and_caches(self):
        seen = []

        def handler(request: httpx.Request):
            seen.append(request.url.params["name"])
            return json_response(load_fixture("openmeteo_geocoding_santorini"))

        usage = UsageMeter()
        geo = OpenMeteoGeocoder(HttpJson(client=transport_client(handler)), usage)
        await geo.geocode("Santorini")
        await geo.geocode("Santorini")
        assert seen == ["Santorini"] and usage.snapshot() == {"open_meteo_geocoding": 1}

    async def test_no_results_raises(self):
        geo = OpenMeteoGeocoder(HttpJson(client=transport_client(lambda r: json_response({}))), UsageMeter())
        with pytest.raises(ProviderError):
            await geo.geocode("Nowhereville")


# ── Weather ───────────────────────────────────────────────────────────────────

class TestWeather:
    def test_wmo_mapping(self):
        assert wmo_condition(0) == "sunny"
        assert wmo_condition(95) == "thunderstorm"
        assert wmo_condition(65) == "heavy_rain"
        assert wmo_condition(None) == "unknown"

    def test_plan_request_forecast_vs_archive(self):
        loc = parse_geocoding(load_fixture("openmeteo_geocoding_santorini"), source="t")
        today = date(2026, 10, 16)
        url, params, shift = plan_request(loc, date(2026, 10, 18), date(2026, 10, 20), today)
        assert url == FORECAST_URL and shift == 0
        url, params, shift = plan_request(loc, date(2027, 6, 14), date(2027, 6, 19), today)
        assert url == ARCHIVE_URL and shift == 1 and params["start_date"] == "2026-06-14"

    def test_parse_recorded_archive(self):
        days, rejected = parse_daily(load_fixture("openmeteo_archive_santorini_06-14_06-19"), years_shift=1,
                                     source="t")
        assert len(days) == 6 and rejected == []
        assert days[0].date == date(2027, 6, 14)
        assert all(d.temperature_high_c >= d.temperature_low_c for d in days)

    def test_parse_recorded_forecast(self):
        days, _ = parse_daily(load_fixture("openmeteo_forecast_santorini_sample"), years_shift=0, source="t")
        assert len(days) == 5 and all(d.precipitation_probability is None or 0 <= d.precipitation_probability <= 1
                                      for d in days)

    def test_invalid_rows_are_rejected(self):
        raw = {"daily": {"time": ["2026-06-14", "2026-06-15"], "weather_code": [0, 95],
                         "temperature_2m_max": [25, 10], "temperature_2m_min": [18, 20]}}
        days, rejected = parse_daily(raw, years_shift=0, source="t")
        assert [d.date.isoformat() for d in days] == ["2026-06-14"] and len(rejected) == 1

    def test_missing_daily_raises(self):
        with pytest.raises(ProviderError):
            parse_daily({"error": True}, years_shift=0, source="t")

    async def test_live_client_redates_archive_proxy(self):
        requested = {}

        def handler(request: httpx.Request):
            requested.update(dict(request.url.params))
            return json_response(load_fixture("openmeteo_archive_santorini_06-14_06-19"))

        loc = parse_geocoding(load_fixture("openmeteo_geocoding_santorini"), source="t")
        weather = OpenMeteoWeather(HttpJson(client=transport_client(handler)), UsageMeter(), today=date(2026, 10, 16))
        days = await weather.daily(loc, date(2027, 6, 14), date(2027, 6, 19))
        assert requested["start_date"] == "2026-06-14"
        assert days[0].date == date(2027, 6, 14) and days[0].source == "open-meteo-archive-proxy"

    async def test_mock_uses_recorded_window_for_single_day(self):
        loc = parse_geocoding(load_fixture("openmeteo_geocoding_santorini"), source="t")
        days = await MockWeather(UsageMeter()).daily(loc, date(2027, 6, 16), date(2027, 6, 16))
        assert len(days) == 1 and days[0].source == "recorded:open-meteo-archive"


# ── FX ────────────────────────────────────────────────────────────────────────

class TestFx:
    async def test_recorded_rates_convert(self):
        fx = RecordedFX(UsageMeter())
        rates = await fx.rates()
        assert await fx.convert(100, "USD", "EUR") == round(100 * rates.rates["EUR"], 2)
        assert await fx.convert(100, "EUR", "EUR") == 100
        eur_inr = await fx.convert(100, "EUR", "INR")
        assert eur_inr == pytest.approx(100 / rates.rates["EUR"] * rates.rates["INR"], abs=0.01)

    async def test_unknown_currency_raises(self):
        with pytest.raises(ProviderError):
            await RecordedFX(UsageMeter()).convert(10, "USD", "XXX")

    def test_invalid_rates_rejected(self):
        with pytest.raises(ProviderError):
            parse_rates({"base": "USD", "date": "2026-01-01", "rates": {"EUR": -1}}, source="t")

    async def test_live_client_fetches_once_under_concurrency(self):
        calls = []

        def handler(request: httpx.Request):
            calls.append(request.url.params["base"])
            return json_response(load_fixture("frankfurter_latest_USD"))

        fx = FrankfurterFX(HttpJson(client=transport_client(handler)), UsageMeter())
        await asyncio.gather(*(fx.convert(10, "USD", "GBP") for _ in range(5)))
        assert calls == ["USD"]


# ── Routing ───────────────────────────────────────────────────────────────────

class TestRouting:
    def test_parse_recorded_osrm(self):
        route = parse_osrm(load_fixture("osrm_route_jtr_fira"), source="t")
        assert 5 < route.duration_minutes < 30 and 3 < route.distance_km < 15

    def test_bad_code_raises(self):
        with pytest.raises(ProviderError):
            parse_osrm({"code": "NoRoute"}, source="t")

    async def test_live_client_url(self):
        urls = []

        def handler(request: httpx.Request):
            urls.append(str(request.url))
            return json_response(load_fixture("osrm_route_jtr_fira"))

        route = await OSRMRouting(HttpJson(client=transport_client(handler)), UsageMeter()).route(
            (36.3992, 25.4793), (36.4166, 25.4317))
        assert "25.4793,36.3992;25.4317,36.4166" in urls[0] and route.source == "osrm"

    async def test_mock_routing_positive(self):
        route = await MockRouting(UsageMeter()).route((36.3992, 25.4793), (36.4166, 25.4317))
        assert route.duration_minutes >= 10 and route.distance_km > 0


# ── Duffel ────────────────────────────────────────────────────────────────────

class TestDuffel:
    def test_parse_sample_rejects_invalid_offers(self):
        offers, rejected = parse_offers(load_fixture("duffel_offer_request_sample"), source="t")
        assert [o.offer_id[-1] for o in offers] == ["1", "2"]
        assert len(rejected) == 2
        assert any("arrival is not after departure" in r for r in rejected)
        assert any("greater than 0" in r for r in rejected)
        connecting = offers[1]
        assert connecting.stops == 1 and connecting.arrival_time.hour == 9

    def test_parse_place_suggestions(self):
        airport = parse_place_suggestions(load_fixture("duffel_places_suggestions_sample"))
        assert airport.iata == "JTR" and airport.latitude == pytest.approx(36.3992)

    def test_missing_offers_raises(self):
        with pytest.raises(ProviderError):
            parse_offers({"data": {}}, source="t")

    async def test_live_client_request_shape(self):
        captured = {}

        def handler(request: httpx.Request):
            if request.url.path.endswith("/places/suggestions"):
                return json_response(load_fixture("duffel_places_suggestions_sample"))
            captured["headers"] = request.headers
            captured["params"] = dict(request.url.params)
            captured["body"] = json.loads(request.content)
            return json_response(load_fixture("duffel_offer_request_sample"))

        duffel = DuffelFlights(HttpJson(client=transport_client(handler)), UsageMeter(), access_token="duffel_test_x")
        assert (await duffel.find_airport("Santorini")).iata == "JTR"
        offers, rejected = await duffel.search("ATH", "JTR", date(2026, 11, 15), 2, "economy")
        assert captured["headers"]["Duffel-Version"] == "v2"
        assert captured["headers"]["Authorization"] == "Bearer duffel_test_x"
        assert captured["params"]["return_offers"] == "true"
        body = captured["body"]["data"]
        assert body["slices"][0] == {"origin": "ATH", "destination": "JTR", "departure_date": "2026-11-15"}
        assert len(body["passengers"]) == 2 and body["cabin_class"] == "economy"
        assert len(offers) == 2 and len(rejected) == 2

    async def test_http_error_is_provider_error(self):
        duffel = DuffelFlights(HttpJson(client=transport_client(lambda r: httpx.Response(500, text="boom"))),
                               UsageMeter(), access_token="t")
        with pytest.raises(ProviderError):
            await duffel.search("ATH", "JTR", date(2026, 11, 15), 1)

    async def test_invalid_json_is_provider_error(self):
        duffel = DuffelFlights(HttpJson(client=transport_client(lambda r: httpx.Response(200, text="<html>"))),
                               UsageMeter(), access_token="t")
        with pytest.raises(ProviderError):
            await duffel.search("ATH", "JTR", date(2026, 11, 15), 1)


# ── Geoapify ──────────────────────────────────────────────────────────────────

class TestGeoapify:
    def test_parse_sample_rejects_invalid_features(self):
        places, rejected = parse_places(load_fixture("geoapify_places_sample"), source="t")
        assert [p.name for p in places] == ["Caldera View Hotel", "Old Port Lookout"]
        assert len(rejected) == 2
        assert places[1].outdoor and not places[0].outdoor

    async def test_live_client_params(self):
        captured = {}

        def handler(request: httpx.Request):
            captured.update(dict(request.url.params))
            return json_response(load_fixture("geoapify_places_sample"))

        places = GeoapifyPlaces(HttpJson(client=transport_client(handler)), UsageMeter(), api_key="k")
        found, _ = await places.search(["accommodation.hotel"], 36.4, 25.43, radius_m=5000, limit=10)
        assert captured["categories"] == "accommodation.hotel"
        assert captured["filter"] == "circle:25.43,36.4,5000" and captured["apiKey"] == "k"
        assert len(found) == 2


# ── Booking ───────────────────────────────────────────────────────────────────

class TestBooking:
    async def test_quote_is_deterministic_and_consistent(self):
        booking = MockBookingProvider(UsageMeter())
        a = await booking.quote("htl_1", date(2027, 6, 14), date(2027, 6, 19), 1, "luxury")
        b = await booking.quote("htl_1", date(2027, 6, 14), date(2027, 6, 19), 1, "luxury")
        assert a == b and a.nights == 5
        assert a.total_usd == pytest.approx(a.nightly_usd * 5)
        assert 360 * 0.9 <= a.nightly_usd <= 360 * 1.1
