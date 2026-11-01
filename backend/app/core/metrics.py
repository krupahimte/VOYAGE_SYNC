"""
Replan Efficiency metrics for one disruption (plan §6.6), used by the demo's
metrics panel: this replan vs the B1 full-replan baseline, both scored
against an oracle's affected set.

Oracle: start from the graph as it was before the event, apply the event,
then repeatedly re-check **every** contract in the graph (not only those
reachable from the changed node) with full knowledge of the new facts. The
shallowest broken nodes are regenerated (with frozen context) and the check
repeats until every contract holds. The nodes it had to regenerate are the
true affected set A. It is contract-based, so it can't see a dependency no
contract encodes; the Phase 8 benchmark validates it on a hand-labelled subset.

Every comparison run works on deep copies of the graph, never touches the
trip, its bookings or its Risk Gate ledger.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

from app.core.contracts import ContractResult, check_edge
from app.core.graph import GraphStore, NodeNotFoundError
from app.core.models import NodeStatus, NodeType
from app.core.monitor.apply import apply_event
from app.core.replanner import Replanner, ReplanResult, _depths

USER_VISIBLE = {NodeType.FLIGHT, NodeType.STAY, NodeType.TRANSFER, NodeType.DAY_PLAN}
# Booking bookkeeping written by the Risk Gate is not a change to the plan.
_BOOKKEEPING = {"booking_status", "booking_ref", "booked_at", "booking_provider", "approval_id"}


def _plan_output(output: dict | None) -> dict:
    return {k: v for k, v in (output or {}).items() if k not in _BOOKKEEPING}


def precision_recall_f1(regenerated: list[str], affected: list[str]) -> tuple[float, float, float]:
    """|R∩A|/|R|, |R∩A|/|A| and F1; an empty set scores 1.0 when the other side is empty too."""
    r, a = set(regenerated), set(affected)
    hit = len(r & a)
    precision = hit / len(r) if r else (1.0 if not a else 0.0)
    recall = hit / len(a) if a else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _experience(node_type: NodeType, output: dict | None) -> Any:
    """What the traveller experiences of an item: which flight, hotel, pickup or activities, and when.

    A re-quoted price for the same flight or room is not a change to the plan.
    """
    o = _plan_output(output)
    if node_type == NodeType.FLIGHT:
        return (o.get("flight_number"), o.get("departure_time"), o.get("arrival_time"), o.get("status"))
    if node_type == NodeType.STAY:
        return (o.get("hotel_id"), o.get("check_in"), o.get("check_out"), o.get("room_type"))
    if node_type == NodeType.TRANSFER:
        return (o.get("hotel_id"), o.get("pickup_time"), o.get("dropoff_time"))
    if node_type == NodeType.DAY_PLAN:
        return tuple((a.get("name"), a.get("start")) for a in o.get("activities") or [])
    return o


def plan_stability(before: GraphStore, after: GraphStore, exclude: set[str]) -> float:
    """Share of user-visible items (flights, stay, transfers, day plans) the traveller sees unchanged.

    Items are compared on what the traveller experiences (``_experience``), not on every output
    field. The disrupted node itself is excluded: the world changed it, not the planner.
    """
    nodes = [n for n in before.list_nodes() if n.type in USER_VISIBLE and n.id not in exclude]
    if not nodes:
        return 1.0
    same = sum(_experience(n.type, n.output) == _experience(n.type, after.get_node(n.id).output) for n in nodes)
    return round(same / len(nodes), 4)


def _contracts(store: GraphStore) -> tuple[int, int]:
    checks = [check_edge(e.contract, e.contract_config, e.source_id, store.get_node(e.source_id).output,
                         store.get_node(e.destination_id).output).holds for e in store.list_edges()]
    return sum(checks), len(checks)


async def oracle_affected(registry: Any, store_before: GraphStore, event: Any) -> list[str]:
    """True affected set A for *event* applied to *store_before* (which is not modified)."""
    store = copy.deepcopy(store_before)
    applied = apply_event(store, event)
    if applied.kind in ("duplicate", "invalid"):
        return []
    replanner = Replanner(registry)
    result = ReplanResult(strategy="oracle", target=applied.target, trigger=applied.kind)
    ctx = {"graph_id": store.graph_id, "oracle": True}
    if applied.kind == "invalidate":
        replanner._mark_stale(store, applied.target, "invalidated by the event", result)
        await replanner._regenerate(store, [applied.target], result, True, ctx)

    order, depth = _depths(store)
    for _ in range(len(order)):
        broken = {e.destination_id for e in store.list_edges()
                  if e.destination_id not in result.stale_reasons
                  and not check_edge(e.contract, e.contract_config, e.source_id,
                                     store.get_node(e.source_id).output,
                                     store.get_node(e.destination_id).output).holds}
        if not broken:
            break
        level = min(depth[b] for b in broken)
        batch = [nid for nid in order if nid in broken and depth[nid] == level]
        for nid in batch:
            replanner._mark_stale(store, nid, "contract broken (oracle)", result)
        await replanner._regenerate(store, batch, result, True, ctx)
    return [nid for nid in order if nid in result.stale_reasons]


def _score(name: str, regenerated: list[str], affected: list[str], before: GraphStore, after: GraphStore,
           target: str, replan: ReplanResult | None, latency_ms: float) -> dict[str, Any]:
    precision, recall, f1 = precision_recall_f1(regenerated, affected)
    holding, total = _contracts(after)
    api_calls = (replan.api_calls if replan else {}) or {}
    return {
        "strategy": name,
        "regenerated": regenerated,
        "regenerated_count": len(regenerated),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "stability": plan_stability(before, after, {target}),
        "contracts_holding": holding,
        "contracts_total": total,
        "latency_ms": latency_ms,
        "llm_calls": replan.llm_calls if replan else 0,
        "api_calls": sum(api_calls.values()),
    }


async def compare_with_baseline(orchestrator: Any, store_before: GraphStore, event: Any, outcome: Any,
                                store_after: GraphStore) -> dict[str, Any]:
    """Metrics of this replan (*outcome* on *store_after*) and of B1 on a copy, against the oracle."""
    affected = await oracle_affected(orchestrator.registry, store_before, event)
    target = event.target_node
    ours_regen = outcome.replan.regenerated if outcome.replan else []
    strategy = outcome.replan.strategy if outcome.replan else "contract"

    b1_store = copy.deepcopy(store_before)
    b1 = await orchestrator.handle_disruption(b1_store, event, strategy="full")
    b1_regen = b1.replan.regenerated if b1.replan else []
    return {
        "oracle": {"affected": affected,
                   "method": "every contract re-checked with full knowledge of the event, to a fixed point"},
        "replan": _score(strategy, ours_regen, affected, store_before, store_after, target, outcome.replan,
                       outcome.latency_ms),
        "b1": _score("full", b1_regen, affected, store_before, b1_store, target, b1.replan, b1.latency_ms),
    }


# ── Evaluation metrics v2 (Phase 8b) ──────────────────────────────────────────
# Operational definitions, documented in eval/results_v2/METRICS_REPORT.md. They are
# pure functions of graph snapshots: no agents, no providers, no LLM. ``None`` means
# "not applicable" (zero denominator, or nothing checkable) and must never be read
# as 0 % or 100 %. The functions above (stability, precision/recall, oracle) are
# unchanged.

PLAN_VALID_STATUSES = {NodeStatus.DONE, NodeStatus.AWAITING_APPROVAL}
REQUIREMENTS = ("dates", "party_size", "budget", "interests")
_UNEVALUABLE = ("Invalid input", "Contract cannot be checked")


def ratio(numerator: float, denominator: float, digits: int = 4) -> float | None:
    """numerator / denominator, or None when the denominator is 0."""
    return round(numerator / denominator, digits) if denominator else None


def task_unchanged(node_type: NodeType, before: dict | None, after: dict | None) -> bool:
    """User-visible tasks compare on what the traveller experiences; others on their whole plan output."""
    if node_type in USER_VISIBLE:
        return _experience(node_type, before) == _experience(node_type, after)
    return _plan_output(before) == _plan_output(after)


def task_preservation(before: GraphStore, after: GraphStore, affected: Iterable[str], target: str) -> dict[str, Any]:
    """
    Task Preservation Rate over the *unaffected* tasks only:
    U = all tasks − oracle-affected set A − the disrupted target;  TPR = |unchanged ∩ U| / |U|.
    Also reported for the user-visible subset of U. Empty U → rate None.
    """
    excluded = set(affected) | {target}
    unaffected = [n for n in before.list_nodes() if n.id not in excluded]
    preserved = [n for n in unaffected if task_unchanged(n.type, n.output, after.get_node(n.id).output)]
    visible = [n for n in unaffected if n.type in USER_VISIBLE]
    visible_kept = [n for n in preserved if n.type in USER_VISIBLE]
    return {
        "unaffected": len(unaffected), "preserved": len(preserved), "rate": ratio(len(preserved), len(unaffected)),
        "visible_unaffected": len(visible), "visible_preserved": len(visible_kept),
        "visible_rate": ratio(len(visible_kept), len(visible)),
    }


def contract_evaluable(result: ContractResult) -> bool:
    """False when a contract could not be evaluated (missing data), as opposed to evaluated and broken."""
    return not result.explanation.startswith(_UNEVALUABLE)


_EXEC_KEYS = ("e1_completion", "e2_output_valid", "e3_dependencies_evaluable", "e4_contracts_hold",
              "plan_valid", "execution_ready", "task_plan_valid_rate", "task_ready_rate")


def executability(store: GraphStore) -> dict[str, Any]:
    """
    Operational executability (not TDAG's definition). Run-level checks:
      E1 completion     — every task is DONE or AWAITING_APPROVAL and has an output
      E2 output valid   — every output passes the agents' validators
      E3 dependencies   — every edge contract can be evaluated
      E4 plan valid     — every edge contract holds
    plan_valid = E1 and E2 and E3 and E4. execution_ready additionally requires every
    task DONE: a task still awaiting the user's approval is a valid plan, not yet
    executable. Task-level rates are reported separately. An empty graph gives None.
    """
    from app.agents.validation import validate_output   # local import: agents depend on core

    nodes = store.list_nodes()
    counts = {"tasks": len(nodes), "awaiting_approval": 0, "invalid_outputs": 0, "unevaluable_contracts": 0,
              "broken_contracts": 0}
    if not nodes:
        return {key: None for key in _EXEC_KEYS} | counts
    output_ok = {n.id: bool(n.output) and not validate_output(n.type, n.output) for n in nodes}
    status_ok = {n.id: n.status in PLAN_VALID_STATUSES and bool(n.output) for n in nodes}
    ready = {n.id: n.status == NodeStatus.DONE and bool(n.output) for n in nodes}
    checks = [check_edge(e.contract, e.contract_config, e.source_id, store.get_node(e.source_id).output,
                         store.get_node(e.destination_id).output) for e in store.list_edges()]
    e1, e2 = all(status_ok.values()), all(output_ok.values())
    e3 = all(contract_evaluable(c) for c in checks)
    e4 = all(c.holds for c in checks)
    plan_valid = e1 and e2 and e3 and e4
    return counts | {
        "e1_completion": e1, "e2_output_valid": e2, "e3_dependencies_evaluable": e3, "e4_contracts_hold": e4,
        "plan_valid": plan_valid,
        "execution_ready": plan_valid and all(ready.values()),
        "awaiting_approval": sum(1 for n in nodes if n.status == NodeStatus.AWAITING_APPROVAL),
        "invalid_outputs": sum(1 for ok in output_ok.values() if not ok),
        "unevaluable_contracts": sum(1 for c in checks if not contract_evaluable(c)),
        "broken_contracts": sum(1 for c in checks if not c.holds),
        "task_plan_valid_rate": ratio(sum(status_ok[i] and output_ok[i] for i in status_ok), len(nodes)),
        "task_ready_rate": ratio(sum(ready[i] and output_ok[i] for i in ready), len(nodes)),
    }


def _output(store: GraphStore, node_id: str) -> dict | None:
    try:
        return store.get_node(node_id).output or None
    except NodeNotFoundError:
        return None


def user_requirements(goal: Any, store: GraphStore) -> dict[str, bool | None]:
    """
    The user's own requirements that the plan's outputs let us check (True / False),
    or None when not checkable (a missing output, nothing requested):
      dates       — outbound and return flights depart on the requested dates; the stay covers them
      party_size  — both flights carry every traveller; rooms × 2 ≥ travellers (2 guests per room)
      budget      — total trip cost ≤ the user's budget (same currency)
      interests   — every day after arrival is themed on one of the requested interests
    Accommodation type, pace, flexible dates and notes are not present in the outputs.
    """
    out_flight, ret_flight = _output(store, "flight_outbound"), _output(store, "flight_return")
    stay, budget = _output(store, "hotel_stay"), _output(store, "budget_check")
    dep, ret = goal.departure_date.isoformat(), goal.return_date.isoformat()

    dates = None
    if out_flight and ret_flight and stay:
        dates = (out_flight.get("departure_date") == dep and ret_flight.get("departure_date") == ret
                 and stay.get("check_in") == dep and stay.get("check_out") == ret)
    party = None
    if out_flight and ret_flight and stay and isinstance(stay.get("rooms"), int):
        party = (out_flight.get("passengers") == goal.travelers and ret_flight.get("passengers") == goal.travelers
                 and stay["rooms"] * 2 >= goal.travelers)
    within = None
    if budget and isinstance(budget.get("total_cost"), (int, float)) and budget.get("currency") == goal.currency:
        within = budget["total_cost"] <= goal.budget
    interests = None
    wanted = [i.lower() for i in goal.interests or []]
    days = [n for n in store.list_nodes() if n.type == NodeType.DAY_PLAN and int(n.inputs.get("day", 0)) >= 2]
    if wanted and days and all(n.output for n in days):
        interests = all(any(w in str(n.output.get("theme", "")).lower() for w in wanted) for n in days)
    return {"dates": dates, "party_size": party, "budget": within, "interests": interests}


def requirement_satisfaction(requirements: dict[str, bool | None]) -> dict[str, Any]:
    """Met / checkable over the checkable requirements only; nothing checkable → rate None."""
    checkable = [v for v in requirements.values() if v is not None]
    met = sum(1 for v in checkable if v)
    return {"met": met, "checkable": len(checkable), "rate": ratio(met, len(checkable))}


def requirement_regressions(before: dict[str, bool | None], after: dict[str, bool | None]) -> list[str]:
    """Requirements that held before the disruption and are violated after the replan."""
    return [k for k in REQUIREMENTS if before.get(k) is True and after.get(k) is False]


def travel_cost_change(before: GraphStore, after: GraphStore) -> dict[str, Any]:
    """
    The traveller's money (not computational cost): total trip cost before and after the
    replan, and the change as a share of the budget limit. Missing totals → None.
    """
    b, a = _output(before, "budget_check") or {}, _output(after, "budget_check") or {}
    total_b, total_a = b.get("total_cost"), a.get("total_cost")
    limit = a.get("budget_limit", b.get("budget_limit"))
    numbers = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (total_b, total_a))
    return {
        "total_before": total_b if numbers else None,
        "total_after": total_a if numbers else None,
        "change_of_budget": (ratio(total_a - total_b, limit) if numbers and isinstance(limit, (int, float))
                             and limit > 0 else None),
        "within_budget_after": a.get("within_budget") if isinstance(a.get("within_budget"), bool) else None,
    }


def _activity_minutes(output: dict | None) -> int:
    return sum(int(x.get("duration_minutes") or 0) for x in (output or {}).get("activities") or [])


def activity_time_retention(before: GraphStore, after: GraphStore, affected: Iterable[str]) -> dict[str, Any]:
    """
    Planned activity minutes kept on the *affected* day plans: sum after / sum before over
    the day plans in A. No affected day plan, or 0 minutes before → rate None. A value
    above 1 means the repair scheduled more activity time than before.
    """
    day_plans = {n.id for n in before.list_nodes() if n.type == NodeType.DAY_PLAN}
    days = [nid for nid in affected if nid in day_plans]
    minutes_before = sum(_activity_minutes(_output(before, d)) for d in days)
    minutes_after = sum(_activity_minutes(_output(after, d)) for d in days)
    return {"days": len(days), "minutes_before": minutes_before, "minutes_after": minutes_after,
            "rate": ratio(minutes_after, minutes_before) if days else None}


def contract_recovered(contracts_holding: int, contracts_total: int, failed_tasks: int) -> bool:
    """The existing definition: every contract holds and no task failed."""
    return contracts_holding == contracts_total and failed_tasks == 0


def strict_recovered(plan_valid: bool | None, regressions: list[str]) -> bool:
    """A valid plan (executability E1–E4) that broke none of the user's requirements that held before."""
    return bool(plan_valid) and not regressions
