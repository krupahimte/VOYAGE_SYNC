"""
Search over repairs: the contract-pruned BFS of E1, run inside every branch, with
a choice among each task's options (paper §V.A–B).

A search state is a partial repair: a GraphView with the outputs chosen so far,
the repair region (task → why it is stale) and the region tasks still to choose,
processed by longest-path depth like E1. Choosing an option for a task:

  1. checks its incoming contracts against its settled parents — a broken one prunes the option;
  2. if its output differs from the reference, checks its outgoing contracts against the
     frozen children — a broken one adds that child to the region (E1's rule). A child that
     may not be regenerated (the disrupted task under a facts event, R7) prunes instead.

Tasks outside the region are never given an output, so they stay frozen by construction.
The budget is an ordinary region task with one option (it is derived), so its cost
terms are exact. Agents get the same frozen context E1 gives them: their previous
output and their neighbours that were not stale when their depth level began.

Policies (pre-registration arms):
  rank        — first option that survives the contract checks (used to check parity with E1)
  greedy      — E1g: the option with the best partial objective at each task
  exhaustive  — EXH: every combination, up to the evaluation budget
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import AgentResult
from app.core.contracts import check_edge
from app.core.graph import GraphStore
from app.core.models import NodeType, TaskNode
from app.core.repair.constraints import ValidationReport, goal_from_graph, validate_plan
from app.core.repair.objective import ObjectiveWeights, Score, deb_key, score
from app.core.repair.view import GraphView
from app.core.replanner import _IDENTITY_KEYS, _depths
from app.core.scheduler import Scheduler

DEFAULT_K = {NodeType.FLIGHT: 3, NodeType.STAY: 3, NodeType.TRANSFER: 4, NodeType.DAY_PLAN: 3}


@dataclass(frozen=True)
class SearchParams:
    k: dict[NodeType, int] = field(default_factory=lambda: dict(DEFAULT_K))   # options per task type (others: 1)
    max_plan_evaluations: int = 500         # complete plans validated and scored (the compute budget, R8)
    max_exhaustive: int = 10000             # complete plans the exhaustive reference may enumerate
    timeout_s: float = 2.0                  # safety only; a run that hits it is flagged
    weights: ObjectiveWeights = ObjectiveWeights()

    def options_for(self, node_type: NodeType) -> int:
        return self.k.get(node_type, 1)


@dataclass
class EvalBudget:
    """Counts the work a search does; ``max_plan_evaluations`` is the hardware-independent budget."""
    max_plan_evaluations: int
    timeout_s: float
    started: float = field(default_factory=time.perf_counter)
    plan_evaluations: int = 0
    partial_evaluations: int = 0
    option_generations: int = 0
    option_cache_hits: int = 0
    timed_out: bool = False

    @property
    def exhausted(self) -> bool:
        if time.perf_counter() - self.started > self.timeout_s:
            self.timed_out = True
        return self.timed_out or self.plan_evaluations >= self.max_plan_evaluations

    def to_dict(self) -> dict[str, Any]:
        return {"max_plan_evaluations": self.max_plan_evaluations, "plan_evaluations": self.plan_evaluations,
                "partial_evaluations": self.partial_evaluations, "option_generations": self.option_generations,
                "option_cache_hits": self.option_cache_hits, "timed_out": self.timed_out}


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OptionGenerator:
    """Agents' options, cached by (task, k, resolved inputs, frozen context) for one repair run."""

    def __init__(self, registry: Any, budget: EvalBudget) -> None:
        self.registry, self.budget = registry, budget
        self._cache: dict[tuple, list[AgentResult]] = {}

    async def options(self, node: TaskNode, resolved: dict[str, Any], ctx: dict[str, Any], k: int) -> list[AgentResult]:
        key = (node.id, k, _digest(resolved), _digest(ctx))
        if key in self._cache:
            self.budget.option_cache_hits += 1
            return self._cache[key]
        self.budget.option_generations += 1
        try:
            found = await self.registry.get(node).candidates(node, resolved, ctx, k)
        except Exception as exc:  # noqa: BLE001 — an agent error means "no options", never a crash
            found = [AgentResult.fail(f"Unhandled exception: {exc}", retryable=False)]
        self._cache[key] = found
        return found


