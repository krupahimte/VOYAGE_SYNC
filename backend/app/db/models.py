"""
SQLModel persistence models for the Task Dependency Graph and user memory.

Tables:
  - GraphRecord       — one row per graph (id, created_at).
  - NodeRecord        — one row per node (current state).
  - NodeHistoryRecord — append-only audit log of every node state change.
  - EdgeRecord        — one row per edge.
  - UserProfileRecord — saved preferences per user (memory).
  - TripRecord        — planned trips (memory: past trips).
  - ApprovalRecord    — approve/reject history (memory; written from Phase 6).
  - DisruptionRecord  — handled disruptions and their re-plans (Phase 5).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Graph ─────────────────────────────────────────────────────────────────────

class GraphRecord(SQLModel, table=True):
    __tablename__ = "graphs"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── Node (current state) ───────────────────────────────────────────────────────

class NodeRecord(SQLModel, table=True):
    __tablename__ = "graph_nodes"

    # Composite primary key: (graph_id, node_id)
    graph_id: str = Field(primary_key=True)
    node_id: str = Field(primary_key=True)

    node_type: str
    agent: str
    status: str
    version: int = Field(default=1)
    risk_tier: Optional[str] = Field(default=None)

    # JSON-serialised dicts stored as TEXT
    inputs_json: str = Field(default="{}")
    output_json: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # ── helpers ───────────────────────────────────────────────────────────────

    def get_inputs(self) -> dict[str, Any]:
        return json.loads(self.inputs_json)

    def set_inputs(self, value: dict[str, Any]) -> None:
        self.inputs_json = json.dumps(value)

    def get_output(self) -> dict[str, Any] | None:
        if self.output_json is None:
            return None
        return json.loads(self.output_json)

    def set_output(self, value: dict[str, Any] | None) -> None:
        self.output_json = json.dumps(value) if value is not None else None


# ── Node history (append-only) ────────────────────────────────────────────────

class NodeHistoryRecord(SQLModel, table=True):
    __tablename__ = "node_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    graph_id: str = Field(index=True)
    node_id: str = Field(index=True)
    version: int
    status: str
    reason: str
    inputs_json: str = Field(default="{}")
    output_json: Optional[str] = Field(default=None)
    timestamp: datetime = Field(default_factory=_now)

    def get_inputs(self) -> dict[str, Any]:
        return json.loads(self.inputs_json)

    def get_output(self) -> dict[str, Any] | None:
        if self.output_json is None:
            return None
        return json.loads(self.output_json)


# ── Edge ──────────────────────────────────────────────────────────────────────

class EdgeRecord(SQLModel, table=True):
    __tablename__ = "graph_edges"

    id: Optional[int] = Field(default=None, primary_key=True)
    graph_id: str = Field(index=True)
    source_id: str
    destination_id: str
    contract: str
    contract_config_json: str = Field(default="{}")
    description: str = Field(default="")

    def get_contract_config(self) -> dict[str, Any]:
        return json.loads(self.contract_config_json)

    def set_contract_config(self, value: dict[str, Any]) -> None:
        self.contract_config_json = json.dumps(value)


# ── User profile & memory (read by the Orchestrator) ─────────────────────────

class UserProfileRecord(SQLModel, table=True):
    __tablename__ = "user_profiles"

    user_id: str = Field(primary_key=True)
    display_name: str = Field(default="")
    preferences_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def get_preferences(self) -> dict[str, Any]:
        return json.loads(self.preferences_json)

    def set_preferences(self, value: dict[str, Any]) -> None:
        self.preferences_json = json.dumps(value, sort_keys=True)


class TripRecord(SQLModel, table=True):
    """One planned trip: its goal, its graph and how it was decomposed."""
    __tablename__ = "trips"

    trip_id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    graph_id: str
    goal_json: str
    status: str = Field(default="PLANNED")
    summary: str = Field(default="")
    decomposition_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=_now)

    def get_goal(self) -> dict[str, Any]:
        return json.loads(self.goal_json)

    def get_decomposition(self) -> dict[str, Any]:
        return json.loads(self.decomposition_json)


class ApprovalRecord(SQLModel, table=True):
    """Approval history: one row per approve/reject decision (filled in Phase 6)."""
    __tablename__ = "approval_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    trip_id: str = Field(index=True)
    node_id: str = Field(default="")
    action: str = Field(default="")
    decision: str            # "approved" | "rejected"
    amount: Optional[float] = Field(default=None)
    created_at: datetime = Field(default_factory=_now)


# ── Disruptions and re-plans (Phase 5) ────────────────────────────────────────

class DisruptionRecord(SQLModel, table=True):
    """One handled disruption event and the re-plan it caused (trace log for evaluation)."""
    __tablename__ = "disruptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: str = Field(index=True)
    event_id: str = Field(index=True)
    event_type: str
    target_node: str
    status: str                      # replanned | no_effect | duplicate | invalid
    strategy: str
    outcome_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=_now)

    def get_outcome(self) -> dict[str, Any]:
        return json.loads(self.outcome_json)


# ── Risk Gate actions (Phase 6) ───────────────────────────────────────────────

class GateActionRecord(SQLModel, table=True):
    """
    One Risk Gate action: T0 drafts, T1 notifications (with their undo payload)
    and T2 approvals. ``status`` is authoritative and is moved out of
    "pending" only by a conditional update (see approval_repo.claim).
    """
    __tablename__ = "gate_actions"

    action_id: str = Field(primary_key=True)
    trip_id: str = Field(index=True)
    node_id: str = Field(default="")
    tier: str = Field(index=True)
    kind: str
    status: str = Field(index=True)
    data_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def get_data(self) -> dict[str, Any]:
        return json.loads(self.data_json)
