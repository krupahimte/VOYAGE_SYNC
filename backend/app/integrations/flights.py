"""
Flight search.

Live: Duffel API in test mode (sandbox airline "Duffel Airways"), API v2.
  - GET  /places/suggestions?query=<city>        → airport for a city
  - POST /air/offer_requests?return_offers=true  → offers for one slice
Mock: airport table (``airports`` fixture) + synthetic offers written in
Duffel's response format and run through the same parser.

Duffel returns local, offset-free times (``departing_at``), matching the
naive local-time convention used by the contracts.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.integrations.fixtures import load_fixture, slug
from app.integrations.http import HttpJson, ProviderError, UsageMeter, reason

DUFFEL_BASE_URL = "https://api.duffel.com"
DUFFEL_VERSION = "v2"


class AirportInfo(BaseModel):
    iata: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = ""
    city: str = ""
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class FlightOffer(BaseModel):
    offer_id: str = Field(min_length=1)
    airline: str = Field(min_length=1)
    airline_iata: str = ""
    flight_number: str = Field(min_length=1)
    origin: AirportInfo
    destination: AirportInfo
    departure_time: datetime
    arrival_time: datetime
    stops: int = Field(ge=0)
    total_amount: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    cabin_class: str = "economy"
    source: str = ""

    @field_validator("departure_time", "arrival_time")
    @classmethod
    def naive_local(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=None)

    @model_validator(mode="after")
    def sane(self) -> "FlightOffer":
        if self.arrival_time <= self.departure_time:
            raise ValueError("arrival is not after departure")
        if self.origin.iata == self.destination.iata:
            raise ValueError("origin equals destination")
        if self.arrival_time - self.departure_time > timedelta(hours=48):
            raise ValueError("journey longer than 48 h")
        return self


def _airport(raw: dict) -> dict:
    return {"iata": raw.get("iata_code"), "name": raw.get("name", ""),
            "city": raw.get("city_name") or (raw.get("city") or {}).get("name", ""),
            "latitude": raw.get("latitude"), "longitude": raw.get("longitude")}


def parse_offers(raw: Any, *, source: str) -> tuple[list[FlightOffer], list[str]]:
    """Validate Duffel offers (first slice only). Returns (valid, rejection reasons)."""
    data = raw.get("data") if isinstance(raw, dict) else None
    offers = data.get("offers") if isinstance(data, dict) else None
    if not isinstance(offers, list):
        raise ProviderError("Duffel response has no data.offers list.")
    valid, rejected = [], []
    for item in offers:
        oid = item.get("id", "?") if isinstance(item, dict) else "?"
        try:
            sl = item["slices"][0]
            segments = sl["segments"]
            first, last = segments[0], segments[-1]
            carrier = first.get("marketing_carrier") or item.get("owner") or {}
            valid.append(FlightOffer(
                offer_id=item["id"],
                airline=carrier.get("name") or (item.get("owner") or {}).get("name", ""),
                airline_iata=carrier.get("iata_code") or "",
                flight_number=f"{carrier.get('iata_code') or ''}{first.get('marketing_carrier_flight_number', '')}",
                origin=_airport(sl["origin"]),
                destination=_airport(sl["destination"]),
                departure_time=first["departing_at"],
                arrival_time=last["arriving_at"],
                stops=len(segments) - 1,
                total_amount=float(item["total_amount"]),
                currency=item["total_currency"],
                cabin_class=((first.get("passengers") or [{}])[0].get("cabin_class") or "economy"),
                source=source,
            ))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            rejected.append(f"{oid}: {reason(exc)}")
    return valid, rejected


def parse_place_suggestions(raw: Any) -> AirportInfo | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        return None
    for place in data:
        candidates = [place] if place.get("type") == "airport" else list(place.get("airports") or [])
        for candidate in candidates:
            try:
                return AirportInfo(**_airport(candidate))
            except ValidationError:
                continue
    return None


def offer_request_body(origin: str, destination: str, departure_date: date, passengers: int, cabin: str) -> dict:
    return {"data": {
        "slices": [{"origin": origin, "destination": destination, "departure_date": departure_date.isoformat()}],
        "passengers": [{"type": "adult"} for _ in range(passengers)],
        "cabin_class": cabin,
    }}


class DuffelFlights:
    name = "duffel"

    def __init__(self, http: HttpJson, usage: UsageMeter, *, access_token: str) -> None:
        self._http = http
        self._usage = usage
        self._headers = {"Authorization": f"Bearer {access_token}", "Duffel-Version": DUFFEL_VERSION,
                         "Accept": "application/json"}
        self._airports: dict[str, AirportInfo] = {}

    async def find_airport(self, city: str) -> AirportInfo:
        key = slug(city)
        if key not in self._airports:
            self._usage.record(self.name)
            raw = await self._http.get(f"{DUFFEL_BASE_URL}/places/suggestions", params={"query": city},
                                       headers=self._headers)
            airport = parse_place_suggestions(raw)
            if airport is None:
                raise ProviderError(f"Duffel found no airport for '{city}'.")
            self._airports[key] = airport
        return self._airports[key]

    async def search_raw(self, origin: str, destination: str, departure_date: date, passengers: int,
                         cabin: str) -> Any:
        self._usage.record(self.name)
        return await self._http.post(
            f"{DUFFEL_BASE_URL}/air/offer_requests",
            params={"return_offers": "true", "supplier_timeout": "20000"},
            json=offer_request_body(origin, destination, departure_date, passengers, cabin),
            headers=self._headers,
        )

    async def search(self, origin: str, destination: str, departure_date: date, passengers: int,
                     cabin: str = "economy", *, direction: str = "outbound") -> tuple[list[FlightOffer], list[str]]:
        raw = await self.search_raw(origin, destination, departure_date, passengers, cabin)
        return parse_offers(raw, source="duffel-test")


def _h(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


# (departure HH:MM, duration minutes, price factor) — the first option is the cheapest.
_MOCK_SCHEDULE = {
    "outbound": [("08:00", 360, 1.00), ("11:30", 360, 1.12), ("06:00", 480, 1.25)],
    "return": [("16:00", 360, 1.00), ("09:00", 360, 1.10), ("19:30", 420, 1.20)],
}
# Further offers for the evaluation's stress option profile (``MockFlights.offer_count`` > 3). All are
# dearer than the three above, so the cheapest offer, which planning books, is unchanged.
_STRESS_SCHEDULE = {
    "outbound": [("07:00", 360, 1.30), ("09:30", 360, 1.35), ("13:00", 360, 1.40), ("15:00", 360, 1.45),
                 ("05:00", 480, 1.50)],
    "return": [("12:00", 360, 1.30), ("14:00", 360, 1.35), ("18:00", 360, 1.40), ("21:00", 360, 1.45),
               ("07:00", 360, 1.50)],
}


class MockFlights:
    """Synthetic Duffel-format offers; the cheapest outbound arrives at 14:00, return departs 16:00."""

    name = "mock_flights"

    def __init__(self, usage: UsageMeter, geocoder: Any = None, drift: Any = None) -> None:
        self._usage = usage
        self._geocoder = geocoder
        self.drift = drift                  # evaluation only (app.integrations.drift)
        self.offer_count = 3                # evaluation only: the stress option profile raises it (max 8)
        self._table = {slug(k): v for k, v in load_fixture("airports")["cities"].items()}
        self._synthetic: dict[str, AirportInfo] = {}
        self._looked_up: set[str] = set()

    async def find_airport(self, city: str) -> AirportInfo:
        key = slug(city)
        if key not in self._looked_up:   # counted like the live client, which caches lookups
            self._usage.record(self.name)
            self._looked_up.add(key)
        if key in self._table:
            return AirportInfo(**self._table[key])
        letters = "".join(c for c in city.upper() if c.isalpha()) or "XXX"
        lat = lon = None
        if self._geocoder is not None:
            loc = await self._geocoder.geocode(city)
            lat, lon = loc.latitude, loc.longitude
        airport = AirportInfo(iata=("X" + letters[:2]).ljust(3, "X"), name=f"{city} Airport", city=city,
                              latitude=lat, longitude=lon)
        self._synthetic[airport.iata] = airport
        return airport

    def build_raw(self, origin: AirportInfo, destination: AirportInfo, departure_date: date, passengers: int,
                  cabin: str, direction: str) -> dict:
        h = _h(origin.iata, destination.iata, departure_date.isoformat())
        per_person_usd = (220 + h % 480) * (2.2 if cabin == "business" else 1.0)
        carriers = [("ZZ", "Duffel Airways"), ("VY", "VoyageAir"), ("SK", "SkyMock")]
        offers = []
        key = direction if direction in _MOCK_SCHEDULE else "outbound"
        schedule = (_MOCK_SCHEDULE[key] + _STRESS_SCHEDULE[key])[: self.offer_count]
        for i, (hm, minutes, factor) in enumerate(schedule):
            dep = datetime.fromisoformat(f"{departure_date.isoformat()}T{hm}:00")
            arr = dep + timedelta(minutes=minutes)
            code, name = carriers[(h + i) % len(carriers)]
            place = lambda a: {"type": "airport", "iata_code": a.iata, "name": a.name, "city_name": a.city,  # noqa: E731
                               "latitude": a.latitude, "longitude": a.longitude}
            offer_id = f"off_mock_{h:08x}_{i}"
            amount = per_person_usd * factor * passengers
            if self.drift is not None:
                amount = self.drift.price(offer_id, amount)
            offers.append({
                "id": offer_id,
                "total_amount": f"{amount:.2f}",
                "total_currency": "USD",
                "owner": {"name": name, "iata_code": code},
                "slices": [{
                    "origin": place(origin), "destination": place(destination),
                    "segments": [{
                        "departing_at": dep.isoformat(), "arriving_at": arr.isoformat(),
                        "marketing_carrier": {"name": name, "iata_code": code},
                        "marketing_carrier_flight_number": str(100 + (h + i * 37) % 800),
                        "passengers": [{"cabin_class": cabin}],
                    }],
                }],
            })
        if self.drift is not None:
            offers = self.drift.filter(offers, lambda o: o["id"])
        return {"data": {"id": f"orq_mock_{h:08x}", "offers": offers}}

    async def search(self, origin: str, destination: str, departure_date: date, passengers: int,
                     cabin: str = "economy", *, direction: str = "outbound") -> tuple[list[FlightOffer], list[str]]:
        self._usage.record(self.name)
        o, d = await self._resolve(origin), await self._resolve(destination)
        return parse_offers(self.build_raw(o, d, departure_date, passengers, cabin, direction), source="mock")

    async def _resolve(self, iata: str) -> AirportInfo:
        for info in self._table.values():
            if info["iata"] == iata:
                return AirportInfo(**info)
        return self._synthetic.get(iata) or AirportInfo(iata=iata, name=f"{iata} Airport")