@dataclass
class SearchState:
    view: GraphView
    region: dict[str, str]                         # task → why it is stale, in the order found
    pending: tuple[str, ...]                       # region tasks still to choose
    choices: dict[str, int] = field(default_factory=dict)        # task → rank of the chosen option
    contexts: dict[str, dict[str, Any]] = field(default_factory=dict)   # task → frozen context it was given
    level: int = -1
    frozen_exclude: frozenset[str] = frozenset()   # region at the start of the current depth level

    @property
    def complete(self) -> bool:
        return not self.pending

    def signature(self) -> tuple:
        return tuple(sorted(self.choices.items()))


@dataclass
class Plan:
    state: SearchState
    report: ValidationReport
    score: Score

    @property
    def key(self) -> tuple:
        return deb_key(self.report, self.score.fitness) + (self.state.signature(),)


@dataclass
class SearchOutcome:
    policy: str
    best: Plan | None
    plans: list[Plan]                              # every complete plan evaluated, best first
    budget: EvalBudget
    dead_ends: list[str] = field(default_factory=list)   # why branches were pruned
    truncated: bool = False
    conflict: dict[str, Any] | None = None               # set when nothing feasible could be committed

    @property
    def feasible(self) -> bool:
        return self.best is not None and self.best.report.feasible

    def summary(self) -> dict[str, Any]:
        best = self.best
        return {
            "policy": self.policy,
            "feasible": self.feasible,
            "region": list(best.state.region) if best else [],
            "stale_reasons": dict(best.state.region) if best else {},
            "choices": dict(best.state.choices) if best else {},
            "fitness": best.score.fitness if best else None,
            "terms": dict(best.score.terms) if best else {},
            "violations": best.report.violations() if best else [],
            "complete_plans": len(self.plans),
            "feasible_plans": sum(p.report.feasible for p in self.plans),
            "pruned": len(self.dead_ends),
            "dead_ends": self.dead_ends[:20],
            "truncated": self.truncated,
            "budget": self.budget.to_dict(),
            "conflict": self.conflict,
        }


