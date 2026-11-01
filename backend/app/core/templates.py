"""
Trip-graph templates owned by the Orchestrator.

  - ``propose_template_tasks``  deterministic task proposal; used by MockLLM
                                and as the fallback when the LLM keeps failing.
  - ``canonical_id`` / ``canonical_inputs``  stable node IDs and inputs derived
                                from the goal (the LLM's IDs and inputs are not trusted).
  - ``template_edges``          edges + contracts between task roles (plan §5.3).

The LLM only decides which tasks exist and their day themes; everything here
is application code.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.contracts import ContractType
from app.core.models import Edge, NodeType
from app.schemas.trip import ProposedPlan, ProposedTask, TripGoal

# Agent that executes each node type (slide: Flight / Hotel / Itinerary / Budget).
CANONICAL_AGENT: dict[NodeType, str] = {
    NodeType.FLIGHT: "flight_agent",
    NodeType.STAY: "hotel_agent",
    NodeType.TRANSFER: "itinerary_agent",
    NodeType.DAY_PLAN: "itinerary_agent",
    NodeType.WEATHER: "weather_source",
    NodeType.BUDGET: "budget_agent",
}

FLIGHT_OUT, FLIGHT_RET = "flight_outbound", "flight_return"
HOTEL = "hotel_stay"
TRANSFER_IN, TRANSFER_OUT = "airport_transfer_in", "airport_transfer_out"
BUDGET = "budget_check"

COST_TYPES = frozenset({NodeType.FLIGHT, NodeType.STAY, NodeType.TRANSFER, NodeType.DAY_PLAN})


def day_plan_id(day: int) -> str:
    return f"day_{day}_plan"


def weather_id(day: int) -> str:
    return f"weather_day_{day}"


def canonical_id(task: ProposedTask) -> str:
    """Stable node ID for a proposed task's role."""
    t = task.task_type
    if t == NodeType.FLIGHT:
        return FLIGHT_RET if task.direction == "return" else FLIGHT_OUT
    if t == NodeType.STAY:
        return HOTEL
    if t == NodeType.TRANSFER:
        return TRANSFER_OUT if task.direction == "departure" else TRANSFER_IN
    if t == NodeType.DAY_PLAN:
        return day_plan_id(task.day or 0)
    if t == NodeType.WEATHER:
        return weather_id(task.day or 0)
    return BUDGET


def _themes(goal: TripGoal) -> list[str]:
    interests = goal.interests or ["culture"]
    themes = ["Arrival, sunset & dinner"]
    for d in range(2, goal.nights + 1):
        themes.append(f"{interests[(d - 2) % len(interests)].title()} day in {goal.destination}")
    return themes


def propose_template_tasks(goal: TripGoal) -> ProposedPlan:
    """The deterministic task set for a goal (§5.3 shape, one plan + forecast per day)."""
    tasks = [
        ProposedTask(task_id=FLIGHT_OUT, task_type=NodeType.FLIGHT, direction="outbound"),
        ProposedTask(task_id=FLIGHT_RET, task_type=NodeType.FLIGHT, direction="return"),
        ProposedTask(task_id=HOTEL, task_type=NodeType.STAY),
        ProposedTask(task_id=TRANSFER_IN, task_type=NodeType.TRANSFER, direction="arrival"),
        ProposedTask(task_id=TRANSFER_OUT, task_type=NodeType.TRANSFER, direction="departure"),
    ]
    for day, theme in enumerate(_themes(goal), start=1):
        tasks.append(ProposedTask(task_id=weather_id(day), task_type=NodeType.WEATHER, day=day))
        tasks.append(ProposedTask(task_id=day_plan_id(day), task_type=NodeType.DAY_PLAN, day=day, theme=theme))
    tasks.append(ProposedTask(task_id=BUDGET, task_type=NodeType.BUDGET))
    return ProposedPlan(
        tasks=tasks,
        summary=(f"{goal.departure_city} → {goal.destination}, {goal.nights} nights, "
                 f"{goal.travelers} traveller(s), {goal.budget:g} {goal.currency}"),
    )


