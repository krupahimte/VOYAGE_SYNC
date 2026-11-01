"""
Deterministic mock agents for all six VoyageSync node types.

Rules:
  - No external APIs, no randomness: the same inputs always give the same output.
  - Inputs are inspected but never executed as code.
  - Outputs carry the fields the edge contracts read (arrival_time,
    checkin_cutoff, pickup/dropoff times, start/end times, cost, …).
  - Parent outputs are read from ``resolved_inputs["_parents"]`` when present
    (see Scheduler._resolve_inputs); flat keys still work for direct calls.
  - All times are naive local ISO-8601 strings ("2026-06-14T14:00:00").
  - A special ``FailingAgent`` is provided for testing failure/retry paths.
  - A ``SlowAgent`` is provided for concurrency-limit testing.

Slide mapping: Flight Agent = FLIGHT, Hotel Agent = STAY,
Itinerary Agent = DAY_PLAN + TRANSFER, Budget Agent = BUDGET,
WEATHER is a data source for the Itinerary Agent and the Disruption Monitor.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from datetime import datetime, timedelta
from typing import Any

from app.agents.base import AgentResult, BaseAgent, AgentRegistry
from app.agents.fx import convert_from_usd
from app.core.models import NodeType, TaskNode


# ── Helpers ───────────────────────────────────────────────────────────────────

def stable_hash(*parts: Any) -> int:
    """Deterministic across processes (unlike built-in hash())."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _fmt(dt: datetime, like: str = "") -> str:
    text = dt.isoformat()
    if like.endswith("Z") and text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text


def _shift(iso: str, minutes: int) -> str:
    """Add minutes to an ISO datetime string, keeping its style."""
    if minutes == 0:
        return iso
    return _fmt(_parse(iso) + timedelta(minutes=minutes), like=iso)


def _parents_of_type(inputs: dict[str, Any], node_type: NodeType) -> list[tuple[str, dict[str, Any]]]:
    parents = inputs.get("_parents") or {}
    return [
        (pid, p.get("output") or {})
        for pid, p in sorted(parents.items())
        if p.get("type") == node_type.value
    ]


def _travelers(inputs: dict[str, Any]) -> int:
    try:
        return max(1, int(inputs.get("travelers", 1)))
    except (TypeError, ValueError):
        return 1


def _currency(inputs: dict[str, Any]) -> str:
    return str(inputs.get("currency", "USD")).upper()


# ── Flight ────────────────────────────────────────────────────────────────────

_AIRLINES = [("DA", "Duffel Airways"), ("VY", "VoyageAir"), ("SK", "SkyMock"), ("AM", "Aegean Mock")]


class MockFlightAgent(BaseAgent):
    """Flight offer: outbound departs 08:00 → arrives 14:00; return departs 16:00 → arrives 22:00."""

    async def run(self, node, resolved_inputs, frozen_context) -> AgentResult:
        origin = resolved_inputs.get("origin", "LHR")
        destination = resolved_inputs.get("destination", "NRT")
        departure_date = str(resolved_inputs.get("departure_date", "2025-06-01"))[:10]
        direction = resolved_inputs.get("direction", "outbound")
        travelers = _travelers(resolved_inputs)
        currency = _currency(resolved_inputs)

        dep_hm, arr_hm = ("16:00", "22:00") if direction == "return" else ("08:00", "14:00")
        h = stable_hash(origin, destination, departure_date)
        code, airline = _AIRLINES[h % len(_AIRLINES)]
        business = resolved_inputs.get("cabin") == "business"
        per_person_usd = (220 + h % 480) * (2.2 if business else 1.0)
        cost = convert_from_usd(per_person_usd * travelers, currency)

        return AgentResult.ok({
            "direction": direction,
            "flight_number": f"{code}{100 + h % 800}",
            "airline": airline,
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "departure_time": f"{departure_date}T{dep_hm}:00",
            "arrival_time": f"{departure_date}T{arr_hm}:00",
            "cabin_class": "business" if business else "economy",
            "passengers": travelers,
            "price": cost,
            "cost": cost,
            "currency": currency,
            "offer_id": f"off_{h:08x}",
            "status": "offered",
        })