class RepairSearch:
    """One repair problem: the store after the event, the disrupted task and how it was disrupted."""

    def __init__(self, registry: Any, store: GraphStore, target: str, trigger: str, *,
                 params: SearchParams = SearchParams(), event_id: str = "", goal: Any = None,
                 max_plan_evaluations: int | None = None, also: tuple[tuple[str, str], ...] = ()) -> None:
        self.registry, self.store, self.target, self.trigger = registry, store, target, trigger
        # The disrupted tasks, one per event: *also* holds further (target, trigger) pairs of a
        # combined disruption (several events repaired at once). A single event is just (target, trigger).
        self.targets: list[tuple[str, str]] = [(target, trigger), *also]
        self.params = params
        self.goal = goal if goal is not None else goal_from_graph(store)
        self.budget = EvalBudget(max_plan_evaluations or params.max_plan_evaluations, params.timeout_s)
        self.generator = OptionGenerator(registry, self.budget)
        self.order, self.depth = _depths(store)
        self.position = {nid: i for i, nid in enumerate(self.order)}
        self.in_edges: dict[str, list] = {nid: [] for nid in self.order}
        self.out_edges: dict[str, list] = {nid: [] for nid in self.order}
        for e in store.list_edges():
            self.in_edges[e.destination_id].append(e)
            self.out_edges[e.source_id].append(e)
        self.fixed = {t for t, k in self.targets if k == "facts"}    # R7: the facts are reality
        self.base_context = {"graph_id": store.graph_id, "replan_event": event_id}
        self.dead_ends: list[str] = []

    @property
    def target_ids(self) -> set[str]:
        return {t for t, _ in self.targets}

    # ── states ────────────────────────────────────────────────────────────────

    def initial(self, *, relaxed: bool = False) -> SearchState | None:
        """
        Invalidated targets join the region; each facts target expands E1's way (its broken outgoing
        contracts add children). *relaxed* (best effort) does not prune on a fixed child.
        """
        state = SearchState(GraphView(self.store), {}, ())
        for t, k in self.targets:
            if k == "invalidate" and t not in state.region:
                state = self._add(state, t, "invalidated by disruption")
        for t, k in self.targets:
            if k == "facts":
                state = self._expand(state, t, force=True, relaxed=relaxed)
                if state is None:
                    return None
        return state

    def _sorted(self, nids) -> tuple[str, ...]:
        return tuple(sorted(nids, key=lambda n: (self.depth[n], self.position[n])))

    def _add(self, state: SearchState, nid: str, reason: str) -> SearchState:
        return SearchState(state.view, {**state.region, nid: reason}, self._sorted((*state.pending, nid)),
                           state.choices, state.contexts, state.level, state.frozen_exclude)

    def _expand(self, state: SearchState, nid: str, *, force: bool = False,
                relaxed: bool = False) -> SearchState | None:
        """
        E1's rule: if *nid* changed, every frozen child whose contract now breaks joins the region.
        A broken contract into a fixed child prunes, unless *relaxed* (best effort), which keeps going.
        """
        out = state.view.output(nid)
        if not force and out == self.store.get_node(nid).output:
            return state
        for e in self.out_edges[nid]:
            child = e.destination_id
            if child in state.region:
                continue
            check = check_edge(e.contract, e.contract_config, nid, out, state.view.output(child))
            if not check.holds:
                if child in self.fixed:
                    if relaxed:
                        continue
                    self.dead_ends.append(f"{nid} breaks {e.contract} into fixed {child}")
                    return None
                state = self._add(state, child, f"{e.contract} broken by {nid}: {check.explanation}")
        return state

    def _context(self, state: SearchState, nid: str) -> tuple[SearchState, dict[str, Any]]:
        """The frozen context E1's scheduler would give *nid* (previous output + non-stale neighbours)."""
        if self.depth[nid] > state.level:
            state = SearchState(state.view, state.region, state.pending, state.choices, state.contexts,
                                self.depth[nid], frozenset(state.region))
        neighbours = {e.source_id for e in self.in_edges[nid]} | {e.destination_id for e in self.out_edges[nid]}
        ctx = {**self.base_context, "replan": True, "previous_output": self.store.get_node(nid).output,
               "frozen_neighbours": {m: state.view.output(m) for m in sorted(neighbours)
                                     if m not in state.frozen_exclude}}
        return state, ctx

    async def options(self, state: SearchState) -> tuple[SearchState, str, dict[str, Any], list[AgentResult]]:
        nid = state.pending[0]
        state, ctx = self._context(state, nid)
        node = state.view.get_node(nid)
        resolved = Scheduler._resolve_inputs(None, state.view, nid)
        found = await self.generator.options(node, resolved, ctx, self.params.options_for(node.type))
        ok = [r for r in found if r.success]
        if not ok:
            self.dead_ends.append(f"{nid}: no option ({found[0].error if found else 'none'})")
        return state, nid, ctx, ok

    def broken_incoming(self, state: SearchState, nid: str, output: dict[str, Any]) -> list[str]:
        out = self._with_facts(nid, output)
        broken = []
        for e in self.in_edges[nid]:
            check = check_edge(e.contract, e.contract_config, e.source_id, state.view.output(e.source_id), out)
            if not check.holds:
                broken.append(f"{e.contract} from {e.source_id}: {check.explanation}")
        return broken

    def choose(self, state: SearchState, nid: str, rank: int, output: dict[str, Any],
               ctx: dict[str, Any], *, relaxed: bool = False) -> SearchState | None:
        """
        Assign option *rank* to *nid*; None when a contract check prunes it. *relaxed* (best
        effort only) never prunes: broken contracts stay in the plan and its validation report.
        """
        if not relaxed:
            broken = self.broken_incoming(state, nid, output)
            if broken:
                self.dead_ends.append(f"{nid} option {rank}: {broken[0]}")
                return None
        out = self._with_facts(nid, output)
        state = SearchState(state.view.with_output(nid, out), state.region, state.pending[1:],
                            {**state.choices, nid: rank}, {**state.contexts, nid: ctx}, state.level,
                            state.frozen_exclude)
        return self._expand(state, nid, relaxed=relaxed)

    def skip(self, state: SearchState) -> SearchState:
        """Best effort with no option at all: the task keeps its current output (and its violations)."""
        return SearchState(state.view, state.region, state.pending[1:], state.choices, state.contexts,
                           state.level, state.frozen_exclude)

    def _with_facts(self, nid: str, output: dict[str, Any]) -> dict[str, Any]:
        """As E1's ``_reapply_facts``: pinned facts stay while the entity is the same one."""
        node = self.store.get_node(nid)
        facts = node.inputs.get("facts")
        if not facts or not output:
            return output
        previous = node.output or {}
        same = all(output.get(k) == previous.get(k) for k in _IDENTITY_KEYS if k in output or k in previous)
        return {**output, **facts} if same else output

    # ── evaluation ────────────────────────────────────────────────────────────

    def evaluate(self, state: SearchState) -> Plan:
        self.budget.plan_evaluations += 1
        report = validate_plan(state.view, self.store, state.region, self.goal)
        return Plan(state, report, score(self.store, state.view, self.target_ids, self.params.weights))

    def partial_fitness(self, state: SearchState) -> float:
        self.budget.partial_evaluations += 1
        return score(self.store, state.view, self.target_ids, self.params.weights).fitness

    def outcome(self, policy: str, plans: list[Plan], truncated: bool = False) -> SearchOutcome:
        plans = sorted(plans, key=lambda p: p.key)
        return SearchOutcome(policy, plans[0] if plans else None, plans, self.budget, list(self.dead_ends),
                             truncated)


