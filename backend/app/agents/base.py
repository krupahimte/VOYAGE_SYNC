"""
Agent interface and registry for the VoyageSync execution engine.

Design principles:
  - All agents are async callables with a single stable interface.
  - Results are typed and validated — no arbitrary dicts flowing through.
  - The registry maps (NodeType | agent-name string) → agent instance.
  - No LLM, external API or side-effectful code lives here.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from app.core.models import NodeType, TaskNode


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """
    The structured result returned by every agent run.

    Attributes:
        success:   Whether the run completed without error.
        output:    Arbitrary JSON-serialisable dict produced by the agent.
        retryable: If False and success is False, the scheduler must not retry.
        error:     Human-readable error message when success is False.
    """
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    retryable: bool = True
    error: str = ""

    @classmethod
    def ok(cls, output: dict[str, Any]) -> "AgentResult":
        return cls(success=True, output=output)

    @classmethod
    def fail(cls, error: str, *, retryable: bool = True) -> "AgentResult":
        return cls(success=False, output={}, retryable=retryable, error=error)


# ── Agent base class ──────────────────────────────────────────────────────────

class BaseAgent(abc.ABC):
    """
    Common interface every agent must implement.

    Parameters passed to ``run``:
        node:             The TaskNode being executed (read-only view).
        resolved_inputs:  Merged dict of the node's own inputs plus outputs
                          collected from all completed parent nodes.
        frozen_context:   Immutable execution context (graph_id, run_id, …).
    """

    @abc.abstractmethod
    async def run(
        self,
        node: TaskNode,
        resolved_inputs: dict[str, Any],
        frozen_context: dict[str, Any],
    ) -> AgentResult:
        """Execute the task and return a structured result."""


# ── Registry ──────────────────────────────────────────────────────────────────

class AgentRegistry:
    """
    Maps NodeType values and agent-name strings to BaseAgent instances.

    Lookup priority:
      1. Exact agent-name string match (node.agent).
      2. NodeType string match (node.type.value).
    """

    def __init__(self) -> None:
        self._by_name: dict[str, BaseAgent] = {}
        self._by_type: dict[str, BaseAgent] = {}
        # Set by app.agents.registry.build_agent_registry (provider bundle + usage meter).
        self.providers: Any = None

    def register_by_name(self, name: str, agent: BaseAgent) -> None:
        """Register an agent under an explicit agent-name key."""
        self._by_name[name] = agent

    def register_by_type(self, node_type: NodeType, agent: BaseAgent) -> None:
        """Register an agent as the default handler for a NodeType."""
        self._by_type[node_type.value] = agent

    def get(self, node: TaskNode) -> BaseAgent:
        """
        Return the agent for *node*, or raise KeyError if none is registered.
        """
        if node.agent in self._by_name:
            return self._by_name[node.agent]
        if node.type.value in self._by_type:
            return self._by_type[node.type.value]
        raise KeyError(
            f"No agent registered for name='{node.agent}' or type='{node.type.value}'."
        )

    def __contains__(self, node: TaskNode) -> bool:
        return node.agent in self._by_name or node.type.value in self._by_type
