"""
Request/response models for the disruption endpoints.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.monitor.events import DisruptionType


class DisruptionRequest(BaseModel):
    """Either a preset ``scenario`` or an explicit ``type`` + ``target_node`` (+ ``new_facts``)."""
    scenario: str | None = Field(default=None, description="Preset name, e.g. 'delay_5h'.")
    type: DisruptionType | None = None
    target_node: str | None = None
    new_facts: dict[str, Any] = Field(default_factory=dict)
    strategy: Literal["contract", "full", "naive"] = "contract"
    use_frozen_context: bool = True
    seed: int = 0
    compare_baseline: bool = Field(default=False,
                                   description="Also score this replan and a B1 full replan (on a copy) "
                                               "against the oracle; used by the demo's metrics panel.")

    @model_validator(mode="after")
    def one_form(self) -> "DisruptionRequest":
        if self.scenario is None and (self.type is None or not self.target_node):
            raise ValueError("give either 'scenario' or both 'type' and 'target_node'")
        return self


class DisruptionResponse(BaseModel):
    trip_id: str
    status: Literal["replanned", "no_effect", "duplicate", "invalid"]
    message: str
    event: dict[str, Any]
    broken_edges: list[list[str]] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)
    regenerated: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    contracts_total: int = 0
    contracts_holding: int = 0
    replan: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] | None = None


class DisruptionHistoryItem(BaseModel):
    id: int
    event_id: str
    event_type: str
    target_node: str
    status: str
    strategy: str
    created_at: str
    outcome: dict[str, Any]
