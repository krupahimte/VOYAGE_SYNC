"""
Dependency-aware asynchronous scheduler for the VoyageSync execution engine.

Responsibilities:
  - Walk the existing GraphStore's topological order.
  - Identify tasks whose all required parent dependencies are DONE.
  - Transition eligible nodes: PENDING → READY → RUNNING → DONE | FAILED.
  - Resolve merged input dicts from parent outputs before calling agents.
  - Execute independent READY tasks concurrently up to a configurable limit.
  - Retry retryable failures up to a bounded retry limit.
  - Never execute descendants of FAILED nodes (propagate blocked status).
  - Prevent double-execution via an asyncio lock per node.
  - Persist every status change through the existing GraphStore.

What this module does NOT do:
  - LLM calls, real API calls, Orchestrator decomposition.
  - Disruption Monitor, Risk Gate, approval workflows.
  - SQLite persistence (callers own that layer).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agents.base import AgentRegistry
from app.core.graph import GraphStore
from app.core.models import NodeStatus, TaskNode

logger = logging.getLogger(__name__)


# ── Execution log entry ───────────────────────────────────────────────────────

@dataclass
class ExecutionEvent:
    """Structured log entry for a single scheduler action."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    node_id: str = ""
    event: str = ""          # e.g. "READY", "RUNNING", "DONE", "FAILED", "SKIPPED"
    attempt: int = 1
    detail: str = ""


# ── Scheduler result ──────────────────────────────────────────────────────────

@dataclass
class SchedulerResult:
    """Summary returned after a full scheduler run."""
    done: list[str] = field(default_factory=list)       # node IDs that completed
    failed: list[str] = field(default_factory=list)     # node IDs that failed
    skipped: list[str] = field(default_factory=list)    # node IDs skipped (bad dep)
    log: list[ExecutionEvent] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0 and len(self.skipped) == 0


# ── Scheduler ─────────────────────────────────────────────────────────────────