# ── Stay ──────────────────────────────────────────────────────────────────────

_NIGHTLY_USD = {"budget": 70, "balanced": 130, "comfort": 210, "luxury": 360}
_HOTEL_SUFFIX = ["Harbour Hotel", "Central Inn", "Vista Suites", "Old Town Residence"]


class MockStayAgent(BaseAgent):
    """Hotel offer with a check-in cutoff at 23:59 on the check-in date."""

    async def run(self, node, resolved_inputs, frozen_context) -> AgentResult:
        city = resolved_inputs.get("city", "Tokyo")
        check_in = str(resolved_inputs.get("check_in", "2025-06-01"))[:10]
        check_out = str(resolved_inputs.get("check_out", "2025-06-08"))[:10]
        style = resolved_inputs.get("travel_style", "balanced")
        travelers = _travelers(resolved_inputs)
        currency = _currency(resolved_inputs)

        try:
            nights = max(1, (datetime.fromisoformat(check_out) - datetime.fromisoformat(check_in)).days)
        except ValueError:
            nights = 1
        rooms = math.ceil(travelers / 2)
        h = stable_hash(city, style)
        per_night = convert_from_usd(_NIGHTLY_USD.get(style, 130) * rooms, currency)
        cost = round(per_night * nights, 2)

        return AgentResult.ok({
            "hotel_id": f"htl_{h % 100000:05d}",
            "hotel_name": f"{city} {_HOTEL_SUFFIX[h % len(_HOTEL_SUFFIX)]}",
            "city": city,
            "address": f"{10 + h % 90} Main Street, {city}",
            "check_in": check_in,
            "check_out": check_out,
            "checkin_time": f"{check_in}T15:00:00",
            "checkin_cutoff": f"{check_in}T23:59:00",
            "checkout_time": f"{check_out}T11:00:00",
            "nights": nights,
            "rooms": rooms,
            "room_type": "suite" if style == "luxury" else "double",
            "price_per_night": per_night,
            "cost": cost,
            "currency": currency,
            "offer_id": f"hof_{h:08x}",
            "status": "offered",
        })


# ── Transfer ──────────────────────────────────────────────────────────────────

_TRANSFER_MINUTES = 45


class MockTransferAgent(BaseAgent):
    """
    Airport transfer.
      arrival:   pickup = flight arrival + pickup_offset_minutes
      departure: pickup = flight departure − airport_buffer_minutes − drive time
    """

    async def run(self, node, resolved_inputs, frozen_context) -> AgentResult:
        direction = resolved_inputs.get("direction", "arrival")
        city = resolved_inputs.get("city", "the city")
        travelers = _travelers(resolved_inputs)
        currency = _currency(resolved_inputs)

        flights = _parents_of_type(resolved_inputs, NodeType.FLIGHT)
        flight = flights[0][1] if flights else {}
        stays = _parents_of_type(resolved_inputs, NodeType.STAY)
        hotel_id = stays[0][1].get("hotel_id") if stays else None

        if direction == "departure":
            departure = flight.get("departure_time") or resolved_inputs.get("departure_time")
            if not departure:
                return AgentResult.fail("departure transfer needs the return flight's departure_time",
                                        retryable=False)
            buffer = int(resolved_inputs.get("airport_buffer_minutes", 180))
            pickup = _shift(departure, -(buffer + _TRANSFER_MINUTES))
            pickup_location, dropoff_location = f"Hotel in {city}", f"{city} Airport"
        else:
            arrival = flight.get("arrival_time") or resolved_inputs.get("arrival_time", "2025-06-01T21:15:00")
            pickup = _shift(arrival, int(resolved_inputs.get("pickup_offset_minutes", 0)))
            pickup_location, dropoff_location = f"{city} Airport", f"Hotel in {city}"

        vehicles = math.ceil(travelers / 4)
        cost = convert_from_usd(40 * vehicles, currency)
        return AgentResult.ok({
            "direction": direction,
            "hotel_id": hotel_id,
            "pickup_location": pickup_location,
            "dropoff_location": dropoff_location,
            "pickup_time": pickup,
            "dropoff_time": _shift(pickup, _TRANSFER_MINUTES) if pickup else None,
            "duration_minutes": _TRANSFER_MINUTES,
            "vehicle_type": "private_car" if travelers <= 3 else "private_van",
            "cost": cost,
            "currency": currency,
            "status": "planned",
        })


