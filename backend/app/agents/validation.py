"""
Output validation: every real agent checks its result against these schemas
before it is written to the graph (plan §6.5). A failing output becomes a
non-retryable agent failure instead of bad data flowing downstream.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.base import AgentResult
from app.core.models import NodeType


class _Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class FlightOutput(_Out):
    direction: str
    flight_number: str = Field(min_length=1)
    origin_iata: str = Field(pattern=r"^[A-Z]{3}$")
    destination_iata: str = Field(pattern=r"^[A-Z]{3}$")
    departure_time: datetime
    arrival_time: datetime
    cost: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def times(self) -> "FlightOutput":
        if self.arrival_time <= self.departure_time:
            raise ValueError("arrival_time must be after departure_time")
        if self.origin_iata == self.destination_iata:
            raise ValueError("origin and destination are the same airport")
        return self


class StayOutput(_Out):
    hotel_id: str = Field(min_length=1)
    hotel_name: str = Field(min_length=1)
    check_in: date
    check_out: date
    checkin_cutoff: datetime
    nights: int = Field(ge=1)
    cost: float = Field(gt=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def dates(self) -> "StayOutput":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if (self.check_out - self.check_in).days != self.nights:
            raise ValueError("nights does not match the dates")
        if self.checkin_cutoff.date() != self.check_in:
            raise ValueError("checkin_cutoff must be on the check-in date")
        return self


class TransferOutput(_Out):
    direction: str
    hotel_id: str = Field(min_length=1)
    pickup_time: datetime
    dropoff_time: datetime
    duration_minutes: float = Field(gt=0, le=600)
    cost: float = Field(ge=0)

    @model_validator(mode="after")
    def order(self) -> "TransferOutput":
        if self.dropoff_time <= self.pickup_time:
            raise ValueError("dropoff_time must be after pickup_time")
        return self


class ActivityOutput(_Out):
    name: str = Field(min_length=1)
    start: datetime
    end: datetime
    outdoor: bool
    cost: float = Field(ge=0)

    @model_validator(mode="after")
    def order(self) -> "ActivityOutput":
        if self.end <= self.start:
            raise ValueError(f"activity '{self.name}' ends before it starts")
        return self


class DayPlanOutput(_Out):
    date: date
    hotel_id: str | None
    activities: list[ActivityOutput]
    cost: float = Field(ge=0)

    @model_validator(mode="after")
    def schedule(self) -> "DayPlanOutput":
        for a in self.activities:
            if a.start.date() != self.date:
                raise ValueError(f"activity '{a.name}' is not on {self.date}")
        for prev, nxt in zip(self.activities, self.activities[1:]):
            if nxt.start < prev.end:
                raise ValueError(f"'{prev.name}' overlaps '{nxt.name}'")
        if abs(sum(a.cost for a in self.activities) - self.cost) > 0.05:
            raise ValueError("day cost does not equal the sum of activity costs")
        return self


class WeatherOutput(_Out):
    date: date
    condition: str = Field(min_length=1)
    is_storm: bool
    temperature_high_c: float
    temperature_low_c: float

    @model_validator(mode="after")
    def temps(self) -> "WeatherOutput":
        if self.temperature_high_c < self.temperature_low_c:
            raise ValueError("high temperature below low temperature")
        return self


class BudgetOutput(_Out):
    budget_limit: float = Field(gt=0)
    total_cost: float = Field(ge=0)
    within_budget: bool
    line_items: dict[str, float]

    @model_validator(mode="after")
    def totals(self) -> "BudgetOutput":
        if any(v < 0 for v in self.line_items.values()):
            raise ValueError("negative line item")
        if abs(sum(self.line_items.values()) - self.total_cost) > 0.05:
            raise ValueError("total_cost does not equal the sum of line items")
        if self.within_budget != (self.total_cost <= self.budget_limit):
            raise ValueError("within_budget flag is inconsistent")
        return self


OUTPUT_SCHEMAS: dict[NodeType, type[BaseModel]] = {
    NodeType.FLIGHT: FlightOutput,
    NodeType.STAY: StayOutput,
    NodeType.TRANSFER: TransferOutput,
    NodeType.DAY_PLAN: DayPlanOutput,
    NodeType.WEATHER: WeatherOutput,
    NodeType.BUDGET: BudgetOutput,
}


def validate_output(node_type: NodeType, output: dict[str, Any]) -> list[str]:
    """Return validation errors for an agent output (empty list = valid)."""
    try:
        OUTPUT_SCHEMAS[node_type].model_validate(output)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, e['loc'])) or 'output'}: {e['msg']}" for e in exc.errors()]
    return []


def finish(node_type: NodeType, output: dict[str, Any]) -> AgentResult:
    """Validate and wrap an output; invalid outputs never reach the graph."""
    errors = validate_output(node_type, output)
    if errors:
        return AgentResult.fail("output validation failed: " + "; ".join(errors[:5]), retryable=False)
    return AgentResult.ok(output)
