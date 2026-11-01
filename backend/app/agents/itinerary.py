"""
Itinerary Agent — day plans and airport transfers.

  DayPlanAgent:  places near the hotel (Geoapify or mock) slotted into the day,
                 respecting the arrival time and the weather; the LLM writes
                 one-line descriptions (validated, with a template fallback).
                 When re-planning with frozen context it keeps every previous
                 activity that is still feasible and only refills the rest.
  TransferAgent: driving time airport ⇄ hotel from OSRM (or mock routing);
                 pickup derived from the flight time.

The slot times match the mock agents, so the contract timeline (and the
Santorini delay scenario) is the same in mock and live data.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.agents.common import currency_of, locate, parents_of_type, shift_iso, stable_hash, travelers_of
from app.agents.mocks import _FIXED, _INDOOR_ALTERNATIVE, _INTEREST_ACTIVITIES, _SLOTS
from app.agents.validation import finish
from app.core.models import NodeType, TaskNode
from app.integrations.http import ProviderError
from app.integrations.places import Place
from app.llm.client import LLMError, extract_json_object
from app.llm.prompts import DAY_PLAN_SYSTEM_PROMPT, build_day_plan_prompt

# interest → (morning category, afternoon category)
INTEREST_CATEGORIES: dict[str, tuple[str, str]] = {
    "culture": ("tourism.sights", "entertainment.museum"),
    "food": ("commercial", "catering.cafe"),
    "nature": ("natural", "leisure.park"),
    "adventure": ("activity", "natural"),
    "shopping": ("commercial", "commercial"),
    "nightlife": ("catering.cafe", "catering.bar"),
    "relaxation": ("beach", "leisure.spa"),
}
MEAL_CATEGORY = "catering.restaurant"
SUNSET_CATEGORY = "tourism.sights"
INDOOR_CATEGORY = "entertainment.museum"

# USD per person by category (estimates for the budget; not provider prices)
CATEGORY_COST_USD = {
    "tourism.sights": 10, "entertainment.museum": 20, "natural": 0, "leisure.park": 0, "beach": 15,
    "leisure.spa": 80, "activity": 70, "commercial": 0, "catering.cafe": 15, "catering.bar": 30,
}
MEAL_COST_USD = {"lunch": 25, "dinner": 50}
TRANSFER_BASE_USD, TRANSFER_PER_KM_USD = 15.0, 1.8


def _round_up(minutes: float, step: int = 5) -> int:
    return max(10, int(math.ceil(minutes / step) * step))


# ── Transfer ──────────────────────────────────────────────────────────────────

class TransferAgent(BaseAgent):
    def __init__(self, providers: Any) -> None:
        self.p = providers

    async def run(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any]) -> AgentResult:
        ri = resolved_inputs
        direction = ri.get("direction", "arrival")
        city = ri.get("city", "")
        currency = currency_of(ri)
        flights = parents_of_type(ri, NodeType.FLIGHT)
        stays = parents_of_type(ri, NodeType.STAY)
        if not flights:
            return AgentResult.fail("transfer needs its flight as a parent", retryable=False)
        flight = flights[0][1]
        stay = stays[0][1] if stays else {}

        try:
            if stay.get("latitude") is not None:
                hotel = (stay["latitude"], stay["longitude"])
            else:
                loc = await locate(city, self.p)
                hotel = (loc.latitude, loc.longitude)
            key = "arrival_airport" if direction == "arrival" else "departure_airport"
            airport_info = flight.get(key) or {}
            if airport_info.get("latitude") is None:
                found = await self.p.flights.find_airport(city)
                airport_info = {"name": found.name, "latitude": found.latitude, "longitude": found.longitude}
            if airport_info.get("latitude") is None:
                return AgentResult.fail("airport location unknown", retryable=False)
            airport = (airport_info["latitude"], airport_info["longitude"])
            route = await self.p.routing.route(airport, hotel) if direction == "arrival" \
                else await self.p.routing.route(hotel, airport)
            vehicles = math.ceil(travelers_of(ri) / 4)
            cost = await self.p.fx.convert((TRANSFER_BASE_USD + TRANSFER_PER_KM_USD * route.distance_km) * vehicles,
                                           "USD", currency)
        except ProviderError as exc:
            return AgentResult.fail(f"transfer routing failed: {exc}")

        minutes = _round_up(route.duration_minutes)
        hotel_name = stay.get("hotel_name") or f"Hotel in {city}"
        airport_name = airport_info.get("name") or f"{city} Airport"
        if direction == "departure":
            departure = flight.get("departure_time")
            if not departure:
                return AgentResult.fail("return flight has no departure_time", retryable=False)
            pickup = shift_iso(departure, -(int(ri.get("airport_buffer_minutes", 180)) + minutes))
            frm, to = hotel_name, airport_name
        else:
            arrival = flight.get("arrival_time")
            if not arrival:
                return AgentResult.fail("flight has no arrival_time", retryable=False)
            pickup = shift_iso(arrival, int(ri.get("pickup_offset_minutes", 30)))
            frm, to = airport_name, hotel_name

        return finish(NodeType.TRANSFER, {
            "direction": direction,
            "hotel_id": stay.get("hotel_id") or "unknown",
            "pickup_location": frm,
            "dropoff_location": to,
            "pickup_time": pickup,
            "dropoff_time": shift_iso(pickup, minutes),
            "duration_minutes": minutes,
            "distance_km": round(route.distance_km, 1),
            "vehicle_type": "private_car" if travelers_of(ri) <= 3 else "private_van",
            "cost": cost,
            "currency": currency,
            "status": "planned",
            "source": route.source,
        })


# ── Day plan ──────────────────────────────────────────────────────────────────

class DayPlanAgent(BaseAgent):
    def __init__(self, providers: Any) -> None:
        self.p = providers

    async def run(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any]) -> AgentResult:
        ri = resolved_inputs
        day_date = str(ri.get("date", ""))[:10]
        city = ri.get("city", "")
        day = int(ri.get("day", 1) or 1)
        travelers = travelers_of(ri)
        currency = currency_of(ri)
        interests = [i for i in (ri.get("interests") or []) if i in INTEREST_CATEGORIES]
        interest = interests[(day - 1) % len(interests)] if interests else "culture"
        pace = ri.get("pace", "balanced")

        stays = parents_of_type(ri, NodeType.STAY)
        stay = stays[0][1] if stays else {}
        weathers = parents_of_type(ri, NodeType.WEATHER)
        weather = weathers[0][1] if weathers else {}
        stormy = bool(weather.get("is_storm"))

        try:
            earliest = datetime.fromisoformat(f"{day_date}T09:00:00")
        except ValueError:
            return AgentResult.fail(f"invalid date {day_date!r}", retryable=False)
        for _, transfer in parents_of_type(ri, NodeType.TRANSFER):
            if transfer.get("direction") == "arrival" and transfer.get("dropoff_time"):
                dropoff = datetime.fromisoformat(transfer["dropoff_time"].replace("Z", "+00:00")).replace(tzinfo=None)
                earliest = max(earliest, dropoff + timedelta(minutes=60))

        morning_cat, afternoon_cat = INTEREST_CATEGORIES[interest]
        categories = sorted({morning_cat, afternoon_cat, MEAL_CATEGORY, SUNSET_CATEGORY, INDOOR_CATEGORY})
        try:
            if stay.get("latitude") is not None:
                center = (stay["latitude"], stay["longitude"])
            else:
                loc = await locate(city, self.p)
                center = (loc.latitude, loc.longitude)
            places, _ = await self.p.places.search(categories, center[0], center[1], city=city, limit=60)
        except ProviderError as exc:
            return AgentResult.fail(f"places search failed: {exc}")

        picker = _Picker(places, seed=stable_hash(city, day_date))
        previous = frozen_context.get("previous_output") or {}
        kept_by_slot = {a.get("slot"): a for a in (previous.get("activities") or [])
                        if previous.get("date") == day_date and isinstance(a, dict)}
        for a in kept_by_slot.values():
            picker.reserve_name(a.get("name", ""))
        slot_category = {"morning": morning_cat, "afternoon": afternoon_cat, "lunch": MEAL_CATEGORY,
                         "dinner": MEAL_CATEGORY, "sunset": SUNSET_CATEGORY}

        planned: list[tuple[str, str, bool, float, datetime, int, str]] = []
        if earliest.date().isoformat() == day_date:
            for slot, hm, minutes in _SLOTS:
                if pace == "relaxed" and slot == "afternoon":
                    continue
                start = datetime.fromisoformat(f"{day_date}T{hm}:00")
                if start < earliest:
                    continue
                kept = kept_by_slot.get(slot)
                if kept and not (stormy and kept.get("outdoor")) and kept.get("start") == start.isoformat():
                    planned.append((slot, kept["name"], bool(kept["outdoor"]), None, start, minutes, "kept"))
                    continue
                name, outdoor, usd, source = self._choose(slot, slot_category[slot], interest, picker, day)
                if stormy and outdoor:
                    name, outdoor, usd, source = self._indoor(slot, picker)
                planned.append((slot, name, outdoor, usd, start, minutes, source))
            if not planned:
                start = earliest + timedelta(minutes=(-earliest.minute) % 15)
                if start.date().isoformat() == day_date:
                    place = picker.take(MEAL_CATEGORY)
                    name = f"Late dinner at {place.name}" if place else "Late dinner near the hotel"
                    planned.append(("dinner", name, False, 30, start, 75, place.source if place else "template"))

        activities, fresh = [], []
        for slot, name, outdoor, usd, start, minutes, source in planned:
            if usd is None:                                   # kept unchanged from the previous plan
                activities.append(dict(kept_by_slot[slot]))
                continue
            fresh.append(len(activities))
            activities.append({
                "slot": slot, "name": name, "time": start.strftime("%H:%M"),
                "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat(),
                "duration_minutes": minutes, "outdoor": outdoor, "source": source,
                "cost": await self.p.fx.convert(usd * travelers, "USD", currency),
            })

        theme = ri.get("theme") or f"{interest.title()} in {city}"
        new_items = [activities[i] for i in fresh]
        descriptions, desc_source = await self._describe(day, day_date, city, theme, new_items)
        for a in new_items:
            a["description"] = descriptions.get(a["name"], "")
        if not new_items and activities:                      # nothing new: no LLM call needed
            desc_source = previous.get("descriptions_source", "template")

        return finish(NodeType.DAY_PLAN, {
            "day": day,
            "date": day_date,
            "city": city,
            "theme": theme,
            "hotel_id": stay.get("hotel_id") or ri.get("hotel_id"),
            "activities": activities,
            "start_time": activities[0]["start"] if activities else None,
            "end_time": activities[-1]["end"] if activities else None,
            "cost": round(sum(a["cost"] for a in activities), 2),
            "currency": currency,
            "weather_condition": weather.get("condition"),
            "available_time_hours": round(sum(a["duration_minutes"] for a in activities) / 60, 2),
            "descriptions_source": desc_source,
        })

    @staticmethod
    def _choose(slot: str, category: str, interest: str, picker: "_Picker", day: int):
        place = picker.take(category)
        if slot in ("lunch", "dinner"):
            usd = MEAL_COST_USD[slot]
            if place:
                return f"{slot.title()} at {place.name}", False, usd, place.source
            name, outdoor, usd = _FIXED[slot]
            return name, outdoor, usd, "template"
        if slot == "sunset":
            if place:
                return f"Sunset at {place.name}", True, 10, place.source
            name, outdoor, usd = _FIXED["sunset"]
            return name, outdoor, usd, "template"
        if place:
            return place.name, place.outdoor, CATEGORY_COST_USD.get(category, 20), place.source
        name, outdoor, usd = _INTEREST_ACTIVITIES[interest][slot]
        return name, outdoor, usd, "template"

    @staticmethod
    def _indoor(slot: str, picker: "_Picker"):
        place = picker.take(INDOOR_CATEGORY)
        if place and slot != "sunset":
            return place.name, False, CATEGORY_COST_USD[INDOOR_CATEGORY], place.source
        name, outdoor, usd = _INDOOR_ALTERNATIVE.get(slot, ("Indoor break", False, 0))
        return name, outdoor, usd, "template"

    async def _describe(self, day: int, day_date: str, city: str, theme: str,
                        activities: list[dict]) -> tuple[dict[str, str], str]:
        """One LLM call per day; any invalid reply falls back to template text."""
        template = {a["name"]: f"{a['time']} — {a['name']}." for a in activities}
        if not activities:
            return template, "template"
        request = {"day": day, "date": day_date, "city": city, "theme": theme,
                   "activities": [{k: a[k] for k in ("slot", "name", "time", "outdoor")} for a in activities]}
        try:
            reply = await self.p.llm.complete([
                {"role": "system", "content": DAY_PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": build_day_plan_prompt(request)},
            ])
            parsed = json.loads(extract_json_object(reply.text)).get("descriptions")
        except (LLMError, ValueError, AttributeError):
            return template, "template"
        if not isinstance(parsed, dict):
            return template, "template"
        names = set(template)
        clean = {k: v.strip() for k, v in parsed.items()
                 if k in names and isinstance(v, str) and 0 < len(v.strip()) <= 240}
        if not clean:
            return template, "template"
        return template | clean, "llm"


class _Picker:
    """Deterministic, non-repeating choice of places per category for one day."""

    def __init__(self, places: list[Place], seed: int) -> None:
        self._by_cat: dict[str, list[Place]] = {}
        for place in sorted(places, key=lambda p: p.place_id):
            for cat in place.categories:
                self._by_cat.setdefault(cat, []).append(place)
        self._seed = seed
        self._used: set[str] = set()
        self._reserved: set[str] = set()

    def reserve_name(self, text: str) -> None:
        """Avoid re-picking a place already used by a kept activity (matched by name)."""
        if text:
            self._reserved.add(text)

    def take(self, category: str) -> Place | None:
        options = [p for p in self._by_cat.get(category, []) if p.place_id not in self._used
                   and not any(p.name in r for r in self._reserved)]
        if not options:
            return None
        place = options[self._seed % len(options)]
        self._used.add(place.place_id)
        return place