def canonical_inputs(task: ProposedTask, goal: TripGoal) -> dict[str, Any]:
    """Concrete node inputs, computed from the goal (never taken from the LLM)."""
    base: dict[str, Any] = {"currency": goal.currency, "travelers": goal.travelers}
    prefs = goal.preferences or {}
    t = task.task_type

    if t == NodeType.FLIGHT:
        outbound = task.direction != "return"
        return base | {
            "direction": "outbound" if outbound else "return",
            "origin": goal.departure_city if outbound else goal.destination,
            "destination": goal.destination if outbound else goal.departure_city,
            "departure_date": (goal.departure_date if outbound else goal.return_date).isoformat(),
            "cabin": prefs.get("cabin", "economy"),
        }
    if t == NodeType.STAY:
        return base | {
            "city": goal.destination,
            "check_in": goal.departure_date.isoformat(),
            "check_out": goal.return_date.isoformat(),
            "travel_style": goal.travel_style,
            "accommodation": prefs.get("accommodation", "hotel"),
        }
    if t == NodeType.TRANSFER:
        if task.direction == "departure":
            return base | {"direction": "departure", "city": goal.destination, "airport_buffer_minutes": 180}
        return base | {"direction": "arrival", "city": goal.destination, "pickup_offset_minutes": 30}
    if t in (NodeType.DAY_PLAN, NodeType.WEATHER):
        day = task.day or 1
        inputs = {
            "day": day,
            "date": (goal.departure_date + timedelta(days=day - 1)).isoformat(),
            "city": goal.destination,
        }
        if t == NodeType.WEATHER:
            return inputs
        return base | inputs | {
            "theme": task.theme,
            "interests": list(goal.interests),
            "pace": prefs.get("pace", "balanced"),
        }
    return {"budget_limit": goal.budget, "currency": goal.currency}


def _edge(src: str, dst: str, contract: ContractType, description: str, **config: Any) -> Edge:
    return Edge(source_id=src, destination_id=dst, contract=contract.value,
                contract_config=config, description=description)


def template_edges(present: dict[str, NodeType]) -> list[Edge]:
    """
    Edges and contracts for the nodes present (keyed by canonical ID).
    Only pairs whose both ends exist are emitted.
    """
    edges: list[Edge] = []
    days = sorted(int(nid.split("_")[1]) for nid, t in present.items() if t == NodeType.DAY_PLAN)

    def has(*ids: str) -> bool:
        return all(i in present for i in ids)

    if has(FLIGHT_OUT, HOTEL):
        edges.append(_edge(FLIGHT_OUT, HOTEL, ContractType.FLIGHT_BEFORE_CHECKIN,
                           "arrival + 90 min ≤ check-in cutoff", buffer_minutes=90))
    if has(FLIGHT_OUT, TRANSFER_IN):
        edges.append(_edge(FLIGHT_OUT, TRANSFER_IN, ContractType.TRANSFER_AFTER_FLIGHT,
                           "pickup 15–90 min after arrival", min_gap_minutes=15, max_gap_minutes=90))
    for transfer in (TRANSFER_IN, TRANSFER_OUT):
        if has(HOTEL, transfer):
            edges.append(_edge(HOTEL, transfer, ContractType.TRANSFER_SERVES_HOTEL,
                               "transfer is routed to the booked hotel"))
    if days and days[0] == 1 and has(TRANSFER_IN):
        edges.append(_edge(TRANSFER_IN, day_plan_id(1), ContractType.TRANSFER_BEFORE_DAY_START,
                           "reach the hotel before day 1 starts", buffer_minutes=30))
    for d in days:
        if has(HOTEL):
            edges.append(_edge(HOTEL, day_plan_id(d), ContractType.STAY_COVERS_DAY,
                               "day plan is based at the booked hotel"))
        if has(weather_id(d)):
            edges.append(_edge(weather_id(d), day_plan_id(d), ContractType.WEATHER_SUITS_PLAN,
                               "no outdoor items in storms"))
    if has(FLIGHT_RET, TRANSFER_OUT):
        edges.append(_edge(FLIGHT_RET, TRANSFER_OUT, ContractType.TRANSFER_BEFORE_DEPARTURE,
                           "at the airport 2–4 h before departure",
                           min_buffer_minutes=120, max_buffer_minutes=240))
    if days and has(TRANSFER_OUT):
        edges.append(_edge(day_plan_id(days[-1]), TRANSFER_OUT, ContractType.DAY_ENDS_BEFORE_PICKUP,
                           "last day ends before the airport pickup", buffer_minutes=60))
    if has(BUDGET):
        for nid, t in present.items():
            if t in COST_TYPES:
                edges.append(_edge(nid, BUDGET, ContractType.BUDGET_ITEM_CURRENT,
                                   "budget uses this item's current cost"))
    return edges
