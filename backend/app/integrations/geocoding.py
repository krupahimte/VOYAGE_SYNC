"""
Geocoding: city name → coordinates.

Live: Open-Meteo geocoding API (no key).
Mock: recorded Open-Meteo responses (``openmeteo_geocoding_<slug>``), else
deterministic synthetic coordinates.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.integrations.fixtures import has_fixture, load_fixture, slug
from app.integrations.http import HttpJson, ProviderError, UsageMeter

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


class Location(BaseModel):
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    country: str = ""
    timezone: str = ""
    source: str = ""

    @property
    def key(self) -> str:
        return slug(self.name)


def geocoding_params(name: str) -> dict[str, Any]:
    return {"name": name, "count": 20, "language": "en", "format": "json"}


NEAR_KM = 300.0

Point = tuple[float, float]


def _km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def parse_geocoding(raw: Any, *, source: str, near: Point | None = None) -> Location | None:
    """
    Pick a result. Names are ambiguous ("Bali", "Goa"), so when an anchor point
    is known (the destination airport) the best-ranked candidate within NEAR_KM
    of it wins; otherwise the API's relevance order decides. Airports and
    similar facilities are skipped.
    """
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        return None
    candidates: list[Location] = []
    for r in results:
        if not isinstance(r, dict) or r.get("feature_code") in ("AIRP", "AIRF"):
            continue
        try:
            candidates.append(Location(name=r.get("name", ""), latitude=r.get("latitude"),
                                       longitude=r.get("longitude"), country=r.get("country", ""),
                                       timezone=r.get("timezone", ""), source=source))
        except ValidationError:
            continue
    if near is not None:
        return next((c for c in candidates if _km((c.latitude, c.longitude), near) <= NEAR_KM), None)
    return candidates[0] if candidates else None


def airport_proxy(name: str, near: Point) -> Location:
    """Used when no geocoding candidate lies near the destination airport."""
    return Location(name=name, latitude=near[0], longitude=near[1], source="airport-proxy")


class OpenMeteoGeocoder:
    name = "open_meteo_geocoding"

    def __init__(self, http: HttpJson, usage: UsageMeter) -> None:
        self._http = http
        self._usage = usage
        self._cache: dict[str, Location] = {}

    async def fetch_raw(self, name: str) -> Any:
        self._usage.record(self.name)
        return await self._http.get(GEOCODING_URL, params=geocoding_params(name))

    async def geocode(self, name: str, *, near: Point | None = None) -> Location:
        key = f"{slug(name)}|{near}"
        if key not in self._cache:
            location = parse_geocoding(await self.fetch_raw(name), source="open-meteo", near=near)
            if location is None:
                if near is None:
                    raise ProviderError(f"No geocoding result for '{name}'.")
                location = airport_proxy(name, near)
            self._cache[key] = location
        return self._cache[key]


class MockGeocoder:
    name = "mock_geocoding"

    def __init__(self, usage: UsageMeter) -> None:
        self._usage = usage
        self._seen: set[str] = set()

    async def geocode(self, name: str, *, near: Point | None = None) -> Location:
        if f"{slug(name)}|{near}" not in self._seen:   # counted like the cached live client
            self._usage.record(self.name)
            self._seen.add(f"{slug(name)}|{near}")
        fixture = f"openmeteo_geocoding_{slug(name)}"
        if has_fixture(fixture):
            location = parse_geocoding(load_fixture(fixture), source="recorded:open-meteo", near=near)
            if location is not None:
                return location
        if near is not None:
            return airport_proxy(name, near)
        h = int(hashlib.sha256(slug(name).encode()).hexdigest()[:8], 16)
        return Location(name=name, latitude=round(-40 + (h % 9000) / 100, 4),
                        longitude=round(-170 + (h // 9000 % 34000) / 100, 4), source="synthetic")