# ── Day plan ──────────────────────────────────────────────────────────────────

# (slot, start HH:MM, minutes)
_SLOTS = [("morning", "09:30", 150), ("lunch", "12:30", 60), ("afternoon", "14:30", 150),
          ("sunset", "18:00", 90), ("dinner", "20:00", 90)]

# interest → {slot: (name, outdoor, usd per person)}
_INTEREST_ACTIVITIES: dict[str, dict[str, tuple[str, bool, float]]] = {
    "culture":    {"morning": ("Old town & museum walk", True, 20), "afternoon": ("Historic landmarks tour", True, 30)},
    "food":       {"morning": ("Local market food tour", True, 45), "afternoon": ("Cooking class", False, 60)},
    "nature":     {"morning": ("Coastal hike", True, 0), "afternoon": ("Scenic boat trip", True, 55)},
    "adventure":  {"morning": ("Guided kayaking", True, 70), "afternoon": ("Cliff-top trail", True, 0)},
    "shopping":   {"morning": ("Artisan shops & boutiques", False, 0), "afternoon": ("Design district", False, 0)},
    "nightlife":  {"morning": ("Late breakfast & café hop", False, 20), "afternoon": ("Rooftop bar evening prep", False, 25)},
    "relaxation": {"morning": ("Beach morning", True, 15), "afternoon": ("Spa afternoon", False, 80)},
}
_FIXED: dict[str, tuple[str, bool, float]] = {
    "lunch": ("Lunch at a local taverna", False, 25),
    "sunset": ("Sunset viewpoint", True, 10),
    "dinner": ("Dinner in the old town", False, 50),
}
_INDOOR_ALTERNATIVE: dict[str, tuple[str, bool, float]] = {
    "morning": ("Museum visit", False, 20),
    "afternoon": ("Indoor workshop", False, 40),
    "sunset": ("Wine tasting", False, 35),
}


