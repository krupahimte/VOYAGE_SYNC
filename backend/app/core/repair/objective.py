"""
The repair objective (pre-registration R4): four terms that do not overlap, each
on a fixed scale chosen before any experiment, and a weighted sum that the
search maximises as ``fitness = -(w·terms)``. Feasibility is never a term: plans
are ordered feasibility first (Deb's rule, ``deb_key``).

  disruption          mean change the traveller sees, over the user-visible tasks
                      except the disrupted one (each task 0 = unchanged … 1 = replaced)
  money               trip-cost change as a share of the budget, plus the same again
                      for any new spending above the budget (so overrun counts double)
  activity_time_lost  activity minutes lost, summed over days (a gain on one day does
                      not offset a loss on another), as a share of the planned minutes
  approvals           booked items whose offer changed, as a share of the bookable items
                      (each one needs a T2 approval)

Disruption, activity time and approvals are fractions in [0, 1]. Money is a
fraction of the user's own budget, so 1 means a change of one whole budget; it is
negative when the repair saves money. Travel time enters disruption (a different
flight, hotel or pickup time) rather than as a separate term, so a change is not
counted twice. Preferences are not a term: plans do not yet record what each
activity's category is (R5).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

from app.core.metrics import USER_VISIBLE, task_unchanged
from app.core.models import NodeType
from app.core.repair.constraints import ValidationReport
from app.core.risk_gate import BOOKABLE
from app.core.templates import BUDGET, COST_TYPES

# A pickup moved by this much or more counts as fully changed.
SHIFT_SCALE_MINUTES = 180
# Extra weight on new spending above the budget, relative to spending within it.
OVERRUN_FACTOR = 1.0
TERMS = ("disruption", "money", "activity_time_lost", "approvals")


@dataclass(frozen=True)
class ObjectiveWeights:
    disruption: float = 1.0
    money: float = 1.0
    activity_time_lost: float = 1.0
    approvals: float = 1.0


@dataclass(frozen=True)
class Score:
    terms: dict[str, float]
    fitness: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _minutes_between(a: Any, b: Any) -> float:
    return abs((datetime.fromisoformat(str(a)) - datetime.fromisoformat(str(b))).total_seconds()) / 60


def item_change(node_type: NodeType, before: dict | None, after: dict | None) -> float:
    """How much of one user-visible task the traveller sees changed, from 0 to 1."""
    if task_unchanged(node_type, before, after):
        return 0.0
    before, after = before or {}, after or {}
    if node_type == NodeType.TRANSFER and before.get("hotel_id") == after.get("hotel_id") \
            and before.get("pickup_time") and after.get("pickup_time"):
        return min(1.0, _minutes_between(before["pickup_time"], after["pickup_time"]) / SHIFT_SCALE_MINUTES)
    if node_type == NodeType.DAY_PLAN:
        old = {(a.get("name"), a.get("start")) for a in before.get("activities") or []}
        new = {(a.get("name"), a.get("start")) for a in after.get("activities") or []}
        return 1.0 - len(old & new) / len(old) if old else (0.0 if not new else 1.0)
    return 1.0                                           # another flight or hotel


def _minutes(output: dict | None) -> int:
    return sum(int(a.get("duration_minutes") or 0) for a in (output or {}).get("activities") or [])


def _total(graph: Any) -> float:
    try:
        total = (graph.get_node(BUDGET).output or {}).get("total_cost")
    except Exception:  # noqa: BLE001 — a graph without a budget node falls back to summing costs
        total = None
    if isinstance(total, (int, float)):
        return float(total)
    return float(sum((n.output or {}).get("cost") or 0 for n in graph.list_nodes() if n.type in COST_TYPES))


def _limit(graph: Any) -> float | None:
    try:
        limit = (graph.get_node(BUDGET).output or {}).get("budget_limit")
    except Exception:  # noqa: BLE001
        return None
    return float(limit) if isinstance(limit, (int, float)) and limit > 0 else None


def terms(reference: Any, graph: Any, target: str | Iterable[str]) -> dict[str, float]:
    """
    The four terms of *graph* (a repaired plan) against *reference* (the plan the repair started
    from). *target* is the disrupted task, or all of them for a combined disruption.
    """
    targets = {target} if isinstance(target, str) else set(target)
    before = {n.id: n for n in reference.list_nodes()}
    after = {n.id: n for n in graph.list_nodes()}

    visible = [nid for nid, n in before.items() if n.type in USER_VISIBLE and nid not in targets]
    disruption = (sum(item_change(before[n].type, before[n].output, after[n].output) for n in visible) / len(visible)
                  if visible else 0.0)

    limit = _limit(reference) or _limit(graph)
    total_before, total_after = _total(reference), _total(graph)
    if limit:
        overrun = max(0.0, total_after - limit) - max(0.0, total_before - limit)
        money = (total_after - total_before) / limit + OVERRUN_FACTOR * max(0.0, overrun) / limit
    else:
        money = 0.0

    days = [nid for nid, n in before.items() if n.type == NodeType.DAY_PLAN]
    planned = sum(_minutes(before[d].output) for d in days)
    lost = sum(max(0, _minutes(before[d].output) - _minutes(after[d].output)) for d in days)
    activity_time_lost = lost / planned if planned else 0.0

    bookable = [nid for nid, n in before.items() if n.type in BOOKABLE]
    rebooked = [nid for nid in bookable if (before[nid].output or {}).get("booking_status") == "booked"
                and (before[nid].output or {}).get("offer_id") != (after[nid].output or {}).get("offer_id")]
    approvals = len(rebooked) / len(bookable) if bookable else 0.0

    return {"disruption": round(disruption, 6), "money": round(money, 6),
            "activity_time_lost": round(activity_time_lost, 6), "approvals": round(approvals, 6)}


def score(reference: Any, graph: Any, target: str | Iterable[str],
          weights: ObjectiveWeights = ObjectiveWeights()) -> Score:
    t = terms(reference, graph, target)
    fitness = -sum(getattr(weights, name) * t[name] for name in TERMS)
    return Score(t, round(fitness, 6))


def deb_key(report: ValidationReport, fitness: float) -> tuple[int, int, float]:
    """Sort key, smaller is better: any feasible plan, then fewer violations, then higher fitness."""
    return (0 if report.feasible else 1, report.violation_count, -fitness)
