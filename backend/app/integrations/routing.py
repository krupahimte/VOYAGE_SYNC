"""
Driving time between two points.

Live: OSRM public demo server (no key; fair-use only — fine for a student demo).
Mock: great-circle distance × 1.3 road factor at 35 km/h average, min 10 min.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from app.integrations.http import HttpJson, ProviderError, UsageMeter

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"

Point = tuple[float, float]   # (lat, lon)


class Route(BaseModel):
    duration_minutes: float = Field(gt=0, le=24 * 60)
    distance_km: float = Field(ge=0, le=3000)
    source: str = ""


def osrm_url(a: Point, b: Point) -> str:
    return OSRM_URL.format(lat1=a[0], lon1=a[1], lat2=b[0], lon2=b[1])


def parse_osrm(raw: Any, *, source: str) -> Route:
    try:
        if raw.get("code") != "Ok":
            raise ProviderError(f"OSRM returned code {raw.get('code')!r}")
        route = raw["routes"][0]
        return Route(duration_minutes=max(1.0, route["duration"] / 60), distance_km=route["distance"] / 1000,
                     source=source)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise ProviderError(f"Invalid OSRM response: {exc}") from exc


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


class OSRMRouting:
    name = "osrm"

    def __init__(self, http: HttpJson, usage: UsageMeter) -> None:
        self._http = http
        self._usage = usage

    async def route(self, a: Point, b: Point) -> Route:
        self._usage.record(self.name)
        return parse_osrm(await self._http.get(osrm_url(a, b), params={"overview": "false"}), source="osrm")


class MockRouting:
    name = "mock_routing"

    def __init__(self, usage: UsageMeter) -> None:
        self._usage = usage

    async def route(self, a: Point, b: Point) -> Route:
        self._usage.record(self.name)
        km = haversine_km(a, b) * 1.3
        return Route(duration_minutes=max(10.0, km / 35 * 60), distance_km=round(km, 2), source="synthetic")
