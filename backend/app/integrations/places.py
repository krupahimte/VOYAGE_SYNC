"""
Places search (hotels and attractions).

Live: Geoapify Places API v2 (``/v2/places``), GeoJSON FeatureCollection.
Mock: synthetic Geoapify-format features with fictional, generic names,
run through the same parser.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.integrations.http import HttpJson, ProviderError, UsageMeter, reason

GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"

# Category prefixes that describe outdoor places.
OUTDOOR_PREFIXES = ("natural", "beach", "leisure.park", "tourism.sights", "tourism.attraction", "activity",
                    "sport")


class Place(BaseModel):
    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str = ""
    categories: list[str] = Field(default_factory=list)
    source: str = ""

    @property
    def outdoor(self) -> bool:
        return any(c.startswith(OUTDOOR_PREFIXES) for c in self.categories)


def places_params(categories: list[str], lat: float, lon: float, radius_m: int, limit: int, api_key: str) -> dict:
    return {"categories": ",".join(categories), "filter": f"circle:{lon},{lat},{radius_m}",
            "bias": f"proximity:{lon},{lat}", "limit": limit, "apiKey": api_key}


def parse_places(raw: Any, *, source: str) -> tuple[list[Place], list[str]]:
    features = raw.get("features") if isinstance(raw, dict) else None
    if not isinstance(features, list):
        raise ProviderError("Geoapify response has no features list.")
    valid, rejected = [], []
    for f in features:
        props = (f or {}).get("properties") or {}
        try:
            valid.append(Place(
                place_id=props["place_id"],
                name=props["name"],
                latitude=props["lat"],
                longitude=props["lon"],
                address=props.get("formatted") or props.get("address_line2") or "",
                categories=list(props.get("categories") or []),
                source=source,
            ))
        except (KeyError, TypeError, ValidationError) as exc:
            rejected.append(f"{props.get('place_id', '?')}: {reason(exc)}")
    return valid, rejected


class GeoapifyPlaces:
    name = "geoapify"

    def __init__(self, http: HttpJson, usage: UsageMeter, *, api_key: str) -> None:
        self._http = http
        self._usage = usage
        self._api_key = api_key

    async def search(self, categories: list[str], lat: float, lon: float, *, radius_m: int = 6000,
                     limit: int = 20, city: str = "") -> tuple[list[Place], list[str]]:
        self._usage.record(self.name)
        raw = await self._http.get(GEOAPIFY_PLACES_URL,
                                   params=places_params(categories, lat, lon, radius_m, limit, self._api_key))
        return parse_places(raw, source="geoapify")


# Fictional names per category for mock mode ("{city}" is replaced).
_MOCK_NAMES: dict[str, list[str]] = {
    "accommodation.hotel": ["{city} Harbour Hotel", "{city} Central Inn", "{city} Vista Suites",
                            "{city} Old Town Residence", "Blue Horizon Hotel"],
    "accommodation.hostel": ["{city} Backpackers", "Sunrise Hostel", "Old Port Hostel"],
    "accommodation.apartment": ["{city} Garden Apartments", "Harbourview Flats", "Courtyard Studios"],
    "tourism.sights": ["{city} Old Town", "Lighthouse Point", "Castle Ruins", "Harbour Promenade",
                       "Clifftop Chapel"],
    "entertainment.museum": ["{city} Archaeological Museum", "Maritime Museum", "Folk Art Museum",
                             "Contemporary Art Gallery"],
    "catering.restaurant": ["Taverna Elena", "Harbour Grill", "Blue Door Kitchen", "Olive & Vine",
                            "The Fig Tree", "Sea Salt Bistro", "Lemon Court"],
    "catering.cafe": ["Morning Light Café", "Corner Roastery", "Kiosk by the Square"],
    "natural": ["Coastal Trail", "Volcanic Rim Path", "Cedar Ridge"],
    "beach": ["Red Sand Beach", "Pebble Cove", "Long Beach"],
    "leisure.park": ["{city} Botanical Garden", "Seafront Park"],
    "leisure.spa": ["Thermal Spa", "Stillwater Spa"],
    "activity": ["Sea Kayak Centre", "Sailing Club", "Climbing Wall"],
    "commercial": ["Artisan Market", "Design District", "Old Bazaar"],
    "catering.bar": ["Rooftop Bar", "Jazz Cellar", "Harbour Pub"],
}


def _h(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class MockPlaces:
    name = "mock_places"

    def __init__(self, usage: UsageMeter, drift: Any = None) -> None:
        self._usage = usage
        self.drift = drift                  # evaluation only (app.integrations.drift)

    def build_raw(self, categories: list[str], lat: float, lon: float, limit: int, city: str) -> dict:
        features = []
        for category in categories:
            names = _MOCK_NAMES.get(category) or _MOCK_NAMES.get(category.split(".")[0]) or []
            for i, template in enumerate(names):
                name = template.replace("{city}", city or "City")
                h = _h(city, category, name)
                plat = round(lat + ((h % 400) - 200) / 10000, 6)
                plon = round(lon + ((h // 400 % 400) - 200) / 10000, 6)
                features.append({"type": "Feature", "properties": {
                    "name": name, "place_id": f"mock_{h:08x}", "lat": plat, "lon": plon,
                    "formatted": f"{name}, {10 + h % 90} Main Street, {city}",
                    "categories": [category], "datasource": {"sourcename": "voyagesync-mock"},
                }, "geometry": {"type": "Point", "coordinates": [plon, plat]}})
        if self.drift is not None:
            features = self.drift.filter(features, lambda f: f["properties"]["place_id"])
        return {"type": "FeatureCollection", "features": features[:limit]}

    async def search(self, categories: list[str], lat: float, lon: float, *, radius_m: int = 6000,
                     limit: int = 20, city: str = "") -> tuple[list[Place], list[str]]:
        self._usage.record(self.name)
        return parse_places(self.build_raw(categories, lat, lon, limit, city), source="mock")
