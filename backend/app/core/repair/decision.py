"""
Decision log (paper §VIII): for every handled disruption, what happened, which tasks
were affected and why (the violated contracts), which alternatives were considered,
which repair was selected, how it was validated, and what the user approved.

Two halves:
  ``search_log``  built by the repair search right after it runs (alternatives, the
                  options of every task on the chosen path and why rejected ones were
                  rejected, the chosen plan's terms, budget and per-stage cost); stored in
                  the disruption record as ``replan.repair.decision_log``.
  ``decision_log`` assembled when read: the stored disruption record (written once, never
                  updated) plus the Risk Gate actions raised for that event, with their
                  current decisions. Works for every strategy; E1/B1/B2 have no search
                  half and say so.
"""

from __future__ import annotations

from typing import Any

from app.core.models import NodeType
from app.core.repair.search import Plan, RepairSearch, SearchOutcome
from app.core.scheduler import Scheduler

CONSIDERED_LIMIT = 10      # complete plans listed per disruption (the counts cover all of them)


def option_label(node_type: NodeType | str, output: dict[str, Any] | None) -> str:
    """A one-line description of one option, for people reading the log."""
    o = output or {}
    t = NodeType(node_type)
    hm = lambda iso: str(iso)[11:16] if iso else "—"  # noqa: E731
    if t == NodeType.FLIGHT:
        return f"{o.get('flight_number')} {hm(o.get('departure_time'))}–{hm(o.get('arrival_time'))}, " \
               f"{o.get('cost')} {o.get('currency')}"
    if t == NodeType.STAY:
        return f"{o.get('hotel_name')}, {o.get('cost')} {o.get('currency')}"
    if t == NodeType.TRANSFER:
        return f"pickup {hm(o.get('pickup_time'))}, drop-off {hm(o.get('dropoff_time'))}"
    if t == NodeType.DAY_PLAN:
        names = [a.get("name") for a in o.get("activities") or []]
        return f"{len(names)} activities: " + (", ".join(names) if names else "none")
    if t == NodeType.BUDGET:
        return f"total {o.get('total_cost')} of {o.get('budget_limit')} {o.get('currency')}"
    return str(o.get("condition") or "")


def _plan_entry(plan: Plan) -> dict[str, Any]:
    return {"choices": dict(plan.state.choices), "region": list(plan.state.region),
            "feasible": plan.report.feasible, "fitness": plan.score.fitness, "terms": dict(plan.score.terms),
            "violations": plan.report.violations()[:5]}


async def options_on_path(search: RepairSearch, plan: Plan) -> dict[str, list[dict[str, Any]]]:
    """
    Every option each region task had on the chosen plan's path: its label, whether it was
    chosen, and the contract it broke if it was rejected. Read from the option cache (the
    options are the ones the search saw, not regenerated).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for nid in [n for n in search.order if n in plan.state.region]:
        ctx = plan.state.contexts.get(nid)
        if ctx is None:                      # best effort skipped a task with no option at all
            out[nid] = []
            continue
        node = search.store.get_node(nid)
        resolved = Scheduler._resolve_inputs(None, plan.state.view, nid)
        options = await search.generator.options(node, resolved, ctx, search.params.options_for(node.type))
        chosen = plan.state.choices.get(nid)
        entries = []
        for rank, option in enumerate(o for o in options if o.success):
            broken = search.broken_incoming(plan.state, nid, option.output)
            entries.append({"rank": rank, "label": option_label(node.type, option.output), "chosen": rank == chosen,
                            "rejected_because": broken[0] if broken else None})
        out[nid] = entries
    return out


async def search_log(search: RepairSearch, outcome: SearchOutcome) -> dict[str, Any]:
    """The search half of the decision log (see the module docstring)."""
    best = outcome.best
    return {
        "policy": outcome.policy,
        "affected": [{"task": nid, "reason": why} for nid, why in (best.state.region.items() if best else [])],
        "alternatives": {
            "complete_plans": len(outcome.plans),
            "feasible_plans": sum(p.report.feasible for p in outcome.plans),
            "considered": [_plan_entry(p) for p in outcome.plans[:CONSIDERED_LIMIT]],
            "options_by_task": await options_on_path(search, best) if best else {},
            "pruned_branches": len(outcome.dead_ends),
            "truncated": outcome.truncated,
        },
        "selected": _plan_entry(best) if best else None,
        "search_budget": outcome.budget.to_dict(),
    }


def decision_log(trip_id: str, strategy: str, recorded_at: str, outcome: dict[str, Any],
                 actions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    The full decision log of one disruption, from its stored record (*outcome*, as written by
    the API) and the Risk Gate actions raised for its event (*actions*, current state).
    """
    replan = outcome.get("replan") or {}
    repair = replan.get("repair") or {}
    searched = repair.get("decision_log")
    event = outcome.get("event") or {}
    reasons = replan.get("stale_reasons") or {}
    affected = (searched or {}).get("affected") or [{"task": nid, "reason": reasons.get(nid, "")}
                                                    for nid in replan.get("stale", [])]
    return {
        "trip_id": trip_id,
        "event_id": event.get("event_id"),
        "recorded_at": recorded_at,
        "strategy": strategy,
        "status": outcome.get("status"),
        "disruption": {"type": event.get("type"), "target": event.get("target_node"), "facts": event.get("new_facts"),
                       "severity": event.get("severity"), "message": outcome.get("message"),
                       "contracts_broken_by_event": outcome.get("broken_edges", [])},
        "affected": affected,
        "alternatives": (searched or {}).get("alternatives") or {
            "searched": False,
            "note": f"strategy '{strategy}' regenerates each task with its agent's single choice; no alternatives "
                    "were compared"},
        "selected": {
            "regenerated": replan.get("regenerated", []),
            "changed": replan.get("changed", []),
            "plan": (searched or {}).get("selected"),
        },
        "validation": replan.get("validation"),
        "conflict": repair.get("conflict") and {k: v for k, v in repair["conflict"].items() if k != "best_effort"}
        | {"best_effort_violations": (repair["conflict"].get("best_effort") or {}).get("violations")},
        "approvals": [{k: a.get(k) for k in ("action_id", "kind", "tier", "node_id", "status", "decision",
                                              "decided_by", "decided_at", "title")} for a in actions],
        "cost": {"latency_ms": outcome.get("latency_ms"), "llm_calls": replan.get("llm_calls"),
                 "api_calls": replan.get("api_calls"), "stages": repair.get("stages"),
                 "search_budget": (searched or {}).get("search_budget")},
        "ga_trace": repair.get("ga_trace"),
    }
