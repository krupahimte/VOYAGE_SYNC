"""
Hard constraints of a repaired plan (pre-registration R6). A plan is feasible
only if every check passes; nothing here is ever relaxed by the search.

  contracts     every edge contract holds and could be evaluated
  schemas       every repaired output passes its agent's output schema
  frozen        every task outside the repair region is exactly as it was
  requirements  the user's dates and party size (budget is soft: an objective term)
  exclusions    no cancelled offer or overbooked hotel is used again
  activities    every repaired day plan has an activity, unless the arrival makes that impossible
  dag           the graph is still acyclic

The checks reuse the existing definitions (``check_edge``, ``contract_evaluable``,
``validate_output``, ``user_requirements``); none of them is redefined here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Iterable

from app.agents.validation import validate_output
from app.core.contracts import check_edge
from app.core.graph import GraphError, NodeNotFoundError
from app.core.metrics import contract_evaluable, user_requirements
from app.core.models import NodeType
from app.core.templates import BUDGET, FLIGHT_OUT, FLIGHT_RET

HARD_REQUIREMENTS = ("dates", "party_size")
# Mirrors DayPlanAgent: the first activity can start 60 min after the arrival drop-off.
ARRIVAL_SETTLE_MINUTES = 60


@dataclass
class ValidationReport:
    contracts: list[str] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)
    frozen: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    activities: list[str] = field(default_factory=list)
    dag: list[str] = field(default_factory=list)

    CHECKS = ("contracts", "schemas", "frozen", "requirements", "exclusions", "activities", "dag")

    def violations(self) -> list[str]:
        return [f"{check}: {v}" for check in self.CHECKS for v in getattr(self, check)]

    @property
    def violation_count(self) -> int:
        return sum(len(getattr(self, check)) for check in self.CHECKS)

    @property
    def feasible(self) -> bool:
        return self.violation_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {"feasible": self.feasible, **{check: list(getattr(self, check)) for check in self.CHECKS}}


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).replace(tzinfo=None)


def empty_day_allowed(graph: Any, node_id: str) -> bool:
    """
    Only day 1 may be empty, and only when the arrival leaves no start time on that date: as in
    DayPlanAgent, the earliest start is the drop-off + 60 min, rounded up to the next quarter hour.
    """
    node = graph.get_node(node_id)
    if int(node.inputs.get("day", 0) or 0) != 1:
        return False
    day = str((node.output or {}).get("date") or node.inputs.get("date", ""))[:10]
    for edge in graph.list_edges():
        if edge.destination_id != node_id:
            continue
        src = graph.get_node(edge.source_id)
        out = src.output or {}
        if src.type == NodeType.TRANSFER and out.get("direction") == "arrival" and out.get("dropoff_time"):
            earliest = _parse(out["dropoff_time"]) + timedelta(minutes=ARRIVAL_SETTLE_MINUTES)
            earliest += timedelta(minutes=(-earliest.minute) % 15)
            return earliest.date().isoformat() > day
    return False


def goal_from_graph(store: Any) -> SimpleNamespace | None:
    """
    The parts of the TripGoal the requirement checks read, recovered from the planned
    nodes' inputs (which the Orchestrator computed from the goal). None if a node is missing.
    """
    try:
        out, ret = store.get_node(FLIGHT_OUT).inputs, store.get_node(FLIGHT_RET).inputs
        budget = store.get_node(BUDGET).inputs
    except NodeNotFoundError:
        return None
    days = [n for n in store.list_nodes() if n.type == NodeType.DAY_PLAN]
    return SimpleNamespace(
        departure_date=date.fromisoformat(str(out["departure_date"])[:10]),
        return_date=date.fromisoformat(str(ret["departure_date"])[:10]),
        travelers=int(out.get("travelers", 1)),
        budget=float(budget["budget_limit"]),
        currency=budget.get("currency"),
        interests=list(days[0].inputs.get("interests") or []) if days else [],
    )


def validate_plan(graph: Any, reference: Any, region: Iterable[str], goal: Any = None) -> ValidationReport:
    """
    Check *graph* (a GraphView or GraphStore) against the hard constraints.

    *reference* is the plan the repair started from (the store after the event was
    applied); every task outside *region* must be unchanged from it. *goal* is the
    TripGoal for the requirement checks (None skips them).
    """
    report = ValidationReport()
    region = set(region)
    nodes = {n.id: n for n in graph.list_nodes()}

    try:
        graph.validate()
    except GraphError as exc:
        report.dag.append(str(exc))

    for e in graph.list_edges():
        result = check_edge(e.contract, e.contract_config, e.source_id,
                            nodes[e.source_id].output, nodes[e.destination_id].output)
        if not result.holds or not contract_evaluable(result):
            report.contracts.append(f"{e.source_id} → {e.destination_id} {e.contract}: {result.explanation}")

    for nid, node in nodes.items():
        if nid in region:
            if not node.output:
                report.schemas.append(f"{nid}: no output")
            else:
                report.schemas += [f"{nid}: {err}" for err in validate_output(node.type, node.output)]
        elif node.output != reference.get_node(nid).output:
            report.frozen.append(f"{nid} changed outside the repair region")

        out = node.output or {}
        for key, field_name in (("exclude_offer_ids", "offer_id"), ("exclude_hotel_ids", "hotel_id")):
            if out.get(field_name) is not None and out.get(field_name) in set(node.inputs.get(key) or []):
                report.exclusions.append(f"{nid} uses excluded {field_name} {out[field_name]}")

        if nid in region and node.type == NodeType.DAY_PLAN and node.output is not None \
                and not out.get("activities") and not empty_day_allowed(graph, nid):
            report.activities.append(f"{nid} has no activities although the day has time for them")

    if goal is not None:
        met = user_requirements(goal, graph)
        report.requirements += [f"{name} not met" for name in HARD_REQUIREMENTS if met.get(name) is False]
    return report
