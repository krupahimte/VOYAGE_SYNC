"""
Custom Orchestrator: turns a TripGoal into a Task Dependency Graph and runs it.

Pipeline (plan §6.2, steps 1–2):
  1. Merge user profile & memory into the goal (defaults) and the prompt.
  2. Decompose: the LLM proposes tasks from the fixed vocabulary as JSON.
     The reply is parsed, validated with Pydantic and checked semantically.
     Invalid replies are retried with the validation error in the
     conversation; after ``max_attempts`` failures the deterministic
     template proposal is used instead.
  3. Build: node IDs, inputs, edges and contracts come from templates
     (app.core.templates) — never from LLM text.
  4. Schedule: the existing Scheduler executes the graph.
  5. Check every edge contract against the produced outputs.

Partial re-planning (PROPAGATE, plan §6.2 step 3) lives in app.core.replanner
and is reached through ``Orchestrator.handle_disruption``.

Risk Gate (plan §6.4, app.core.risk_gate): when a caller passes an
``ActionLedger``, planning proposes the bookings as T2 approvals and every
re-plan is reviewed (T0 drafts, T1 notifications with undo, T2 approvals for
re-bookings). Approved actions run through ``approve_action`` only.
No LangGraph, CrewAI or AutoGen; no LLM output is executed.
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.agents.base import AgentRegistry
from app.core.contracts import check_edge
from app.core.graph import GraphError, GraphStore
from app.core.models import NodeType, TaskNode
from app.core.risk_gate import Action, ActionLedger, RiskGate, RiskPolicy
from app.core.scheduler import Scheduler, SchedulerResult
from app.core.templates import (
    CANONICAL_AGENT,
    canonical_id,
    canonical_inputs,
    propose_template_tasks,
    template_edges,
)
from app.integrations.http import UsageMeter
from app.llm.client import LLMClient, LLMError, extract_json_object, get_llm_client
from app.llm.prompts import SYSTEM_PROMPT, build_retry_prompt, build_user_prompt
from app.schemas.trip import ProposedPlan, TripGoal

logger = logging.getLogger(__name__)


# ── Errors and results ────────────────────────────────────────────────────────

class OrchestrationError(Exception):
    """Raised when graph construction or validation fails."""


@dataclass
class DecompositionResult:
    plan: ProposedPlan
    source: str                      # "llm" | "fallback"
    attempts: int
    errors: list[str] = field(default_factory=list)
    llm_calls: int = 0
    provider: str = ""


@dataclass
class EdgeCheck:
    source_id: str
    destination_id: str
    contract: str
    description: str
    holds: bool
    explanation: str


@dataclass
class OrchestratorResult:
    """Result returned by the Orchestrator after planning + execution."""
    trip_id: str
    graph_id: str
    goal: TripGoal
    plan_summary: str
    decomposition: DecompositionResult | None = None
    scheduler_result: SchedulerResult | None = None
    graph_store: GraphStore | None = None
    contract_checks: list[EdgeCheck] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error and self.scheduler_result is not None and self.scheduler_result.success

    @property
    def contracts_holding(self) -> int:
        return sum(1 for c in self.contract_checks if c.holds)


# ── Proposal validation (beyond the Pydantic schema) ─────────────────────────

def validate_proposal(plan: ProposedPlan, goal: TripGoal) -> list[str]:
    """Return human-readable problems with a proposal; empty means valid."""
    errors: list[str] = []
    nights = goal.nights

    ids = [t.task_id for t in plan.tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"duplicate task_id values: {dupes}")

    by_type: dict[NodeType, list] = {t: [] for t in NodeType}
    for task in plan.tasks:
        by_type[task.task_type].append(task)
        t = task.task_type
        if t == NodeType.FLIGHT and task.direction not in ("outbound", "return"):
            errors.append(f"{task.task_id}: FLIGHT needs direction 'outbound' or 'return'")
        if t == NodeType.TRANSFER and task.direction not in ("arrival", "departure"):
            errors.append(f"{task.task_id}: TRANSFER needs direction 'arrival' or 'departure'")
        if t in (NodeType.DAY_PLAN, NodeType.WEATHER):
            if task.day is None or not 1 <= task.day <= nights:
                errors.append(f"{task.task_id}: {t.value} needs day between 1 and {nights}")

    flights = [f.direction for f in by_type[NodeType.FLIGHT]]
    for direction in ("outbound", "return"):
        if flights.count(direction) != 1:
            errors.append(f"need exactly one {direction} FLIGHT, got {flights.count(direction)}")
    if len(by_type[NodeType.STAY]) != 1:
        errors.append(f"need exactly one STAY, got {len(by_type[NodeType.STAY])}")
    if len(by_type[NodeType.BUDGET]) != 1:
        errors.append(f"need exactly one BUDGET, got {len(by_type[NodeType.BUDGET])}")

    canonical = [canonical_id(t) for t in plan.tasks]
    clashes = sorted({c for c in canonical if canonical.count(c) > 1})
    if clashes and not dupes:
        errors.append(f"more than one task for the same role: {clashes}")
    return errors


def apply_profile_defaults(goal: TripGoal, user_context: dict[str, Any] | None) -> TripGoal:
    """Fill gaps in the goal from saved profile preferences (goal values win)."""
    prefs = (user_context or {}).get("preferences") or {}
    updates: dict[str, Any] = {}
    if not goal.interests and prefs.get("interests"):
        updates["interests"] = list(prefs["interests"])
    merged = {k: v for k, v in prefs.items() if k in ("accommodation", "pace")}
    merged.update(goal.preferences)
    if merged != goal.preferences:
        updates["preferences"] = merged
    return goal.model_copy(update=updates) if updates else goal


def check_graph_contracts(store: GraphStore) -> list[EdgeCheck]:
    """Evaluate every edge contract on the current node outputs."""
    checks = []
    for edge in store.list_edges():
        src = store.get_node(edge.source_id)
        dst = store.get_node(edge.destination_id)
        result = check_edge(edge.contract, edge.contract_config, edge.source_id, src.output, dst.output)
        checks.append(EdgeCheck(edge.source_id, edge.destination_id, edge.contract,
                                edge.description, result.holds, result.explanation))
    return checks


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Parameters:
        llm:             LLM client (default: from settings — MockLLM in mock mode).
        registry:        Agent registry (default: the real agents with providers chosen by
                         VOYAGESYNC_MODE; they share this Orchestrator's LLM client).
        max_attempts:    LLM decomposition attempts before template fallback.
        max_concurrency: Scheduler concurrency limit.
        max_retries:     Scheduler retry limit per node.
    """

    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        registry: AgentRegistry | None = None,
        max_attempts: int | None = None,
        max_concurrency: int = 4,
        max_retries: int = 2,
        gate: RiskGate | None = None,
    ) -> None:
        from app.config import get_settings

        from app.agents.providers import build_providers
        from app.agents.registry import build_agent_registry

        self.llm = llm or get_llm_client()
        self.registry = registry or build_agent_registry(providers=build_providers(llm=self.llm))
        self.max_attempts = max(1, max_attempts or get_settings().voyagesync_llm_max_attempts)
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.gate = gate or RiskGate(RiskPolicy.from_settings())

    # ── 1. Decompose ──────────────────────────────────────────────────────────

    async def decompose(self, goal: TripGoal, user_context: dict[str, Any] | None = None) -> DecompositionResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(goal, user_context)},
        ]
        errors: list[str] = []
        calls_before = self.llm.calls

        for attempt in range(1, self.max_attempts + 1):
            try:
                reply = await self.llm.complete(messages, json_mode=True)
            except LLMError as exc:
                errors.append(f"attempt {attempt}: LLM call failed: {exc}")
                logger.warning("Decomposition attempt %d: LLM call failed: %s", attempt, exc)
                continue

            try:
                plan = ProposedPlan.model_validate_json(extract_json_object(reply.text))
                problems = validate_proposal(plan, goal)
                if problems:
                    raise ValueError("; ".join(problems))
            except (ValidationError, ValueError) as exc:
                message = _short_error(exc)
                errors.append(f"attempt {attempt}: {message}")
                logger.info("Decomposition attempt %d rejected: %s", attempt, message)
                messages.append({"role": "assistant", "content": reply.text})
                messages.append({"role": "user", "content": build_retry_prompt(message)})
                continue

            return DecompositionResult(plan, "llm", attempt, errors,
                                       self.llm.calls - calls_before, self.llm.provider)

        logger.warning("LLM decomposition failed %d times; using template graph.", self.max_attempts)
        return DecompositionResult(propose_template_tasks(goal), "fallback", self.max_attempts, errors,
                                   self.llm.calls - calls_before, self.llm.provider)

    # ── 2. Build ──────────────────────────────────────────────────────────────

    def build_graph(self, plan: ProposedPlan, goal: TripGoal, graph_id: str) -> GraphStore:
        """Nodes from the proposal; IDs, inputs, edges and contracts from templates."""
        problems = validate_proposal(plan, goal)
        if problems:
            raise OrchestrationError("Invalid plan: " + "; ".join(problems))

        store = GraphStore(graph_id)
        present: dict[str, NodeType] = {}
        try:
            for task in plan.tasks:
                nid = canonical_id(task)
                store.add_node(TaskNode(
                    id=nid,
                    type=task.task_type,
                    agent=CANONICAL_AGENT[task.task_type],
                    inputs=canonical_inputs(task, goal),
                ))
                present[nid] = task.task_type
            for edge in template_edges(present):
                store.add_edge(edge)
            store.validate()
        except GraphError as exc:
            raise OrchestrationError(f"Graph construction failed: {exc}") from exc
        return store

    # ── 3. Schedule ───────────────────────────────────────────────────────────

    async def execute(self, store: GraphStore, context: dict[str, Any] | None = None) -> SchedulerResult:
        scheduler = Scheduler(self.registry, max_concurrency=self.max_concurrency, max_retries=self.max_retries)
        return await scheduler.run(store, context=context)

    # ── Re-plan after a disruption ────────────────────────────────────────────

    async def handle_disruption(self, store: GraphStore, event: Any, *, strategy: str = "contract",
                                use_frozen_context: bool = True, context: dict[str, Any] | None = None,
                                ledger: ActionLedger | None = None):
        """
        Apply a DisruptionEvent and re-plan (contract PROPAGATE, or the B1/B2
        baselines). With a ledger, the Risk Gate reviews what the re-plan changed;
        the same gate runs after every strategy.
        """
        from app.core.monitor.monitor import DisruptionMonitor
        from app.core.replanner import Replanner

        monitor = DisruptionMonitor(Replanner(self.registry, max_concurrency=self.max_concurrency,
                                              max_retries=self.max_retries))
        before = {n.id: copy.deepcopy(n.output) for n in store.list_nodes()}
        outcome = await monitor.handle(store, event, strategy=strategy, use_frozen_context=use_frozen_context,
                                       context=context)
        if ledger is not None and outcome.status in ("replanned", "no_effect"):
            outcome.actions = self.gate.review_replan(
                store, ledger, before, outcome.replan.changed if outcome.replan else [],
                why=event.summary(), event_id=event.event_id)
        elif ledger is not None and outcome.status == "conflict":
            outcome.actions = [self.gate.raise_decision(store, ledger, outcome.replan.repair["conflict"],
                                                        why=event.summary(), event_id=event.event_id)]
        return outcome

    # ── Risk Gate decisions ───────────────────────────────────────────────────

    def executor(self):
        """Executor for approved actions (sandbox booking through the providers), or None."""
        from app.agents.actions import ActionExecutor

        providers = getattr(self.registry, "providers", None)
        return ActionExecutor(providers) if providers is not None else None

    async def approve_action(self, store: GraphStore, ledger: ActionLedger, action_id: str,
                             *, decided_by: str = "user") -> Action:
        return await self.gate.approve(store, ledger, action_id, self.executor(), decided_by=decided_by)

    def reject_action(self, store: GraphStore, ledger: ActionLedger, action_id: str,
                      *, decided_by: str = "user") -> Action:
        return self.gate.reject(store, ledger, action_id, decided_by=decided_by)

    # ── Full pipeline ─────────────────────────────────────────────────────────

    async def plan_trip(
        self,
        goal: TripGoal,
        user_context: dict[str, Any] | None = None,
        *,
        trip_id: str | None = None,
        ledger: ActionLedger | None = None,
    ) -> OrchestratorResult:
        trip_id = trip_id or uuid.uuid4().hex[:12]
        graph_id = f"trip_{trip_id}"
        goal = apply_profile_defaults(goal, user_context)
        providers = getattr(self.registry, "providers", None)
        api_before = providers.usage.snapshot() if providers else {}
        llm_before = self.llm.calls

        decomposition = await self.decompose(goal, user_context)
        try:
            store = self.build_graph(decomposition.plan, goal, graph_id)
        except OrchestrationError as exc:
            return OrchestratorResult(trip_id, graph_id, goal, decomposition.plan.summary,
                                      decomposition=decomposition, error=str(exc))

        sched = await self.execute(store, {"trip_id": trip_id, "graph_id": graph_id})
        actions: list[Action] = []
        if ledger is not None:
            ledger.trip_id = ledger.trip_id or trip_id
            actions = self.gate.propose_bookings(store, ledger)
        summary = decomposition.plan.summary or propose_template_tasks(goal).summary
        api_calls = UsageMeter.diff(providers.usage.snapshot(), api_before) if providers else {}
        agent_llm = getattr(providers, "llm", None) if providers else None
        usage = {
            "llm_calls": self.llm.calls - llm_before
                         + (agent_llm.calls if agent_llm is not None and agent_llm is not self.llm else 0),
            "api_calls": api_calls,
            "api_calls_total": sum(api_calls.values()),
            "provider_modes": dict(providers.modes) if providers else {},
        }
        return OrchestratorResult(
            trip_id=trip_id,
            graph_id=graph_id,
            goal=goal,
            plan_summary=summary,
            decomposition=decomposition,
            scheduler_result=sched,
            graph_store=store,
            contract_checks=check_graph_contracts(store),
            usage=usage,
            actions=actions,
        )


def _short_error(exc: Exception) -> str:
    """Compact validation message suitable for feeding back to the LLM."""
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8]]
        return "schema errors — " + "; ".join(parts)
    return str(exc)[:800]
