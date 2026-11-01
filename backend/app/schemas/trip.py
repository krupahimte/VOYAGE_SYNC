"""
Trip goal schema, LLM proposal schema and API response models for /trips.

The TripGoal is the validated user intent — the single input to the Orchestrator.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.models import NodeStatus, NodeType


# ── Trip goal (input) ─────────────────────────────────────────────────────────

class TripGoal(BaseModel):
    """Validated user trip goal — the entry point for the Orchestrator."""

    destination: str = Field(min_length=1, description="Destination city or region.")
    departure_city: str = Field(min_length=1, description="Departure city or airport.")
    departure_date: date = Field(description="Trip start date.")
    return_date: date = Field(description="Trip end date.")
    travelers: int = Field(ge=1, le=20, description="Number of travelers.")
    budget: float = Field(gt=0, description="Total trip budget.")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="ISO 4217 currency code.")
    travel_style: str = Field(default="balanced", description="Travel style (budget, balanced, comfort, luxury).")
    interests: list[str] = Field(default_factory=list, description="Areas of interest.")
    preferences: dict[str, Any] = Field(default_factory=dict, description="Additional preferences (accommodation, pace, …).")
    notes: str = Field(default="", description="Free-text notes from the traveler.")
    flexible_dates: bool = Field(default=False, description="Whether dates are flexible.")

    @field_validator("destination", "departure_city")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def dates_ordered(self) -> "TripGoal":
        if self.return_date <= self.departure_date:
            raise ValueError("return_date must be after departure_date")
        if (self.return_date - self.departure_date).days > 30:
            raise ValueError("trips longer than 30 nights are not supported")
        return self

    @property
    def nights(self) -> int:
        return (self.return_date - self.departure_date).days


class TripCreateRequest(TripGoal):
    """Body of POST /trips: the goal plus the user whose memory to use."""
    user_id: str = Field(default="demo-user", min_length=1, max_length=64)
    propose_bookings: bool = Field(default=True,
                                   description="Raise T2 booking approvals for the flights and the stay.")


# ── LLM proposal schemas (structured output) ─────────────────────────────────

Direction = Literal["outbound", "return", "arrival", "departure"]


class ProposedTask(BaseModel):
    """
    One task proposed by the LLM.

    The LLM chooses *which* tasks exist (from the fixed vocabulary) and their
    role (direction / day). Edges, contracts and concrete inputs are attached
    by the Orchestrator from templates — any edge or contract the LLM writes
    is ignored.
    """
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    task_type: NodeType
    direction: Direction | None = None
    day: int | None = Field(default=None, ge=1)
    theme: str = Field(default="", max_length=120, description="Short label for a day plan.")


class ProposedPlan(BaseModel):
    """The complete structured plan from LLM decomposition."""
    model_config = ConfigDict(extra="ignore")

    tasks: list[ProposedTask] = Field(min_length=1, max_length=80)
    summary: str = Field(default="", max_length=500)


# ── API response models ──────────────────────────────────────────────────────

class TaskSummaryResponse(BaseModel):
    """Compact view of a task node for API responses."""
    id: str
    type: NodeType
    agent: str = ""
    status: NodeStatus
    version: int = 1
    output: dict[str, Any] | None = None


class EdgeSummaryResponse(BaseModel):
    """Edge with its contract and the contract's current evaluation."""
    source_id: str
    destination_id: str
    contract: str
    description: str
    holds: bool | None = None
    explanation: str = ""


class DecompositionInfo(BaseModel):
    source: Literal["llm", "fallback"]
    attempts: int
    errors: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    provider: str = ""


class TripExecutionResponse(BaseModel):
    """Response from POST /trips — graph creation + execution result."""
    trip_id: str
    graph_id: str
    user_id: str
    success: bool
    tasks_done: list[str]
    tasks_failed: list[str]
    tasks_skipped: list[str]
    summary: str
    decomposition: DecompositionInfo
    contracts_total: int
    contracts_holding: int
    usage: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    nodes: list[TaskSummaryResponse]


class TripGraphResponse(BaseModel):
    """Response from GET /trips/{trip_id}/graph."""
    trip_id: str
    graph_id: str
    nodes: list[TaskSummaryResponse]
    edges: list[EdgeSummaryResponse]


class DayItinerary(BaseModel):
    day: int
    date: str
    theme: str = ""
    weather: dict[str, Any] | None = None
    activities: list[dict[str, Any]] = Field(default_factory=list)
    cost: float = 0.0


class TripItineraryResponse(BaseModel):
    """Response from GET /trips/{trip_id}/itinerary — the user-facing plan."""
    trip_id: str
    destination: str
    departure_city: str
    departure_date: str
    return_date: str
    travelers: int
    currency: str
    summary: str = ""
    flights: list[dict[str, Any]] = Field(default_factory=list)
    stay: dict[str, Any] | None = None
    transfers: list[dict[str, Any]] = Field(default_factory=list)
    days: list[DayItinerary] = Field(default_factory=list)
    budget: dict[str, Any] | None = None
