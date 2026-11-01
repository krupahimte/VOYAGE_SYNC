"""
Domain enumerations and Pydantic models for the Task Dependency Graph engine.

These are pure-Python models — they carry no SQLModel table metadata.
SQLModel persistence wrappers live in app.db.models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class NodeType(str, Enum):
    FLIGHT    = "FLIGHT"
    STAY      = "STAY"
    TRANSFER  = "TRANSFER"
    DAY_PLAN  = "DAY_PLAN"
    WEATHER   = "WEATHER"
    BUDGET    = "BUDGET"


class NodeStatus(str, Enum):
    PENDING           = "PENDING"
    READY             = "READY"
    RUNNING           = "RUNNING"
    DONE              = "DONE"
    STALE             = "STALE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    FAILED            = "FAILED"


class RiskTier(str, Enum):
    """Risk Gate tiers (plan §6.4); see app.core.risk_gate."""
    T0 = "T0"   # Autonomous — search, compare, draft: runs immediately
    T1 = "T1"   # Notify — small reversible change: runs, then the user is told (with undo)
    T2 = "T2"   # Approve — book / cancel / change / pay / spend: waits for the user


# ── Core domain models ────────────────────────────────────────────────────────

class TaskNode(BaseModel):
    """A single node in the Task Dependency Graph."""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this node.",
    )
    type: NodeType
    agent: str = Field(
        description="Name of the agent responsible for executing this task.",
        min_length=1,
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters consumed by the agent.",
    )
    output: dict[str, Any] | None = Field(
        default=None,
        description="Output produced by the agent after execution.",
    )
    status: NodeStatus = Field(default=NodeStatus.PENDING)
    version: int = Field(default=1, ge=1)
    risk_tier: RiskTier | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("agent")
    @classmethod
    def agent_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent must not be blank")
        return v


class Edge(BaseModel):
    """
    A directed dependency edge in the Task Dependency Graph.
    Edge A → B means B depends on A (A must complete before B can start).
    """

    source_id: str = Field(description="ID of the upstream (prerequisite) node.")
    destination_id: str = Field(description="ID of the downstream (dependent) node.")
    contract: str = Field(
        description="Contract template identifier (e.g. 'FLIGHT_BEFORE_CHECKIN').",
        min_length=1,
    )
    contract_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters used by the contract evaluator.",
    )
    description: str = Field(
        default="",
        description="Human-readable explanation of this dependency.",
    )

    @field_validator("contract")
    @classmethod
    def contract_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("contract must not be blank")
        return v


# ── Node history entry ────────────────────────────────────────────────────────

class NodeHistoryEntry(BaseModel):
    """Immutable snapshot of a node's state at a point in time."""

    node_id: str
    version: int
    status: NodeStatus
    output: dict[str, Any] | None
    inputs: dict[str, Any]
    reason: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
