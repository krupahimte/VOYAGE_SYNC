"""
Partial re-planning (plan §6.2): contract-checked PROPAGATE, plus the two
baselines behind the same interface.

  contract (ours) — start at the changed node; a child becomes STALE only if
                    the edge contract breaks; regenerate STALE nodes level by
                    level in topological order and re-check *their* outgoing
                    contracts against the new outputs; stop where contracts
                    hold. Regenerated agents get their untouched neighbours
                    (and their own previous output) as frozen context.
  full (B1)       — discard everything and regenerate every node (except
                    disruption facts, which are reality), no frozen context.
  naive (B2)      — mark every descendant of the changed node STALE without
                    checking contracts and regenerate them.

Processing by depth level means a node is regenerated at most once: every
parent sits on a lower level and is settled before the node is considered.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

from app.agents.base import AgentRegistry
from app.core.contracts import check_edge
from app.core.graph import GraphStore
from app.core.models import NodeStatus
from app.core.scheduler import Scheduler
from app.integrations.http import UsageMeter

# Identity of the entity a node describes; pinned facts stay valid only while it is unchanged.
_IDENTITY_KEYS = ("offer_id", "hotel_id", "date")


class Strategy(str, Enum):
    CONTRACT = "contract"
    FULL = "full"
    NAIVE = "naive"


@dataclass
class ReplanResult:
    strategy: str
    target: str
    trigger: Literal["facts", "invalidate"]
    event_id: str = ""
    stale: list[str] = field(default_factory=list)          # marked STALE, in order
    regenerated: list[str] = field(default_factory=list)    # re-run successfully
    changed: list[str] = field(default_factory=list)        # regenerated with a different output
    failed: list[str] = field(default_factory=list)
    stale_reasons: dict[str, str] = field(default_factory=dict)
    contract_checks: int = 0
    levels: int = 0
    latency_ms: float = 0.0
    llm_calls: int = 0
    api_calls: dict[str, int] = field(default_factory=dict)
    contracts_total: int = 0
    contracts_holding: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _depths(store: GraphStore) -> tuple[list[str], dict[str, int]]:
    order = store.validate()
    parents: dict[str, list[str]] = {nid: [] for nid in order}
    for e in store.list_edges():
        parents[e.destination_id].append(e.source_id)
    depth: dict[str, int] = {}
    for nid in order:
        depth[nid] = 1 + max((depth[p] for p in parents[nid]), default=-1)
    return order, depth


def _descendants(store: GraphStore, root: str) -> list[str]:
    children: dict[str, list[str]] = {}
    for e in store.list_edges():
        children.setdefault(e.source_id, []).append(e.destination_id)
    seen, stack = set(), [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return [nid for nid in store.validate() if nid in seen]


class Replanner:
    def __init__(self, registry: AgentRegistry, *, max_concurrency: int = 4, max_retries: int = 2) -> None:
        self.registry = registry
        self.scheduler = Scheduler(registry, max_concurrency=max_concurrency, max_retries=max_retries)

    async def replan(
        self,
        store: GraphStore,
        target: str,
        trigger: Literal["facts", "invalidate"],
        *,
        strategy: Strategy | str = Strategy.CONTRACT,
        use_frozen_context: bool = True,
        event_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> ReplanResult:
        """
        Re-plan after ``target`` changed. ``trigger="facts"``: the target's
        output already holds the new facts; ``"invalidate"``: the target itself
        must be regenerated.
        """
        strategy = Strategy(strategy)
        result = ReplanResult(strategy=strategy.value, target=target, trigger=trigger, event_id=event_id)
        providers = getattr(self.registry, "providers", None)
        api_before = providers.usage.snapshot() if providers else {}
        llm = getattr(providers, "llm", None)
        llm_before = llm.calls if llm is not None else 0
        started = time.perf_counter()
        ctx = {**(context or {}), "graph_id": store.graph_id, "replan_event": event_id}

        if strategy == Strategy.CONTRACT:
            await self._contract(store, target, trigger, result, use_frozen_context, ctx)
        else:
            if strategy == Strategy.FULL:
                pinned = {n.id for n in store.list_nodes() if n.inputs.get("facts")}
                nodes = [nid for nid in store.validate()
                         if nid not in pinned and not (trigger == "facts" and nid == target)]
                frozen = False
                reason = "full replan (B1)"
            else:
                nodes = ([target] if trigger == "invalidate" else []) + _descendants(store, target)
                frozen = use_frozen_context
                reason = f"descendant of {target} (naive B2)"
            for nid in nodes:
                self._mark_stale(store, nid, reason, result)
            await self._regenerate(store, nodes, result, frozen, ctx)
            result.levels = 1

        result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if providers:
            result.api_calls = UsageMeter.diff(providers.usage.snapshot(), api_before)
        if llm is not None:
            result.llm_calls = llm.calls - llm_before
        checks = [check_edge(e.contract, e.contract_config, e.source_id,
                             store.get_node(e.source_id).output, store.get_node(e.destination_id).output).holds
                  for e in store.list_edges()]
        result.contracts_total, result.contracts_holding = len(checks), sum(checks)
        return result

    # ── contract-checked PROPAGATE ────────────────────────────────────────────

    async def _contract(self, store, target, trigger, result, use_frozen_context, ctx) -> None:
        order, depth = _depths(store)
        dirty: set[str] = set()
        if trigger == "invalidate":
            self._mark_stale(store, target, "invalidated by disruption", result)
        else:
            dirty.add(target)

        out_edges: dict[str, list] = {}
        for e in store.list_edges():
            out_edges.setdefault(e.source_id, []).append(e)

        levels = sorted(set(depth.values()))
        for level in levels:
            level_nodes = [nid for nid in order if depth[nid] == level]
            to_run = [nid for nid in level_nodes if nid in result.stale_reasons]
            if to_run:
                result.levels += 1
                changed = await self._regenerate(store, to_run, result, use_frozen_context, ctx)
                dirty |= changed
            for nid in level_nodes:
                if nid not in dirty:
                    continue
                src = store.get_node(nid)
                for edge in out_edges.get(nid, []):
                    child = edge.destination_id
                    if child in result.stale_reasons:
                        continue
                    result.contract_checks += 1
                    check = check_edge(edge.contract, edge.contract_config, nid, src.output,
                                       store.get_node(child).output)
                    if not check.holds:
                        self._mark_stale(store, child, f"{edge.contract} broken by {nid}: {check.explanation}",
                                         result)

    # ── shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _mark_stale(store: GraphStore, nid: str, reason: str, result: ReplanResult) -> None:
        if nid in result.stale_reasons:
            return
        store.update_node_status(nid, NodeStatus.STALE, reason=f"stale: {reason}")
        result.stale.append(nid)
        result.stale_reasons[nid] = reason

    async def _regenerate(self, store: GraphStore, nodes: list[str], result: ReplanResult, use_frozen_context: bool,
                          ctx: dict[str, Any]) -> set[str]:
        """Run the agents for *nodes*; return the IDs whose output changed."""
        if not nodes:
            return set()
        stale = set(result.stale_reasons)
        before = {nid: store.get_node(nid).output for nid in nodes}
        neighbours: dict[str, set[str]] = {nid: set() for nid in nodes}
        for e in store.list_edges():
            if e.destination_id in neighbours:
                neighbours[e.destination_id].add(e.source_id)
            if e.source_id in neighbours:
                neighbours[e.source_id].add(e.destination_id)

        node_context: dict[str, dict[str, Any]] = {}
        for nid in nodes:
            entry: dict[str, Any] = {"replan": True}
            if use_frozen_context:
                entry["previous_output"] = before[nid]
                entry["frozen_neighbours"] = {m: store.get_node(m).output for m in sorted(neighbours[nid])
                                              if m not in stale}
            node_context[nid] = entry
            store.update_node_status(nid, NodeStatus.PENDING, reason="scheduled for regeneration")

        sched = await self.scheduler.run(store, context=ctx, node_context=node_context)
        result.failed += [nid for nid in nodes if nid in sched.failed or nid in sched.skipped]

        changed = set()
        for nid in nodes:
            node = store.get_node(nid)
            if node.status != NodeStatus.DONE:
                continue
            self._reapply_facts(store, nid, before[nid])
            result.regenerated.append(nid)
            if store.get_node(nid).output != before[nid]:
                changed.add(nid)
                result.changed.append(nid)
        return changed

    @staticmethod
    def _reapply_facts(store: GraphStore, nid: str, previous: dict | None) -> None:
        """Pinned disruption facts survive regeneration while the entity is the same one."""
        node = store.get_node(nid)
        facts = node.inputs.get("facts")
        if not facts or not node.output:
            return
        same = all(node.output.get(k) == (previous or {}).get(k) for k in _IDENTITY_KEYS
                   if k in node.output or k in (previous or {}))
        if same:
            store.update_node_status(nid, NodeStatus.DONE, output={**node.output, **facts},
                                     reason="re-applied disruption facts")
        else:
            store.set_node_inputs(nid, {k: v for k, v in node.inputs.items() if k != "facts"})
