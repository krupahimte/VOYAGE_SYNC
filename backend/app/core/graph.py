"""
GraphStore: in-memory business logic for the Task Dependency Graph.

Responsibilities:
  - CRUD for nodes and edges on a named graph.
  - Structural validation: duplicate IDs, self-edges, missing references, cycles.
  - Deterministic topological ordering (Kahn's algorithm).
  - Maintained node history (version, timestamp, reason) — never silently overwritten.

This module contains NO SQLModel or database code.
Persistence is handled by app.db.graph_repo.
"""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from app.core.models import (
    Edge,
    NodeHistoryEntry,
    NodeStatus,
    TaskNode,
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class GraphError(Exception):
    """Base class for all graph validation errors."""


class DuplicateNodeError(GraphError):
    pass


class DuplicateEdgeError(GraphError):
    pass


class SelfEdgeError(GraphError):
    pass


class MissingNodeError(GraphError):
    pass


class CycleError(GraphError):
    pass


class NodeNotFoundError(GraphError):
    pass


class EdgeNotFoundError(GraphError):
    pass


# ── GraphStore ────────────────────────────────────────────────────────────────

class GraphStore:
    """
    In-memory store for a single graph's nodes, edges, and node history.

    Thread-safety: Not thread-safe by design — callers must serialize access
    (e.g., via FastAPI's request lifecycle + SQLite session).
    """

    def __init__(self, graph_id: str) -> None:
        self.graph_id: str = graph_id
        self._nodes: dict[str, TaskNode] = {}
        self._edges: list[Edge] = []
        # History keyed by node_id, ordered oldest-first.
        self._history: dict[str, list[NodeHistoryEntry]] = defaultdict(list)

    # ── Node CRUD ─────────────────────────────────────────────────────────────

    def add_node(self, node: TaskNode) -> TaskNode:
        """Add a node. Raises DuplicateNodeError if the ID already exists."""
        if node.id in self._nodes:
            raise DuplicateNodeError(
                f"Node '{node.id}' already exists in graph '{self.graph_id}'."
            )
        node = node.model_copy(deep=True)
        self._nodes[node.id] = node
        self._record_history(node, reason="created")
        return node

    def get_node(self, node_id: str) -> TaskNode:
        """Retrieve a node by ID. Raises NodeNotFoundError if absent."""
        if node_id not in self._nodes:
            raise NodeNotFoundError(
                f"Node '{node_id}' not found in graph '{self.graph_id}'."
            )
        return self._nodes[node_id]

    def update_node_status(
        self,
        node_id: str,
        new_status: NodeStatus,
        output: dict[str, Any] | None = None,
        reason: str = "status updated",
    ) -> TaskNode:
        """
        Update a node's status (and optionally output).
        Increments version and records a history entry.
        """
        node = self.get_node(node_id)
        updated = node.model_copy(
            update={
                "status": new_status,
                "output": output if output is not None else node.output,
                "version": node.version + 1,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._nodes[node_id] = updated
        self._record_history(updated, reason=reason)
        return updated

    def set_node_inputs(self, node_id: str, inputs: dict[str, Any]) -> TaskNode:
        """
        Replace a node's inputs (e.g. disruption facts or exclusions). No version
        bump: the new inputs are captured in history by the next status change.
        """
        node = self.get_node(node_id)
        updated = node.model_copy(update={"inputs": copy.deepcopy(inputs),
                                          "updated_at": datetime.now(timezone.utc)})
        self._nodes[node_id] = updated
        return updated

    def restore_from(self, other: "GraphStore") -> None:
        """
        Take over *other*'s nodes, edges and history. Used to commit a repair atomically:
        the repair is applied and validated on a copy, then the copy replaces this graph.
        """
        if other.graph_id != self.graph_id:
            raise GraphError(f"Cannot restore graph '{self.graph_id}' from '{other.graph_id}'.")
        self._nodes, self._edges, self._history = other._nodes, other._edges, other._history

    def list_nodes(self) -> list[TaskNode]:
        """Return all nodes in insertion order."""
        return list(self._nodes.values())

    # ── Edge CRUD ─────────────────────────────────────────────────────────────

    def add_edge(self, edge: Edge) -> Edge:
        """
        Add a directed edge A → B (B depends on A).

        Validates:
          - Self-edges are rejected.
          - Both source and destination must exist as nodes.
          - Duplicate edges (same source+destination) are rejected.
          - Adding the edge must not introduce a cycle.
        """
        if edge.source_id == edge.destination_id:
            raise SelfEdgeError(
                f"Self-edge on node '{edge.source_id}' is not allowed."
            )
        if edge.source_id not in self._nodes:
            raise MissingNodeError(
                f"Source node '{edge.source_id}' does not exist."
            )
        if edge.destination_id not in self._nodes:
            raise MissingNodeError(
                f"Destination node '{edge.destination_id}' does not exist."
            )
        if self._edge_exists(edge.source_id, edge.destination_id):
            raise DuplicateEdgeError(
                f"Edge '{edge.source_id}' → '{edge.destination_id}' already exists."
            )
        # Tentatively add and check for cycles
        self._edges.append(edge)
        try:
            self._topo_sort()  # raises CycleError if a cycle exists
        except CycleError:
            self._edges.pop()  # roll back
            raise
        return edge

    def list_edges(self) -> list[Edge]:
        """Return all edges in insertion order."""
        return list(self._edges)

    # ── Graph-level operations ────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """
        Validate the graph and return a deterministic topological order of node IDs.
        Raises CycleError if a cycle is detected.
        Isolated nodes (no edges) are always included.
        """
        return self._topo_sort()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict."""
        return {
            "graph_id": self.graph_id,
            "nodes": [n.model_dump(mode="json") for n in self._nodes.values()],
            "edges": [e.model_dump(mode="json") for e in self._edges],
            "history": {
                node_id: [h.model_dump(mode="json") for h in entries]
                for node_id, entries in self._history.items()
            },
        }

    def get_history(self, node_id: str) -> list[NodeHistoryEntry]:
        """Retrieve the version history for a specific node."""
        if node_id not in self._nodes and node_id not in self._history:
            raise NodeNotFoundError(f"Node '{node_id}' not found.")
        return list(self._history.get(node_id, []))

    def get_all_history(self) -> dict[str, list[NodeHistoryEntry]]:
        """Retrieve the full version history for all nodes."""
        return {k: list(v) for k, v in self._history.items()}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _edge_exists(self, source_id: str, destination_id: str) -> bool:
        return any(
            e.source_id == source_id and e.destination_id == destination_id
            for e in self._edges
        )

    def _topo_sort(self) -> list[str]:
        """
        Kahn's algorithm for topological ordering.
        Returns node IDs in topological order.
        Raises CycleError if a cycle is detected.
        Isolated nodes are appended at the end in stable insertion order.
        """
        # Build adjacency and in-degree maps
        node_ids = list(self._nodes.keys())
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        successors: dict[str, list[str]] = {nid: [] for nid in node_ids}

        for edge in self._edges:
            successors[edge.source_id].append(edge.destination_id)
            in_degree[edge.destination_id] += 1

        # Stable ordering: use insertion order of nodes
        queue: deque[str] = deque(
            nid for nid in node_ids if in_degree[nid] == 0
        )
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for successor in successors[nid]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if len(order) != len(node_ids):
            # Some nodes couldn't be ordered → cycle exists
            in_cycle = [nid for nid in node_ids if in_degree[nid] > 0]
            raise CycleError(
                f"Cycle detected involving nodes: {in_cycle}"
            )

        return order

    def _record_history(self, node: TaskNode, reason: str) -> None:
        entry = NodeHistoryEntry(
            node_id=node.id,
            version=node.version,
            status=node.status,
            output=copy.deepcopy(node.output),
            inputs=copy.deepcopy(node.inputs),
            reason=reason,
        )
        self._history[node.id].append(entry)
