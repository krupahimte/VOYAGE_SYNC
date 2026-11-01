"""
Record real API responses into app/integrations/fixtures/ for mock mode and tests.

Run from backend/:
    .venv\\Scripts\\python.exe scripts\\record_fixtures.py

Keyless providers (Open-Meteo geocoding + archive + forecast, Frankfurter,
OSRM) are always recorded. Duffel and Geoapify are recorded only when
DUFFEL_ACCESS_TOKEN / GEOAPIFY_API_KEY are set (in .env); otherwise the
hand-written *_sample fixtures stay in use.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.integrations.fixtures import save_fixture, slug  # noqa: E402
from app.integrations.flights import DUFFEL_BASE_URL, DUFFEL_VERSION, offer_request_body  # noqa: E402
from app.integrations.fx import FRANKFURTER_URL  # noqa: E402
from app.integrations.geocoding import GEOCODING_URL, geocoding_params, parse_geocoding  # noqa: E402
from app.integrations.http import HttpJson  # noqa: E402
from app.integrations.places import GEOAPIFY_PLACES_URL, places_params  # noqa: E402
from app.integrations.routing import osrm_url  # noqa: E402
from app.integrations.weather import ARCHIVE_URL, FORECAST_URL  # noqa: E402

CITIES = ["Santorini", "Mumbai", "London", "Paris", "Lisbon", "Rome", "Barcelona", "Tokyo", "Kyoto", "Bali",
          "Dubai", "Singapore", "Bangkok", "Goa", "Jaipur", "Delhi", "New York", "Reykjavik", "Istanbul",
          "Amsterdam", "Sydney", "Athens"]

# (city, start MM-DD, end MM-DD) — archive proxies for demo/benchmark trips.
WEATHER_WINDOWS = [("Santorini", "06-14", "06-19"), ("Tokyo", "03-01", "03-08"), ("Lisbon", "05-10", "05-14"),
                   ("Reykjavik", "01-10", "01-15"), ("Goa", "07-01", "07-05")]


async def main() -> None:
    settings = get_settings()
    http = HttpJson(timeout_s=30)
    today = date.today()

    locations = {}
    for city in CITIES:
        raw = await http.get(GEOCODING_URL, params=geocoding_params(city))
        save_fixture(f"openmeteo_geocoding_{slug(city)}", raw)
        locations[city] = parse_geocoding(raw, source="open-meteo")
        print("geocoding", city, "->", locations[city] and (locations[city].latitude, locations[city].longitude))

    for city, start, end in WEATHER_WINDOWS:
        loc = locations[city]
        # most recent year whose window ended at least a week ago (archive lag)
        archive_year = today.year if date.fromisoformat(f"{today.year}-{end}") <= today - timedelta(days=7)             else today.year - 1
        raw = await http.get(ARCHIVE_URL, params={
            "latitude": loc.latitude, "longitude": loc.longitude, "timezone": "auto",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
            "start_date": f"{archive_year}-{start}", "end_date": f"{archive_year}-{end}",
        })
        raw["_recorded_year"] = archive_year
        save_fixture(f"openmeteo_archive_{slug(city)}_{start}_{end}", raw)
        print("archive", city, archive_year, start, end)

    loc = locations["Santorini"]
    raw = await http.get(FORECAST_URL, params={
        "latitude": loc.latitude, "longitude": loc.longitude, "timezone": "auto",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,wind_speed_10m_max",
        "start_date": today.isoformat(), "end_date": (today + timedelta(days=4)).isoformat(),
    })
    save_fixture("openmeteo_forecast_santorini_sample", raw)
    print("forecast Santorini")

    save_fixture("frankfurter_latest_USD", await http.get(FRANKFURTER_URL, params={"base": "USD"}))
    print("frankfurter USD")

    # Santorini airport (JTR) → Fira
    save_fixture("osrm_route_jtr_fira", await http.get(osrm_url((36.3992, 25.4793), (36.4166, 25.4317)),
                                                      params={"overview": "false"}))
    print("osrm JTR -> Fira")

    if settings.duffel_access_token:
        headers = {"Authorization": f"Bearer {settings.duffel_access_token}", "Duffel-Version": DUFFEL_VERSION,
                   "Accept": "application/json"}
        save_fixture("duffel_places_suggestions_recorded",
                     await http.get(f"{DUFFEL_BASE_URL}/places/suggestions", params={"query": "Santorini"},
                                    headers=headers))
        save_fixture("duffel_offer_request_recorded", await http.post(
            f"{DUFFEL_BASE_URL}/air/offer_requests", params={"return_offers": "true", "supplier_timeout": "20000"},
            json=offer_request_body("ATH", "JTR", today + timedelta(days=30), 1, "economy"), headers=headers))
        print("duffel recorded")
    else:
        print("duffel skipped (no DUFFEL_ACCESS_TOKEN)")

    if settings.geoapify_api_key:
        save_fixture("geoapify_places_recorded", await http.get(GEOAPIFY_PLACES_URL, params=places_params(
            ["accommodation.hotel", "tourism.sights"], loc.latitude, loc.longitude, 6000, 20,
            settings.geoapify_api_key)))
        print("geoapify recorded")
    else:
        print("geoapify skipped (no GEOAPIFY_API_KEY)")


if __name__ == "__main__":
    asyncio.run(main())
