"""
Phase 4 — real agents (Flight, Hotel, Itinerary, Weather, Budget) with
output validation, provider selection and fallbacks. All offline.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

from app.agents.base import AgentResult
from app.agents.budget import BudgetAgent
from app.agents.flight import FlightAgent
from app.agents.hotel import HotelAgent
from app.agents.itinerary import DayPlanAgent, TransferAgent
from app.agents.providers import FallbackProvider, build_providers
from app.agents.registry import build_agent_registry
from app.agents.validation import finish, validate_output
from app.agents.weather import WeatherAgent
from app.config import Settings
from app.core.models import NodeStatus, NodeType, TaskNode
from app.core.orchestrator import Orchestrator
from app.integrations.fixtures import has_fixture, load_fixture, slug
from app.integrations.flights import AirportInfo, MockFlights, parse_offers
from app.integrations.http import ProviderError, UsageMeter
from app.integrations.places import MockPlaces
from app.integrations.weather import DailyWeather
from app.llm.client import MockLLM
from app.schemas.trip import TripGoal

MOCK = Settings(voyagesync_mode="mock")


def node(node_type: NodeType, nid: str = "n") -> TaskNode:
    return TaskNode(id=nid, type=node_type, agent="test")


def parents(**by_id: tuple[NodeType, dict]) -> dict:
    return {"_parents": {pid: {"type": t.value, "output": out} for pid, (t, out) in by_id.items()}}


def santorini(**overrides) -> TripGoal:
    data = dict(destination="Santorini", departure_city="Mumbai", departure_date=date(2027, 6, 14),
                return_date=date(2027, 6, 19), travelers=1, budget=4000, currency="USD",
                travel_style="luxury", interests=["relaxation", "food"])
    return TripGoal(**(data | overrides))


async def plan(goal: TripGoal, providers=None):
    llm = MockLLM()
    providers = providers or build_providers(MOCK, llm=llm)
    orch = Orchestrator(llm=providers.llm, registry=build_agent_registry(providers=providers))
    return await orch.plan_trip(goal)


# ── Whole trip with real agents (mock providers) ──────────────────────────────

class TestTripWithRealAgents:
    async def test_santorini_plan_is_valid(self):
        result = await plan(santorini())
        assert result.success, result.error
        assert all(n.status == NodeStatus.DONE for n in result.graph_store.list_nodes())
        assert [c for c in result.contract_checks if not c.holds] == []
        assert len(result.contract_checks) == 27
        budget = result.graph_store.get_node("budget_check").output
        assert budget["within_budget"] is True
        assert budget["fx_source"] == "recorded:frankfurter"

    async def test_recorded_fixtures_are_used(self):
        store = (await plan(santorini())).graph_store
        weather = store.get_node("weather_day_2").output
        assert weather["source"] == "recorded:open-meteo-archive"
        flight = store.get_node("flight_outbound").output
        assert (flight["origin_iata"], flight["destination_iata"]) == ("BOM", "JTR")
        assert flight["arrival_time"] == "2027-06-14T14:00:00"     # keeps the §5.3 timeline

    async def test_usage_is_reported(self):
        result = await plan(santorini())
        usage = result.usage
        assert usage["llm_calls"] == 1 + 5                          # decomposition + 5 day plans
        assert usage["api_calls"]["mock_fx"] == 1                   # rates fetched once
        assert usage["api_calls_total"] > 10
        assert set(usage["provider_modes"].values()) <= {"mock"}

    async def test_hotel_to_transfer_edges(self):
        store = (await plan(santorini())).graph_store
        pairs = {(e.source_id, e.destination_id): e.contract for e in store.list_edges()}
        assert pairs[("hotel_stay", "airport_transfer_in")] == "TRANSFER_SERVES_HOTEL"
        assert pairs[("hotel_stay", "airport_transfer_out")] == "TRANSFER_SERVES_HOTEL"

    @pytest.mark.parametrize("overrides", [
        dict(destination="Tokyo", departure_city="London", currency="GBP", travelers=3,
             departure_date=date(2027, 3, 1), return_date=date(2027, 3, 8), interests=["culture", "nature"]),
        dict(destination="Goa", currency="INR", budget=300000, travel_style="budget",
             preferences={"accommodation": "hostel", "pace": "relaxed"}),
        dict(destination="Atlantis Springs", departure_city="Nowhere Junction", currency="EUR"),
    ])
    async def test_other_trips_are_valid(self, overrides):
        result = await plan(santorini(**overrides))
        assert result.success, [e for e in result.scheduler_result.log if e.event == "FAILED"]
        assert [c for c in result.contract_checks if not c.holds] == []


# ── Flight agent ──────────────────────────────────────────────────────────────

class SampleFlights:
    """Serves the synthetic Duffel sample (2 valid, 2 invalid offers)."""
    name = "sample"

    def __init__(self, raw: dict | None = None) -> None:
        self.raw = raw or load_fixture("duffel_offer_request_sample")

    async def find_airport(self, city: str) -> AirportInfo:
        return {"Athens": AirportInfo(iata="ATH", name="Athens"),
                "Santorini": AirportInfo(iata="JTR", name="Santorini")}[city]

    async def search(self, *args, **kwargs):
        return parse_offers(self.raw, source="sample")


def flight_inputs(**extra) -> dict:
    return {"origin": "Athens", "destination": "Santorini", "departure_date": "2026-11-15",
            "direction": "outbound", "currency": "USD", "travelers": 1} | extra


class TestFlightAgent:
    async def test_picks_cheapest_valid_offer_and_counts_rejections(self):
        providers = build_providers(MOCK, llm=MockLLM())
        providers.flights = SampleFlights()
        result = await FlightAgent(providers).run(node(NodeType.FLIGHT), flight_inputs(), {})
        assert result.success, result.error
        out = result.output
        assert out["offer_id"].endswith("2") and out["stops"] == 1     # 118.90 EUR beats 142.30 EUR
        assert out["offers_considered"] == 2 and out["offers_rejected"] == 2
        assert out["price_original"] == {"amount": 118.9, "currency": "EUR"}
        rates = await providers.fx.rates()
        assert out["cost"] == pytest.approx(118.9 / rates.rates["EUR"], abs=0.01)

    async def test_wrong_date_offers_are_rejected(self):
        providers = build_providers(MOCK, llm=MockLLM())
        providers.flights = SampleFlights()
        result = await FlightAgent(providers).run(node(NodeType.FLIGHT),
                                                  flight_inputs(departure_date="2026-11-16"), {})
        assert not result.success and not result.retryable
        assert "no valid flight offers" in result.error

    async def test_provider_error_is_retryable(self):
        class Down(SampleFlights):
            async def search(self, *a, **k):
                raise ProviderError("timeout")

        providers = build_providers(MOCK, llm=MockLLM())
        providers.flights = Down()
        result = await FlightAgent(providers).run(node(NodeType.FLIGHT), flight_inputs(), {})
        assert not result.success and result.retryable


# ── Hotel, transfer, day plan, weather, budget ────────────────────────────────

class TestOtherAgents:
    async def test_hotel_agent_hostel_in_inr(self):
        providers = build_providers(MOCK, llm=MockLLM())
        result = await HotelAgent(providers).run(node(NodeType.STAY), {
            "city": "Goa", "check_in": "2027-07-01", "check_out": "2027-07-05", "travelers": 2,
            "currency": "INR", "travel_style": "budget", "accommodation": "hostel"}, {})
        assert result.success, result.error
        out = result.output
        assert "Hostel" in out["hotel_name"] or "Backpackers" in out["hotel_name"]
        assert out["nights"] == 4 and out["currency"] == "INR" and out["cost"] > 1000

    async def test_transfer_follows_flight_and_hotel(self):
        providers = build_providers(MOCK, llm=MockLLM())
        flight = {"arrival_time": "2027-06-14T14:00:00",
                  "arrival_airport": {"name": "JTR", "latitude": 36.3992, "longitude": 25.4793}}
        stay = {"hotel_id": "h1", "hotel_name": "Hotel One", "latitude": 36.4166, "longitude": 25.4317}
        inputs = {"direction": "arrival", "city": "Santorini", "pickup_offset_minutes": 30, "currency": "USD"}
        inputs |= parents(f=(NodeType.FLIGHT, flight), h=(NodeType.STAY, stay))
        out = (await TransferAgent(providers).run(node(NodeType.TRANSFER), inputs, {})).output
        assert out["pickup_time"] == "2027-06-14T14:30:00" and out["hotel_id"] == "h1"
        assert out["duration_minutes"] % 5 == 0 and out["dropoff_location"] == "Hotel One"

    async def test_departure_transfer_leaves_three_hours_before(self):
        providers = build_providers(MOCK, llm=MockLLM())
        flight = {"departure_time": "2027-06-19T16:00:00",
                  "departure_airport": {"name": "JTR", "latitude": 36.3992, "longitude": 25.4793}}
        stay = {"hotel_id": "h1", "latitude": 36.4166, "longitude": 25.4317}
        inputs = {"direction": "departure", "city": "Santorini", "airport_buffer_minutes": 180}
        inputs |= parents(f=(NodeType.FLIGHT, flight), h=(NodeType.STAY, stay))
        out = (await TransferAgent(providers).run(node(NodeType.TRANSFER), inputs, {})).output
        assert out["dropoff_time"] == "2027-06-19T13:00:00"

    async def _day(self, providers, weather: dict | None = None, transfer: dict | None = None):
        inputs = {"day": 2, "date": "2027-06-15", "city": "Santorini", "interests": ["nature"], "currency": "USD"}
        by_id = {"h": (NodeType.STAY, {"hotel_id": "h1", "latitude": 36.4166, "longitude": 25.4317})}
        if weather:
            by_id["w"] = (NodeType.WEATHER, weather)
        if transfer:
            by_id["t"] = (NodeType.TRANSFER, transfer)
        return await DayPlanAgent(providers).run(node(NodeType.DAY_PLAN), inputs | parents(**by_id), {})

    async def test_day_plan_storm_has_no_outdoor_items(self):
        providers = build_providers(MOCK, llm=MockLLM())
        sunny = (await self._day(providers, {"condition": "sunny", "is_storm": False})).output
        stormy = (await self._day(providers, {"condition": "thunderstorm", "is_storm": True})).output
        assert any(a["outdoor"] for a in sunny["activities"])
        assert not any(a["outdoor"] for a in stormy["activities"])
        assert stormy["hotel_id"] == "h1"

    async def test_day_plan_late_arrival(self):
        providers = build_providers(MOCK, llm=MockLLM())
        transfer = {"direction": "arrival", "dropoff_time": "2027-06-15T20:15:00"}
        out = (await self._day(providers, transfer=transfer)).output
        assert len(out["activities"]) == 1 and out["activities"][0]["name"].startswith("Late dinner")
        assert out["start_time"] >= "2027-06-15T21:15:00"

    async def test_day_plan_llm_descriptions_and_fallback(self):
        good = (await self._day(build_providers(MOCK, llm=MockLLM()))).output
        assert good["descriptions_source"] == "llm" and all(a["description"] for a in good["activities"])

        bad_llm = MockLLM(handler=lambda m: json.dumps({"descriptions": {"Invented Place": "x"}}))
        fallback = (await self._day(build_providers(MOCK, llm=bad_llm))).output
        assert fallback["descriptions_source"] == "template"
        assert [a["name"] for a in fallback["activities"]] == [a["name"] for a in good["activities"]]

        garbage = (await self._day(build_providers(MOCK, llm=MockLLM(handler=lambda m: "no json")))).output
        assert garbage["descriptions_source"] == "template"

    async def test_weather_agent_reports_storm(self):
        class Stormy:
            name = "stormy"

            async def daily(self, location, start, end):
                return [DailyWeather(date=start, condition="thunderstorm", is_storm=True, weather_code=95,
                                     temperature_high_c=22, temperature_low_c=17, source="t")]

        providers = build_providers(MOCK, llm=MockLLM())
        providers.weather = Stormy()
        out = (await WeatherAgent(providers).run(node(NodeType.WEATHER),
                                                 {"date": "2027-06-16", "city": "Santorini", "day": 3}, {})).output
        assert out["is_storm"] is True and out["condition"] == "thunderstorm"

    async def test_budget_converts_foreign_costs(self):
        providers = build_providers(MOCK, llm=MockLLM())
        inputs = {"budget_limit": 1000, "currency": "USD"} | parents(
            a=(NodeType.FLIGHT, {"cost": 100.0, "currency": "EUR"}),
            b=(NodeType.STAY, {"cost": 200.0, "currency": "USD"}),
        )
        out = (await BudgetAgent(providers).run(node(NodeType.BUDGET), inputs, {})).output
        rates = await providers.fx.rates()
        assert out["line_items"]["a"] == pytest.approx(100 / rates.rates["EUR"], abs=0.01)
        assert out["line_items"]["b"] == 200.0 and out["converted_items"] == ["a"]


# ── Output validation ─────────────────────────────────────────────────────────

class TestOutputValidation:
    def test_flight_arrival_before_departure(self):
        out = {"direction": "outbound", "flight_number": "ZZ1", "origin_iata": "ATH", "destination_iata": "JTR",
               "departure_time": "2026-11-15T12:00:00", "arrival_time": "2026-11-15T11:00:00",
               "cost": 100, "currency": "USD"}
        assert any("arrival_time" in e for e in validate_output(NodeType.FLIGHT, out))
        result = finish(NodeType.FLIGHT, out)
        assert isinstance(result, AgentResult) and not result.success and not result.retryable

    def test_zero_price_rejected(self):
        out = {"direction": "outbound", "flight_number": "ZZ1", "origin_iata": "ATH", "destination_iata": "JTR",
               "departure_time": "2026-11-15T10:00:00", "arrival_time": "2026-11-15T11:00:00",
               "cost": 0, "currency": "USD"}
        assert validate_output(NodeType.FLIGHT, out)

    def test_overlapping_activities_rejected(self):
        act = lambda n, s, e: {"name": n, "start": s, "end": e, "outdoor": False, "cost": 0}  # noqa: E731
        out = {"date": "2027-06-15", "hotel_id": "h", "cost": 0, "activities": [
            act("A", "2027-06-15T09:00:00", "2027-06-15T11:00:00"),
            act("B", "2027-06-15T10:00:00", "2027-06-15T12:00:00")]}
        assert any("overlaps" in e for e in validate_output(NodeType.DAY_PLAN, out))

    def test_inconsistent_budget_rejected(self):
        out = {"budget_limit": 100, "total_cost": 150, "within_budget": True, "line_items": {"a": 150}}
        assert validate_output(NodeType.BUDGET, out)

    def test_stay_dates_checked(self):
        out = {"hotel_id": "h", "hotel_name": "H", "check_in": "2027-06-19", "check_out": "2027-06-14",
               "checkin_cutoff": "2027-06-19T23:59:00", "nights": 5, "cost": 10, "latitude": 1, "longitude": 1}
        assert validate_output(NodeType.STAY, out)


# ── Provider selection and fallback ───────────────────────────────────────────

class TestProviderSelection:
    def test_mock_mode_uses_mocks(self):
        providers = build_providers(MOCK, llm=MockLLM())
        assert set(providers.modes.values()) == {"mock"}

    def test_live_without_keys_warns_and_uses_mock_for_keyed_apis(self, caplog):
        with caplog.at_level(logging.WARNING):
            providers = build_providers(Settings(voyagesync_mode="live"), llm=MockLLM())
        assert providers.modes["flights"] == "mock" and providers.modes["places"] == "mock"
        assert providers.modes["weather"] == "live" and providers.modes["fx"] == "live"
        assert "DUFFEL_ACCESS_TOKEN is empty" in caplog.text and "GEOAPIFY_API_KEY is empty" in caplog.text

    def test_live_with_keys_uses_duffel_and_geoapify(self):
        providers = build_providers(Settings(voyagesync_mode="live", duffel_access_token="duffel_test_x",
                                             geoapify_api_key="k"), llm=MockLLM())
        assert set(providers.modes[k] for k in ("flights", "places", "weather", "fx", "routing")) == {"live"}
        assert isinstance(providers.flights, FallbackProvider)

    async def test_fallback_on_provider_error(self, caplog):
        class Broken:
            name = "broken"

            async def route(self, a, b):
                raise ProviderError("503")

        from app.integrations.routing import MockRouting
        provider = FallbackProvider(Broken(), MockRouting(UsageMeter()), "routing")
        with caplog.at_level(logging.WARNING):
            route = await provider.route((36.39, 25.47), (36.41, 25.43))
        assert route.source == "synthetic" and provider.fallbacks == 1
        assert "falling back to mock" in caplog.text


# ── Live mode end to end, every HTTP call served offline ──────────────────────

def _live_handler(trip_origin_iata: str):
    airports = {v["iata"]: v for v in load_fixture("airports")["cities"].values()}
    by_city = {slug(k): v for k, v in load_fixture("airports")["cities"].items()}
    counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        host, params = request.url.host, dict(request.url.params)
        counts[host] = counts.get(host, 0) + 1
        if host == "geocoding-api.open-meteo.com":
            name = f"openmeteo_geocoding_{slug(params['name'])}"
            return httpx.Response(200, json=load_fixture(name) if has_fixture(name) else {})
        if host in ("api.open-meteo.com", "archive-api.open-meteo.com"):
            daily = load_fixture("openmeteo_archive_santorini_06-14_06-19")["daily"]
            start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
            days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
            out = {"time": days}
            for key, values in daily.items():
                if key != "time":
                    out[key] = [values[i % len(values)] for i in range(len(days))]
            return httpx.Response(200, json={"daily": out})
        if host == "api.frankfurter.dev":
            return httpx.Response(200, json=load_fixture("frankfurter_latest_USD"))
        if host == "router.project-osrm.org":
            return httpx.Response(200, json=load_fixture("osrm_route_jtr_fira"))
        if host == "api.duffel.com" and request.url.path.endswith("/places/suggestions"):
            info = by_city[slug(params["query"])]
            return httpx.Response(200, json={"data": [{"type": "airport", "iata_code": info["iata"],
                                                       "name": info["name"], "city_name": info["city"],
                                                       "latitude": info["latitude"], "longitude": info["longitude"]}]})
        if host == "api.duffel.com":
            body = json.loads(request.content)["data"]
            sl = body["slices"][0]
            direction = "outbound" if sl["origin"] == trip_origin_iata else "return"
            raw = MockFlights(UsageMeter()).build_raw(
                AirportInfo(**airports[sl["origin"]]), AirportInfo(**airports[sl["destination"]]),
                date.fromisoformat(sl["departure_date"]), len(body["passengers"]), body["cabin_class"], direction)
            return httpx.Response(200, json=raw)
        if host == "api.geoapify.com":
            lon, lat, _ = params["filter"].removeprefix("circle:").split(",")
            raw = MockPlaces(UsageMeter()).build_raw(params["categories"].split(","), float(lat), float(lon),
                                                     int(params["limit"]), "Santorini")
            return httpx.Response(200, json=raw)
        return httpx.Response(404, json={"error": f"unexpected {request.url}"})

    return handler, counts


class TestLiveModeOffline:
    async def test_full_trip_through_live_clients(self):
        handler, counts = _live_handler("BOM")
        settings = Settings(voyagesync_mode="live", duffel_access_token="duffel_test_x", geoapify_api_key="k")
        providers = build_providers(settings, llm=MockLLM(),
                                    client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        result = await plan(santorini(), providers)
        assert result.success, [e for e in result.scheduler_result.log if e.event == "FAILED"]
        assert [c for c in result.contract_checks if not c.holds] == []
        store = result.graph_store
        assert store.get_node("flight_outbound").output["source"] == "duffel-test"
        assert store.get_node("hotel_stay").output["source"] == "geoapify"
        assert store.get_node("airport_transfer_in").output["source"] == "osrm"
        assert store.get_node("weather_day_1").output["source"] == "open-meteo-archive-proxy"
        assert store.get_node("budget_check").output["fx_source"] == "frankfurter"
        assert counts["api.frankfurter.dev"] == 1
        assert all(getattr(getattr(providers, k), "fallbacks", 0) == 0
                   for k in ("geocoder", "weather", "fx", "routing", "flights", "places"))

    async def test_live_outage_falls_back_to_mock_and_still_plans(self):
        down = lambda request: httpx.Response(503, text="maintenance")  # noqa: E731
        settings = Settings(voyagesync_mode="live", duffel_access_token="duffel_test_x", geoapify_api_key="k")
        providers = build_providers(settings, llm=MockLLM(),
                                    client=httpx.AsyncClient(transport=httpx.MockTransport(down)))
        result = await plan(santorini(), providers)
        assert result.success
        assert [c for c in result.contract_checks if not c.holds] == []
        assert providers.flights.fallbacks > 0 and providers.weather.fallbacks > 0
