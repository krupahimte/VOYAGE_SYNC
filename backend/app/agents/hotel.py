"""
Hotel Agent — listings from Geoapify (or mock places) + a quote from our own
mock booking provider. Booking happens only after Risk Gate approval (Phase 6).

Hotels in ``exclude_hotel_ids`` (e.g. overbooked) are skipped. During a re-plan
the replacement closest to the previous hotel is chosen, so transfers and day
plans change as little as possible.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.agents.common import currency_of, locate, stable_hash, travelers_of
from app.agents.validation import finish
from app.core.models import NodeType, TaskNode
from app.integrations.http import ProviderError
from app.integrations.routing import haversine_km

ACCOMMODATION_CATEGORIES = {
    "hotel": ["accommodation.hotel"],
    "hostel": ["accommodation.hostel"],
    "apartment": ["accommodation.apartment"],
    "flexible": ["accommodation.hotel", "accommodation.apartment"],
}


class HotelAgent(BaseAgent):
    def __init__(self, providers: Any) -> None:
        self.p = providers

    async def run(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any]) -> AgentResult:
        ri = resolved_inputs
        city = ri.get("city", "")
        style = ri.get("travel_style", "balanced")
        currency = currency_of(ri)
        rooms = math.ceil(travelers_of(ri) / 2)
        try:
            check_in = date.fromisoformat(str(ri.get("check_in"))[:10])
            check_out = date.fromisoformat(str(ri.get("check_out"))[:10])
        except ValueError:
            return AgentResult.fail("invalid check_in/check_out", retryable=False)

        categories = ACCOMMODATION_CATEGORIES.get(ri.get("accommodation", "hotel"), ["accommodation.hotel"])
        try:
            loc = await locate(city, self.p)
            places, rejected = await self.p.places.search(categories, loc.latitude, loc.longitude, city=city)
            if not places and categories != ["accommodation.hotel"]:
                places, more = await self.p.places.search(["accommodation.hotel"], loc.latitude, loc.longitude,
                                                          city=city)
                rejected += more
            if not places:
                return AgentResult.fail(f"no accommodation found in {city} ({len(rejected)} rejected)",
                                        retryable=False)
            excluded = set(ri.get("exclude_hotel_ids") or [])
            places = sorted((pl for pl in places if pl.place_id not in excluded), key=lambda pl: pl.place_id)
            if not places:
                return AgentResult.fail(f"no available accommodation in {city} after exclusions", retryable=False)
            previous = frozen_context.get("previous_output") or {}
            if previous.get("latitude") is not None:
                anchor = (previous["latitude"], previous["longitude"])
                hotel = min(places, key=lambda pl: (haversine_km((pl.latitude, pl.longitude), anchor), pl.place_id))
            else:
                hotel = places[stable_hash(city, style) % len(places)]
            quote = await self.p.booking.quote(hotel.place_id, check_in, check_out, rooms, style)
            cost = await self.p.fx.convert(quote.total_usd, "USD", currency)
            per_night = await self.p.fx.convert(quote.nightly_usd, "USD", currency)
        except ProviderError as exc:
            return AgentResult.fail(f"hotel search failed: {exc}")

        ci = check_in.isoformat()
        return finish(NodeType.STAY, {
            "hotel_id": hotel.place_id,
            "hotel_name": hotel.name,
            "city": city,
            "address": hotel.address,
            "latitude": hotel.latitude,
            "longitude": hotel.longitude,
            "check_in": ci,
            "check_out": check_out.isoformat(),
            "checkin_time": f"{ci}T15:00:00",
            "checkin_cutoff": f"{ci}T23:59:00",
            "checkout_time": f"{check_out.isoformat()}T11:00:00",
            "nights": quote.nights,
            "rooms": rooms,
            "room_type": "suite" if style == "luxury" else "double",
            "price_per_night": per_night,
            "cost": cost,
            "currency": currency,
            "offer_id": quote.quote_id,
            "refundable": quote.refundable,
            "status": "offered",
            "source": hotel.source,
            "booking_source": quote.source,
            "candidates": len(places),
            "rejected": len(rejected),
        })