class Scheduler:
    """
    Asynchronous dependency-aware task scheduler.

    Usage::

        scheduler = Scheduler(registry, max_concurrency=4, max_retries=2)
        result = await scheduler.run(graph_store, context={"graph_id": "g1"})

    Parameters:
        registry:        AgentRegistry mapping NodeType/name → BaseAgent.
        max_concurrency: Maximum number of tasks running concurrently.
        max_retries:     Maximum number of retries for retryable failures.
                         Set to 0 to disable retries.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        max_concurrency: int = 4,
        max_retries: int = 2,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1.")
        self._registry = registry
        self._max_concurrency = max_concurrency
        self._max_retries = max_retries

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(
        self,
        store: GraphStore,
        *,
        context: dict[str, Any] | None = None,
        node_context: dict[str, dict[str, Any]] | None = None,
    ) -> SchedulerResult:
        """
        Execute all PENDING tasks in *store* in dependency order.

        ``node_context`` adds per-node entries to the frozen context (used by
        re-planning to pass each regenerated node its untouched neighbours).

        Already-DONE, FAILED, or RUNNING nodes are not re-executed.
        Returns a SchedulerResult with per-node outcomes and a structured log.
        """
        frozen_context: dict[str, Any] = dict(context or {})
        frozen_context.setdefault("graph_id", store.graph_id)

        result = SchedulerResult()
        semaphore = asyncio.Semaphore(self._max_concurrency)

        # node_id → asyncio.Lock prevents double-execution in concurrent wave
        node_locks: dict[str, asyncio.Lock] = {
            n.id: asyncio.Lock() for n in store.list_nodes()
        }

        # Retry-attempt counter per node (survives across waves)
        retry_counts: dict[str, int] = {}

        # Keep looping until nothing changes (fixed-point convergence)
        while True:
            ready_ids = self._find_ready(store, result)
            if not ready_ids:
                break

            # Launch all ready tasks concurrently, bounded by semaphore
            tasks = [
                self._execute_node(
                    store=store,
                    node_id=nid,
                    result=result,
                    semaphore=semaphore,
                    node_locks=node_locks,
                    retry_counts=retry_counts,
                    frozen_context={**frozen_context, **(node_context or {}).get(nid, {})},
                )
                for nid in ready_ids
            ]
            await asyncio.gather(*tasks)

        # Mark any nodes that couldn't run due to failed dependencies as SKIPPED
        for node in store.list_nodes():
            if node.status == NodeStatus.PENDING and node.id not in result.skipped:
                self._skip_node(store, node.id, result)

        return result

    # ── Wave helper ───────────────────────────────────────────────────────────

    def _find_ready(self, store: GraphStore, result: SchedulerResult) -> list[str]:
        """
        Return IDs of nodes that are PENDING with all dependencies DONE.
        Exclude nodes whose any dependency is FAILED or SKIPPED.
        """
        edges = store.list_edges()

        # Build: dest → set of required source IDs
        required: dict[str, set[str]] = {}
        for edge in edges:
            required.setdefault(edge.destination_id, set()).add(edge.source_id)

        # Build sets for fast membership tests
        failed_or_skipped = set(result.failed) | set(result.skipped)

        ready = []
        for node in store.list_nodes():
            if node.status != NodeStatus.PENDING:
                continue
            deps = required.get(node.id, set())
            if any(d in failed_or_skipped for d in deps):
                # Dependency failed/skipped — will be marked SKIPPED later
                continue
            dep_nodes = [store.get_node(d) for d in deps]
            # A parent awaiting T2 approval already holds its (proposed) output.
            if all(dn.status in (NodeStatus.DONE, NodeStatus.AWAITING_APPROVAL) for dn in dep_nodes):
                ready.append(node.id)

        return ready

    # ── Node execution ────────────────────────────────────────────────────────

    async def _execute_node(
        self,
        store: GraphStore,
        node_id: str,
        result: SchedulerResult,
        semaphore: asyncio.Semaphore,
        node_locks: dict[str, asyncio.Lock],
        retry_counts: dict[str, int],
        frozen_context: dict[str, Any],
    ) -> None:
        """
        Transition a single node PENDING → READY → RUNNING → DONE|FAILED.
        Uses a per-node lock to prevent concurrent duplicate execution.
        """
        lock = node_locks[node_id]
        if lock.locked():
            # Another coroutine is already handling this node
            return
        async with lock:
            node = store.get_node(node_id)
            if node.status != NodeStatus.PENDING:
                # Re-check inside lock: already handled
                return

            # ── READY ──
            store.update_node_status(node_id, NodeStatus.READY, reason="dependencies satisfied")
            result.log.append(ExecutionEvent(node_id=node_id, event="READY"))

            # ── Resolve inputs (own inputs + parent outputs) ──
            resolved = self._resolve_inputs(store, node_id)

            # ── Retrieve agent ──
            try:
                agent = self._registry.get(store.get_node(node_id))
            except KeyError as exc:
                store.update_node_status(
                    node_id, NodeStatus.FAILED,
                    output={"error": str(exc)},
                    reason="no agent registered",
                )
                result.failed.append(node_id)
                result.log.append(ExecutionEvent(node_id=node_id, event="FAILED", detail=str(exc)))
                return

            # ── RUNNING (with retry loop) ──
            attempt = 0
            while True:
                attempt += 1
                retry_counts[node_id] = attempt

                async with semaphore:
                    store.update_node_status(
                        node_id, NodeStatus.RUNNING,
                        reason=f"starting attempt {attempt}",
                    )
                    result.log.append(ExecutionEvent(
                        node_id=node_id, event="RUNNING", attempt=attempt,
                    ))
                    logger.debug("Node %s RUNNING (attempt %d)", node_id, attempt)

                    try:
                        agent_result = await agent.run(
                            store.get_node(node_id), resolved, frozen_context
                        )
                    except Exception as exc:  # noqa: BLE001
                        agent_result_ok = False
                        error_msg = f"Unhandled exception: {exc}"
                        retryable = True
                    else:
                        agent_result_ok = agent_result.success
                        error_msg = agent_result.error
                        retryable = agent_result.retryable

                if agent_result_ok:
                    store.update_node_status(
                        node_id, NodeStatus.DONE,
                        output=agent_result.output,
                        reason=f"completed on attempt {attempt}",
                    )
                    result.done.append(node_id)
                    result.log.append(ExecutionEvent(
                        node_id=node_id, event="DONE", attempt=attempt,
                    ))
                    logger.debug("Node %s DONE", node_id)
                    return

                # Handle failure
                can_retry = retryable and attempt <= self._max_retries
                if can_retry:
                    # Put back to PENDING so the retry loop picks it up
                    store.update_node_status(
                        node_id, NodeStatus.PENDING,
                        reason=f"retrying after attempt {attempt} failure: {error_msg}",
                    )
                    result.log.append(ExecutionEvent(
                        node_id=node_id, event="RETRY", attempt=attempt, detail=error_msg,
                    ))
                    logger.debug("Node %s RETRY (attempt %d)", node_id, attempt)
                    continue  # retry

                # Permanent failure
                store.update_node_status(
                    node_id, NodeStatus.FAILED,
                    output={"error": error_msg, "attempts": attempt},
                    reason=f"failed after {attempt} attempt(s): {error_msg}",
                )
                result.failed.append(node_id)
                result.log.append(ExecutionEvent(
                    node_id=node_id, event="FAILED", attempt=attempt, detail=error_msg,
                ))
                logger.debug("Node %s FAILED: %s", node_id, error_msg)
                return

    # ── Input resolution ──────────────────────────────────────────────────────

    def _resolve_inputs(self, store: GraphStore, node_id: str) -> dict[str, Any]:
        """
        Merge the node's own inputs with all parent nodes' outputs.
        Parent outputs are overlaid on top of node.inputs so downstream
        agents can access upstream data (e.g. flight arrival_time).
        Node's own inputs take priority over parent outputs for key conflicts.

        Because the flat merge loses data when parents share key names (e.g.
        two flights both have ``arrival_time``), every parent's output is also
        available un-merged under ``_parents[parent_id] = {"type", "output"}``.
        """
        edges = store.list_edges()
        parent_ids = [e.source_id for e in edges if e.destination_id == node_id]

        merged: dict[str, Any] = {}
        parents: dict[str, dict[str, Any]] = {}
        for pid in parent_ids:
            parent = store.get_node(pid)
            if parent.output:
                merged.update(parent.output)
            parents[pid] = {"type": parent.type.value, "output": dict(parent.output or {})}

        # Node's own inputs take precedence over inherited parent outputs
        node = store.get_node(node_id)
        merged.update(node.inputs)
        merged["_parents"] = parents
        return merged

    # ── Skip helper ───────────────────────────────────────────────────────────

    def _skip_node(
        self,
        store: GraphStore,
        node_id: str,
        result: SchedulerResult,
    ) -> None:
        store.update_node_status(
            node_id, NodeStatus.FAILED,
            output={"error": "dependency failed or was skipped"},
            reason="skipped: required dependency did not complete",
        )
        result.skipped.append(node_id)
        result.log.append(ExecutionEvent(node_id=node_id, event="SKIPPED"))
        logger.debug("Node %s SKIPPED (dependency failed)", node_id)
