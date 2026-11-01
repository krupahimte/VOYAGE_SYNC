"""
Request and response Pydantic schemas for the /graphs API.

These are separate from domain models so API contracts can evolve independently.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.models import NodeStatus, NodeType, RiskTier


# ── Request bodies ────────────────────────────────────────────────────────────

class CreateGraphRequest(BaseModel):
    graph_id: str | None = Field(
        default=None,
        description="Optional client-supplied ID. A UUID is generated if omitted.",
    )


class AddNodeRequest(BaseModel):
    id: str | None = Field(
        default=None,
        description="Optional node ID. A UUID is generated if omitted.",
    )
    type: NodeType
    agent: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    risk_tier: RiskTier | None = None


class AddEdgeRequest(BaseModel):
    source_id: str
    destination_id: str
    contract: str = Field(min_length=1)
    contract_config: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="")


# ── Response bodies ───────────────────────────────────────────────────────────

class NodeResponse(BaseModel):
    id: str
    type: NodeType
    agent: str
    inputs: dict[str, Any]
    output: dict[str, Any] | None
    status: NodeStatus
    version: int
    risk_tier: RiskTier | None
    created_at: datetime
    updated_at: datetime


class EdgeResponse(BaseModel):
    source_id: str
    destination_id: str
    contract: str
    contract_config: dict[str, Any]
    description: str


class NodeHistoryEntryResponse(BaseModel):
    node_id: str
    version: int
    status: NodeStatus
    output: dict[str, Any] | None
    inputs: dict[str, Any]
    reason: str
    timestamp: datetime


class GraphResponse(BaseModel):
    graph_id: str
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]


class ValidationResponse(BaseModel):
    graph_id: str
    valid: bool
    topological_order: list[str]
    message: str


class HistoryResponse(BaseModel):
    graph_id: str
    history: dict[str, list[NodeHistoryEntryResponse]]
