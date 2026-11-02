"""
Flight Agent — fare search via Duffel (test mode) or the mock flight provider.

Picks the cheapest valid offer (converted to the trip currency). Offers that
fail parsing or the agent's own checks (wrong date, wrong airports, excluded
after a cancellation) are rejected and counted, never written to the graph.

During a re-plan (frozen context) the agent instead prefers the offer that
disturbs the rest of the plan least: it must keep the frozen hotel's check-in
contract and should be closest in time to the previous flight (arrival for
outbound, departure for return); price breaks ties.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, NamedTuple

from app.agents.base import AgentResult, BaseAgent, distinct
from app.agents.common import currency_of, travelers_of
from app.agents.validation import finish
from app.core.contracts import check_edge
from app.core.models import NodeType, TaskNode
from app.integrations.http import ProviderError


class _Search(NamedTuple):
    day: date
    origin: Any
    destination: Any
    pairs: list[tuple[float, Any]]      # (price in the trip currency, offer), in search order
    rejected: list[str]


class FlightAgent(BaseAgent):
    def __init__(self, providers: Any) -> None:
        self.p = providers

    async def run(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any]) -> AgentResult:
        found = await self._search(resolved_inputs)
        if isinstance(found, AgentResult):
            return found
        direction = resolved_inputs.get("direction", "outbound")
        cost, best = min(found.pairs, key=self._ranker(direction, frozen_context))
        return self._output(resolved_inputs, found, cost, best, frozen_context)

    async def candidates(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any],
                         k: int) -> list[AgentResult]:
        """The valid offers in the order ``run`` ranks them (a stable sort, so option 0 is its choice)."""
        found = await self._search(resolved_inputs)
        if isinstance(found, AgentResult):
            return [found]
        direction = resolved_inputs.get("direction", "outbound")
        ranked = sorted(found.pairs, key=self._ranker(direction, frozen_context))
        return distinct([self._output(resolved_inputs, found, cost, offer, frozen_context) for cost, offer in ranked], k)

    async def _search(self, ri: dict[str, Any]) -> "_Search | AgentResult":
        """Valid offers priced in the trip currency, or the failure ``run`` reports."""
        direction = ri.get("direction", "outbound")
        currency = currency_of(ri)
        travelers = travelers_of(ri)
        cabin = ri.get("cabin", "economy")
        try:
            day = date.fromisoformat(str(ri.get("departure_date"))[:10])
        except ValueError:
            return AgentResult.fail(f"invalid departure_date {ri.get('departure_date')!r}", retryable=False)

        try:
            origin = await self.p.flights.find_airport(ri["origin"])
            destination = await self.p.flights.find_airport(ri["destination"])
            offers, rejected = await self.p.flights.search(origin.iata, destination.iata, day, travelers, cabin,
                                                           direction=direction)
            excluded = set(ri.get("exclude_offer_ids") or [])
            valid, priced = [], []
            for offer in offers:
                if offer.offer_id in excluded:
                    rejected.append(f"{offer.offer_id}: excluded (cancelled)")
                elif offer.departure_time.date() != day:
                    rejected.append(f"{offer.offer_id}: departs {offer.departure_time.date()} not {day}")
                elif (offer.origin.iata, offer.destination.iata) != (origin.iata, destination.iata):
                    rejected.append(f"{offer.offer_id}: route {offer.origin.iata}-{offer.destination.iata}")
                else:
                    valid.append(offer)
                    priced.append(await self.p.fx.convert(offer.total_amount, offer.currency, currency))
        except KeyError as exc:
            return AgentResult.fail(f"missing input {exc}", retryable=False)
        except ProviderError as exc:
            return AgentResult.fail(f"flight search failed: {exc}")

        if not valid:
            return AgentResult.fail(f"no valid flight offers ({len(rejected)} rejected)", retryable=False)
        return _Search(day, origin, destination, list(zip(priced, valid)), rejected)

    @staticmethod
    def _output(ri: dict[str, Any], found: "_Search", cost: float, best: Any,
                frozen_context: dict[str, Any]) -> AgentResult:
        direction = ri.get("direction", "outbound")
        currency = currency_of(ri)
        travelers = travelers_of(ri)
        day, origin, destination = found.day, found.origin, found.destination
        airport = lambda a: {"iata": a.iata, "name": a.name, "city": a.city,  # noqa: E731
                             "latitude": a.latitude, "longitude": a.longitude}
        return finish(NodeType.FLIGHT, {
            "direction": direction,
            "flight_number": best.flight_number,
            "airline": best.airline,
            "origin": ri["origin"],
            "destination": ri["destination"],
            "origin_iata": best.origin.iata,
            "destination_iata": best.destination.iata,
            "departure_airport": airport(best.origin if best.origin.latitude is not None else origin),
            "arrival_airport": airport(best.destination if best.destination.latitude is not None else destination),
            "departure_date": day.isoformat(),
            "departure_time": best.departure_time.isoformat(),
            "arrival_time": best.arrival_time.isoformat(),
            "stops": best.stops,
            "cabin_class": best.cabin_class,
            "passengers": travelers,
            "price": cost,
            "cost": cost,
            "currency": currency,
            "price_original": {"amount": best.total_amount, "currency": best.currency},
            "offer_id": best.offer_id,
            "status": "offered",
            "source": best.source,
            "offers_considered": len(found.pairs),
            "offers_rejected": len(found.rejected),
            "selection": "least-change" if frozen_context.get("previous_output") else "cheapest",
        })

    @staticmethod
    def _ranker(direction: str, frozen_context: dict[str, Any]):
        """Sort key: cheapest normally; least disruption when re-planning with frozen context."""
        previous = frozen_context.get("previous_output") or {}
        anchor_key = "departure_time" if direction == "return" else "arrival_time"
        anchor = previous.get(anchor_key)
        if not anchor:
            return lambda pair: (pair[0], pair[1].departure_time)
        anchor_dt = datetime.fromisoformat(anchor)
        stays = [o for o in (frozen_context.get("frozen_neighbours") or {}).values()
                 if isinstance(o, dict) and "checkin_cutoff" in o]

        def key(pair):
            cost, offer = pair
            breaks_hotel = direction != "return" and any(
                not check_edge("FLIGHT_BEFORE_CHECKIN", {"buffer_minutes": 90}, "f",
                               {"arrival_time": offer.arrival_time.isoformat()}, stay).holds
                for stay in stays)
            moved = abs((getattr(offer, anchor_key) - anchor_dt).total_seconds())
            return (breaks_hotel, moved, cost)

        return key