class MockDayPlanAgent(BaseAgent):
    """
    One day's activities, chosen from fixed slots.

    - Starts no earlier than arrival-transfer drop-off + 60 min (arrival day).
    - On a storm forecast, outdoor items are swapped for indoor alternatives.
    - Is based at the parent hotel (hotel_id).
    """

    async def run(self, node, resolved_inputs, frozen_context) -> AgentResult:
        day_date = str(resolved_inputs.get("date", "2025-06-02"))[:10]
        city = resolved_inputs.get("city", "Tokyo")
        day = int(resolved_inputs.get("day", 1) or 1)
        travelers = _travelers(resolved_inputs)
        currency = _currency(resolved_inputs)
        interests = [i for i in (resolved_inputs.get("interests") or []) if i in _INTEREST_ACTIVITIES]
        interest = interests[(day - 1) % len(interests)] if interests else "culture"
        pace = resolved_inputs.get("pace", "balanced")

        stays = _parents_of_type(resolved_inputs, NodeType.STAY)
        stay = stays[0][1] if stays else {}
        weathers = _parents_of_type(resolved_inputs, NodeType.WEATHER)
        weather = weathers[0][1] if weathers else {}
        stormy = bool(weather.get("is_storm"))

        earliest = datetime.fromisoformat(f"{day_date}T09:00:00")
        for _, transfer in _parents_of_type(resolved_inputs, NodeType.TRANSFER):
            if transfer.get("direction") == "arrival" and transfer.get("dropoff_time"):
                dropoff = _parse(transfer["dropoff_time"]).replace(tzinfo=None)
                earliest = max(earliest, dropoff + timedelta(minutes=60))

        activities: list[dict[str, Any]] = []
        if earliest.date().isoformat() == day_date:
            for slot, hm, minutes in _SLOTS:
                if pace == "relaxed" and slot == "afternoon":
                    continue
                start = datetime.fromisoformat(f"{day_date}T{hm}:00")
                if start < earliest:
                    continue
                name, outdoor, usd = _INTEREST_ACTIVITIES[interest].get(slot) or _FIXED[slot]
                if stormy and outdoor:
                    name, outdoor, usd = _INDOOR_ALTERNATIVE[slot]
                activities.append(self._activity(slot, name, outdoor, usd, start, minutes, travelers, currency))
            if not activities:
                # Arrived too late for any slot: a short late dinner near the hotel.
                start = earliest + timedelta(minutes=(-earliest.minute) % 15)
                if start.date().isoformat() == day_date:
                    activities.append(self._activity("dinner", "Late dinner near the hotel", False, 30,
                                                     start, 75, travelers, currency))

        theme = resolved_inputs.get("theme") or f"{interest.title()} in {city}"
        return AgentResult.ok({
            "day": day,
            "date": day_date,
            "city": city,
            "theme": theme,
            "hotel_id": stay.get("hotel_id") or resolved_inputs.get("hotel_id"),
            "activities": activities,
            "start_time": activities[0]["start"] if activities else None,
            "end_time": activities[-1]["end"] if activities else None,
            "cost": round(sum(a["cost"] for a in activities), 2),
            "currency": currency,
            "weather_condition": weather.get("condition"),
            "available_time_hours": round(sum(a["duration_minutes"] for a in activities) / 60, 2),
        })

    @staticmethod
    def _activity(slot, name, outdoor, usd, start, minutes, travelers, currency) -> dict[str, Any]:
        return {
            "slot": slot,
            "name": name,
            "time": start.strftime("%H:%M"),
            "start": start.isoformat(),
            "end": (start + timedelta(minutes=minutes)).isoformat(),
            "duration_minutes": minutes,
            "outdoor": outdoor,
            "cost": convert_from_usd(usd * travelers, currency),
        }


# ── Weather ───────────────────────────────────────────────────────────────────

_CONDITIONS = ["sunny", "partly_cloudy", "sunny", "cloudy", "light_rain"]


class MockWeatherAgent(BaseAgent):
    """Deterministic forecast; the mock never forecasts storms (disruptions inject them)."""

    async def run(self, node, resolved_inputs, frozen_context) -> AgentResult:
        day_date = str(resolved_inputs.get("date", "2025-06-02"))[:10]
        city = resolved_inputs.get("city", "Tokyo")
        h = stable_hash(city, day_date)
        condition = _CONDITIONS[h % len(_CONDITIONS)]
        high = 20 + h % 12
        return AgentResult.ok({
            "day": resolved_inputs.get("day"),
            "date": day_date,
            "city": city,
            "condition": condition,
            "is_storm": False,
            "temperature_high_c": high,
            "temperature_low_c": high - 7,
            "precipitation_probability": 0.6 if condition == "light_rain" else 0.1,
            "wind_speed_kmh": 8 + h % 20,
            "source": "mock",
        })


# ── Budget ────────────────────────────────────────────────────────────────────

