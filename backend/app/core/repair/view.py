"""
GraphView — a read-only overlay of candidate outputs on a GraphStore.

The repair search tries many combinations of outputs for the tasks it is
repairing. A view answers ``get_node`` / ``list_nodes`` / ``list_edges`` /
``validate`` like the store, with some outputs replaced, so the existing code
that reads a graph (``Scheduler._resolve_inputs``, ``check_edge``, the metrics)
works on it unchanged. The store itself is never written: views are immutable
and ``with_output`` returns a new one.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.core.graph import GraphStore
from app.core.models import TaskNode


class GraphView:
    def __init__(self, base: GraphStore, overrides: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.base = base
        self._overrides: dict[str, dict[str, Any]] = dict(overrides or {})
        missing = [nid for nid in self._overrides if nid not in {n.id for n in base.list_nodes()}]
        if missing:
            raise KeyError(f"overrides for unknown nodes: {missing}")

    @property
    def graph_id(self) -> str:
        return self.base.graph_id

    @property
    def overrides(self) -> dict[str, dict[str, Any]]:
        """The replaced outputs, by node ID (a copy)."""
        return dict(self._overrides)

    def with_output(self, node_id: str, output: dict[str, Any]) -> "GraphView":
        return GraphView(self.base, {**self._overrides, node_id: output})

    def output(self, node_id: str) -> dict[str, Any] | None:
        if node_id in self._overrides:
            return self._overrides[node_id]
        return self.base.get_node(node_id).output

    def get_node(self, node_id: str) -> TaskNode:
        node = self.base.get_node(node_id)                 # raises NodeNotFoundError like the store
        if node_id in self._overrides:
            return node.model_copy(update={"output": self._overrides[node_id]})
        return node

    def list_nodes(self) -> list[TaskNode]:
        return [self.get_node(n.id) for n in self.base.list_nodes()]

    def list_edges(self):
        return self.base.list_edges()

    def validate(self) -> list[str]:
        return self.base.validate()