# ── Policies ──────────────────────────────────────────────────────────────────

async def greedy(search: RepairSearch, *, by: str = "objective") -> SearchOutcome:
    """
    One option per task, in depth order. ``by="rank"`` takes the first option that survives
    the contract checks; ``by="objective"`` (E1g) the one with the best partial fitness,
    ties going to the lower rank. A task with no surviving option is a dead end.
    """
    state = search.initial()
    while state is not None and not state.complete:
        state, nid, ctx, options = await search.options(state)
        best: tuple[float, int, SearchState] | None = None
        for rank, option in enumerate(options):
            child = search.choose(state, nid, rank, option.output, ctx)
            if child is None:
                continue
            if by == "rank":
                best = (0.0, rank, child)
                break
            candidate = (-search.partial_fitness(child), rank, child)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        state = best[2] if best else None
    plans = [search.evaluate(state)] if state is not None else []
    return search.outcome("rank" if by == "rank" else "greedy", plans)


async def best_effort(search: RepairSearch) -> Plan | None:
    """
    The plan offered when no feasible one exists: E1's behaviour made explicit. Each task takes
    the option with the fewest broken incoming contracts (ties to the lower rank) and nothing is
    pruned, so the plan is complete and its validation report names every violation. It is
    only ever committed if the user accepts it (Risk Gate DECIDE).
    """
    state = search.initial(relaxed=True)
    while not state.complete:
        state, nid, ctx, options = await search.options(state)
        if not options:
            state = search.skip(state)
            continue
        rank = min(range(len(options)),
                   key=lambda r: (len(search.broken_incoming(state, nid, options[r].output)), r))
        state = search.choose(state, nid, rank, options[rank].output, ctx, relaxed=True)
    return search.evaluate(state)


async def exhaustive(search: RepairSearch) -> SearchOutcome:
    """Every combination of options that survives the contract checks (depth first), up to the budget."""
    plans: list[Plan] = []
    truncated = False

    async def visit(state: SearchState) -> None:
        nonlocal truncated
        if search.budget.exhausted:
            truncated = True
            return
        if state.complete:
            plans.append(search.evaluate(state))
            return
        state, nid, ctx, options = await search.options(state)
        for rank, option in enumerate(options):
            child = search.choose(state, nid, rank, option.output, ctx)
            if child is not None:
                await visit(child)

    start = search.initial()
    if start is not None:
        await visit(start)
    return search.outcome("exhaustive", plans, truncated)