class MockBudgetAgent(BaseAgent):
    """
    Sums the ``cost`` of every parent into line items and checks the limit.
    Direct calls without parents may pass ``spent`` (or legacy ``spent_gbp``).
    """

    async def run(self, node, resolved_inputs, frozen_context) -> AgentResult:
        from app.core.contracts import evaluate_contract

        currency = _currency(resolved_inputs)
        limit = float(resolved_inputs.get("budget_limit", resolved_inputs.get("total_budget_gbp", 2500.0)))

        line_items: dict[str, float] = {}
        breakdown: dict[str, float] = {}
        for pid, parent in sorted((resolved_inputs.get("_parents") or {}).items()):
            cost = (parent.get("output") or {}).get("cost")
            if isinstance(cost, (int, float)):
                line_items[pid] = round(float(cost), 2)
                key = parent.get("type", "OTHER").lower()
                breakdown[key] = round(breakdown.get(key, 0.0) + float(cost), 2)

        if line_items:
            spent = round(sum(line_items.values()), 2)
        else:
            spent = float(resolved_inputs.get("spent", resolved_inputs.get("spent_gbp", 0.0)))

        check = evaluate_contract("BUDGET_WITHIN_LIMIT",
                                  {"total_cost": spent, "budget_limit": limit, "currency": currency})
        output: dict[str, Any] = {
            "budget_limit": limit,
            "total_cost": spent,
            "remaining": round(limit - spent, 2),
            "within_budget": spent <= limit,
            "currency": currency,
            "line_items": line_items,
            "breakdown": breakdown,
            "explanation": check.explanation,
        }
        if "total_budget_gbp" in resolved_inputs:   # legacy keys (Phase 2 callers)
            output.update(total_budget_gbp=limit, spent_gbp=spent, remaining_gbp=round(limit - spent, 2))
        return AgentResult.ok(output)


# ── Test-support agents ───────────────────────────────────────────────────────

class FailingAgent(BaseAgent):
    """
    Always fails. Used in tests to exercise failure/retry paths.

    Parameters:
        retryable:  Whether the failure should be retried.
        fail_count: Number of times to fail before succeeding (-1 = always fail).
    """

    def __init__(self, *, retryable: bool = True, fail_count: int = -1) -> None:
        self.retryable = retryable
        self.fail_count = fail_count
        self._attempts = 0

    async def run(
        self,
        node: TaskNode,
        resolved_inputs: dict[str, Any],
        frozen_context: dict[str, Any],
    ) -> AgentResult:
        self._attempts += 1
        if self.fail_count == -1 or self._attempts <= self.fail_count:
            return AgentResult.fail(
                f"Intentional failure (attempt {self._attempts})",
                retryable=self.retryable,
            )
        return AgentResult.ok({"recovered": True, "attempt": self._attempts})


class SlowAgent(BaseAgent):
    """
    Sleeps for a configurable duration before succeeding.
    Used to test concurrency behaviour without busy-waiting.
    """

    def __init__(self, delay_seconds: float = 0.05) -> None:
        self.delay_seconds = delay_seconds
        self.concurrent_peak = 0
        self._currently_running = 0

    async def run(
        self,
        node: TaskNode,
        resolved_inputs: dict[str, Any],
        frozen_context: dict[str, Any],
    ) -> AgentResult:
        self._currently_running += 1
        self.concurrent_peak = max(self.concurrent_peak, self._currently_running)
        try:
            await asyncio.sleep(self.delay_seconds)
            return AgentResult.ok({"node_id": node.id})
        finally:
            self._currently_running -= 1


# ── Default registry ──────────────────────────────────────────────────────────

def build_default_registry() -> AgentRegistry:
    """
    Build and return the default agent registry with all mock agents
    registered by NodeType.
    """
    registry = AgentRegistry()
    registry.register_by_type(NodeType.FLIGHT,   MockFlightAgent())
    registry.register_by_type(NodeType.STAY,     MockStayAgent())
    registry.register_by_type(NodeType.TRANSFER, MockTransferAgent())
    registry.register_by_type(NodeType.DAY_PLAN, MockDayPlanAgent())
    registry.register_by_type(NodeType.WEATHER,  MockWeatherAgent())
    registry.register_by_type(NodeType.BUDGET,   MockBudgetAgent())
    return registry
