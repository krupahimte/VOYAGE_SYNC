"""
Committing a repair atomically, conflicts, and the entry point the Replanner calls.

The chosen plan is written to a deep copy of the store: region tasks are marked
STALE, then DONE with their chosen output (day plans first get their activity
descriptions, the one LLM call per new plan, as in E1). The copy is validated
again with the full hard constraints; only if it passes does it replace the
store (``GraphStore.restore_from``). On any failure the store is left exactly
as it was.

When no feasible plan exists (or the commit fails), nothing is committed and the
outcome carries a **conflict** (paper §V): what is violated, and a best-effort
plan (``search.best_effort``) that the user may accept explicitly through the
Risk Gate (a T2 DECIDE action), or reject to keep the plan as it is.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

from app.agents.itinerary import PENDING_DESCRIPTIONS
from app.core.graph import GraphStore
from app.core.models import NodeStatus
from app.core.repair.constraints import ValidationReport, goal_from_graph, validate_plan
from app.core.repair.decision import search_log
from app.core.repair.policies import PolicyParams, policies
from app.core.repair.search import (Plan, RepairSearch, SearchOutcome, SearchParams, best_effort, exhaustive,
                                    greedy)
from app.core.replanner import _IDENTITY_KEYS
from app.core.scheduler import Scheduler

POLICIES = {
    "rank": lambda search: greedy(search, by="rank"),
    "greedy": lambda search: greedy(search, by="objective"),
    "exhaustive": exhaustive,
}


class CommitError(Exception):
    """The chosen plan did not pass validation once written; nothing was committed."""


def outputs_digest(graph: Any) -> str:
    """A hash of every task's output: proves a stored plan was built on the graph as it is now."""
    text = json.dumps({n.id: n.output for n in graph.list_nodes()}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def materialize(search: RepairSearch, plan: Plan) -> dict[str, dict[str, Any]]:
    """The plan's region outputs, with the descriptions of new day-plan activities written."""
    outputs = {}
    for nid in [n for n in search.order if n in plan.state.region]:
        output = plan.state.view.output(nid)
        if PENDING_DESCRIPTIONS in (output or {}):
            node = search.store.get_node(nid)
            output = await search.registry.get(node).complete_descriptions(
                Scheduler._resolve_inputs(None, plan.state.view, nid), plan.state.contexts.get(nid, {}), output)
        outputs[nid] = output
    return outputs


def write_plan(store: GraphStore, region: dict[str, str], outputs: dict[str, dict[str, Any]],
               reason: Callable[[str], str]) -> GraphStore:
    """
    A copy of *store* with *outputs* written to the region (STALE, then DONE), pinned facts kept
    while the entity is the same one and dropped otherwise (E1's ``_reapply_facts``).
    """
    work = copy.deepcopy(store)
    for nid, why in region.items():
        work.update_node_status(nid, NodeStatus.STALE, reason=f"stale: {why}")
    for nid, output in outputs.items():
        node = work.get_node(nid)
        work.update_node_status(nid, NodeStatus.DONE, output=output, reason=reason(nid))
        facts = node.inputs.get("facts")
        previous = store.get_node(nid).output or {}
        if facts and not all(output.get(k) == previous.get(k) for k in _IDENTITY_KEYS
                             if k in output or k in previous):
            work.set_node_inputs(nid, {k: v for k, v in node.inputs.items() if k != "facts"})
    return work


async def commit(search: RepairSearch, plan: Plan, policy: str) -> list[str]:
    """Write *plan* to the store atomically; return the region tasks whose output changed."""
    store = search.store
    outputs = await materialize(search, plan)
    work = write_plan(store, plan.state.region, outputs,
                      reason=lambda nid: f"repair ({policy}): option {plan.state.choices.get(nid, 0)}")
    report = validate_plan(work, store, plan.state.region, search.goal)
    leftover = [nid for nid in outputs if PENDING_DESCRIPTIONS in (work.get_node(nid).output or {})]
    if not report.feasible or leftover:
        raise CommitError("; ".join(report.violations() + [f"{n}: descriptions missing" for n in leftover]))
    before = {nid: store.get_node(nid).output for nid in outputs}
    store.restore_from(work)
    return [nid for nid in outputs if store.get_node(nid).output != before[nid]]


async def conflict_report(search: RepairSearch, reason: str) -> dict[str, Any]:
    """What the user is asked to decide when nothing feasible can be committed."""
    plan = await best_effort(search)
    offer = None
    if plan is not None and plan.state.region:
        offer = {"region": dict(plan.state.region), "choices": dict(plan.state.choices),
                 "outputs": await materialize(search, plan), "violations": plan.report.violations(),
                 "fitness": plan.score.fitness, "terms": dict(plan.score.terms)}
    options = [{"id": "keep_current", "decision": "reject",
                "description": "Keep the plan as it is after the disruption; nothing is regenerated."}]
    if offer is not None:
        options.insert(0, {"id": "accept_best_effort", "decision": "approve",
                           "description": "Apply the best available plan, accepting the listed violations.",
                           "violates": offer["violations"]})
    return {"reason": reason, "target": search.target, "trigger": search.trigger,
            "violations": offer["violations"] if offer else [], "best_effort": offer, "options": options,
            "base_digest": outputs_digest(search.store)}


async def repair(registry: Any, store: GraphStore, target: str, trigger: str, *, policy: str,
                 params: SearchParams = SearchParams(), event_id: str = "",
                 policy_params: PolicyParams = PolicyParams(),
                 also: tuple[tuple[str, str], ...] = ()) -> tuple[SearchOutcome, list[str], str]:
    """
    Search for a repair with *policy* and commit the best feasible plan.
    Returns the search outcome, the changed tasks and the commit error ('' when committed or
    when no feasible plan was found). Without a commit, ``outcome.conflict`` says why and what
    the user can choose. *also*: further (target, trigger) pairs of a combined disruption.
    """
    limit = params.max_exhaustive if policy == "exhaustive" else None
    search = RepairSearch(registry, store, target, trigger, params=params, event_id=event_id,
                          max_plan_evaluations=limit, also=also)
    meter = _Meter(registry)
    outcome = await {**POLICIES, **policies(policy_params)}[policy](search)
    stages = {"search": meter.lap()}
    outcome.decision_log = await search_log(search, outcome)
    changed, error = [], ""
    if outcome.feasible:
        try:
            changed = await commit(search, outcome.best, policy)
            stages["commit"] = meter.lap()
        except CommitError as exc:
            error = str(exc)
            outcome.conflict = await conflict_report(search, f"commit failed: {exc}")
            stages["conflict"] = meter.lap()
    else:
        reason = "no feasible plan" + (" within the evaluation budget" if outcome.truncated else "")
        outcome.conflict = await conflict_report(search, reason)
        stages["conflict"] = meter.lap()
    outcome.stages = stages
    return outcome, changed, error


class _Meter:
    """Wall-clock time, LLM calls and provider calls per stage of a repair (for the decision log)."""

    def __init__(self, registry: Any) -> None:
        self.providers = getattr(registry, "providers", None)
        self._mark = self._read()

    def _read(self) -> tuple[float, int, dict[str, int]]:
        import time

        llm = getattr(self.providers, "llm", None)
        usage = self.providers.usage.snapshot() if self.providers is not None else {}
        return time.perf_counter(), (llm.calls if llm is not None else 0), usage

    def lap(self) -> dict[str, Any]:
        from app.integrations.http import UsageMeter

        now = self._read()
        (t0, llm0, api0), (t1, llm1, api1) = self._mark, now
        self._mark = now
        return {"ms": round((t1 - t0) * 1000, 2), "llm_calls": llm1 - llm0, "api_calls": UsageMeter.diff(api1, api0)}


def accept_best_effort(store: GraphStore, conflict: dict[str, Any], *, action_id: str) -> tuple[list[str], list[str]]:
    """
    Commit a conflict's best-effort plan after the user accepted it. The graph must be exactly
    the one the plan was built on, and the plan must violate exactly what the user was shown.
    Returns (changed tasks, accepted violations); raises ValueError otherwise (nothing committed).
    """
    offer = conflict.get("best_effort")
    if not offer:
        raise ValueError("this conflict has no plan to accept")
    if outputs_digest(store) != conflict.get("base_digest"):
        raise ValueError("the plan changed since the conflict was raised")
    work = write_plan(store, offer["region"], offer["outputs"],
                      reason=lambda nid: f"accepted best-effort plan ({action_id})")
    report: ValidationReport = validate_plan(work, store, offer["region"], goal_from_graph(store))
    if sorted(report.violations()) != sorted(offer["violations"]):
        raise ValueError("the plan no longer violates exactly what was shown")
    before = {nid: store.get_node(nid).output for nid in offer["outputs"]}
    store.restore_from(work)
    return [nid for nid in offer["outputs"] if store.get_node(nid).output != before[nid]], report.violations()
