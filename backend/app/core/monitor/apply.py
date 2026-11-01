"""
Apply a disruption's new facts to the graph.

Two kinds of effect:
  - "facts":      the target's output is updated in place (delay, storm, price
                  spike). The target is *disrupted*, not regenerated; its new
                  facts are pinned in ``inputs["facts"]`` so even a full replan
                  keeps them.
  - "invalidate": the target itself must be regenerated (cancellation,
                  overbooking). The unusable offer/hotel is added to the node's
                  exclusion list so the agent picks something else.

Deduplication: an event whose facts are already reflected in the target
(the same delay, an already-stormy day, a cancellation for an offer that has
since been replaced) is a duplicate and changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from app.core.contracts import STORM_CONDITIONS, check_edge
from app.core.graph import GraphStore, NodeNotFoundError
from app.core.models import NodeStatus
from app.core.monitor.events import TARGET_TYPES, DisruptionEvent, DisruptionType

Kind = Literal["facts", "invalidate", "duplicate", "invalid"]


@dataclass
class ApplyOutcome:
    kind: Kind
    target: str
    message: str
    broken_edges: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_effect(self) -> bool:
        return self.kind == "invalidate" or (self.kind == "facts" and bool(self.broken_edges))


def _shift(iso: str, minutes: float) -> str:
    return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()


def broken_out_edges(store: GraphStore, node_id: str) -> list[tuple[str, str]]:
    """Impact pre-check: outgoing contracts of *node_id* that no longer hold."""
    src = store.get_node(node_id)
    broken = []
    for edge in store.list_edges():
        if edge.source_id != node_id:
            continue
        dst = store.get_node(edge.destination_id)
        if not check_edge(edge.contract, edge.contract_config, node_id, src.output, dst.output).holds:
            broken.append((edge.source_id, edge.destination_id))
    return broken


def apply_event(store: GraphStore, event: DisruptionEvent) -> ApplyOutcome:
    target = event.target_node
    try:
        node = store.get_node(target)
    except NodeNotFoundError:
        return ApplyOutcome("invalid", target, f"No node '{target}' in this trip.")
    if node.type.value not in TARGET_TYPES[event.type]:
        return ApplyOutcome("invalid", target, f"{event.type.value} cannot target a {node.type.value} node.")
    if node.status not in (NodeStatus.DONE, NodeStatus.AWAITING_APPROVAL) or not node.output:
        return ApplyOutcome("invalid", target, f"'{target}' has no completed output to disrupt.")

    out = dict(node.output)
    facts = dict(node.inputs.get("facts") or {})
    f = event.new_facts
    t = event.type

    if t == DisruptionType.FLIGHT_DELAY:
        delay = float(f["delay_minutes"])
        if delay == float(out.get("delay_minutes", 0)):
            return ApplyOutcome("duplicate", target, f"Delay of {delay:g} min is already recorded.")
        out.setdefault("scheduled_departure_time", out["departure_time"])
        out.setdefault("scheduled_arrival_time", out["arrival_time"])
        out["departure_time"] = _shift(out["scheduled_departure_time"], delay)
        out["arrival_time"] = _shift(out["scheduled_arrival_time"], delay)
        out["delay_minutes"] = delay
        out["status"] = "delayed" if delay else "on_time"
        facts.update(delay_minutes=delay, departure_time=out["departure_time"], arrival_time=out["arrival_time"],
                     scheduled_departure_time=out["scheduled_departure_time"],
                     scheduled_arrival_time=out["scheduled_arrival_time"], status=out["status"])
        return _facts(store, event, out, facts)

    if t == DisruptionType.WEATHER_STORM:
        condition = f.get("condition", "thunderstorm")
        if out.get("is_storm") and out.get("condition") == condition:
            return ApplyOutcome("duplicate", target, f"{target} already forecasts {condition}.")
        out.update(condition=condition, is_storm=condition in STORM_CONDITIONS, source="disruption")
        facts.update(condition=condition, is_storm=out["is_storm"], source="disruption")
        return _facts(store, event, out, facts)

    if t == DisruptionType.PRICE_SPIKE:
        pct = float(f["pct"])
        if pct == 0 or not isinstance(out.get("cost"), (int, float)):
            return ApplyOutcome("duplicate", target, "No price change to apply.")
        out.setdefault("original_cost", out["cost"])
        out["cost"] = round(float(out["cost"]) * (1 + pct / 100), 2)
        if "price" in out:
            out["price"] = out["cost"]
        out["price_change_pct"] = round(float(out.get("price_change_pct", 0)) + pct, 2)
        facts.update(cost=out["cost"], original_cost=out["original_cost"], price_change_pct=out["price_change_pct"])
        if "price" in out:
            facts["price"] = out["price"]
        return _facts(store, event, out, facts)

    # Invalidating events
    if t == DisruptionType.FLIGHT_CANCELLATION:
        offer = f.get("offer_id", out.get("offer_id"))
        if offer != out.get("offer_id"):
            return ApplyOutcome("duplicate", target, f"Offer {offer} was already replaced.")
        key, value, label = "exclude_offer_ids", offer, f"flight {out.get('flight_number')} cancelled"
    else:  # HOTEL_OVERBOOKED
        hotel = f.get("hotel_id", out.get("hotel_id"))
        if hotel != out.get("hotel_id"):
            return ApplyOutcome("duplicate", target, f"Hotel {hotel} was already replaced.")
        key, value, label = "exclude_hotel_ids", hotel, f"{out.get('hotel_name')} overbooked"

    inputs = dict(node.inputs)
    inputs[key] = sorted(set(inputs.get(key, [])) | {value})
    inputs.pop("facts", None)
    store.set_node_inputs(target, inputs)              # recorded in history with the status change below
    out["status"] = "cancelled" if t == DisruptionType.FLIGHT_CANCELLATION else "unavailable"
    store.update_node_status(target, node.status, output=out, reason=f"disruption {event.event_id}: {label}")
    return ApplyOutcome("invalidate", target, label)


def _facts(store: GraphStore, event: DisruptionEvent, out: dict[str, Any], facts: dict[str, Any]) -> ApplyOutcome:
    target = event.target_node
    node = store.set_node_inputs(target, {**store.get_node(target).inputs, "facts": facts})
    store.update_node_status(target, node.status, output=out,
                             reason=f"disruption {event.event_id}: {event.summary()}")
    broken = broken_out_edges(store, target)
    message = event.summary() + (f" — breaks {len(broken)} contract(s)" if broken else " — no contract affected")
    return ApplyOutcome("facts", target, message, broken)
