"""
Normalised disruption events: DisruptionEvent{type, target_node, new_facts, severity, timestamp}.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DisruptionType(str, Enum):
    FLIGHT_DELAY = "FLIGHT_DELAY"                # new_facts: delay_minutes (total vs schedule)
    FLIGHT_CANCELLATION = "FLIGHT_CANCELLATION"  # new_facts: offer_id (optional)
    HOTEL_OVERBOOKED = "HOTEL_OVERBOOKED"        # new_facts: hotel_id (optional)
    WEATHER_STORM = "WEATHER_STORM"              # new_facts: condition (default thunderstorm)
    PRICE_SPIKE = "PRICE_SPIKE"                  # new_facts: pct (e.g. 25 = +25 %)


Severity = Literal["low", "medium", "high"]

# Which node type each disruption may target.
TARGET_TYPES = {
    DisruptionType.FLIGHT_DELAY: {"FLIGHT"},
    DisruptionType.FLIGHT_CANCELLATION: {"FLIGHT"},
    DisruptionType.HOTEL_OVERBOOKED: {"STAY"},
    DisruptionType.WEATHER_STORM: {"WEATHER"},
    DisruptionType.PRICE_SPIKE: {"FLIGHT", "STAY", "TRANSFER", "DAY_PLAN"},
}


def default_severity(event_type: DisruptionType, facts: dict[str, Any]) -> Severity:
    if event_type == DisruptionType.FLIGHT_DELAY:
        minutes = float(facts.get("delay_minutes", 0))
        return "low" if minutes < 60 else "medium" if minutes < 180 else "high"
    if event_type == DisruptionType.PRICE_SPIKE:
        pct = abs(float(facts.get("pct", 0)))
        return "low" if pct < 10 else "medium" if pct < 30 else "high"
    if event_type == DisruptionType.WEATHER_STORM:
        return "medium"
    return "high"


class DisruptionEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:10]}")
    type: DisruptionType
    target_node: str = Field(min_length=1)
    new_facts: dict[str, Any] = Field(default_factory=dict)
    severity: Severity | None = None
    source: str = "simulator"              # simulator | live:<provider> | api
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def check_facts(self) -> "DisruptionEvent":
        f = self.new_facts
        if self.type == DisruptionType.FLIGHT_DELAY:
            minutes = f.get("delay_minutes")
            if not isinstance(minutes, (int, float)) or not 0 <= minutes <= 72 * 60:
                raise ValueError("FLIGHT_DELAY needs new_facts.delay_minutes between 0 and 4320")
        if self.type == DisruptionType.PRICE_SPIKE:
            pct = f.get("pct")
            if not isinstance(pct, (int, float)) or not -90 <= pct <= 500:
                raise ValueError("PRICE_SPIKE needs new_facts.pct between -90 and 500")
        if self.severity is None:
            self.severity = default_severity(self.type, f)
        return self

    @property
    def dedup_key(self) -> str:
        return f"{self.type.value}:{self.target_node}"

    def summary(self) -> str:
        f = self.new_facts
        if self.type == DisruptionType.FLIGHT_DELAY:
            return f"{self.target_node} delayed by {int(f['delay_minutes'])} min"
        if self.type == DisruptionType.PRICE_SPIKE:
            return f"{self.target_node} price {f['pct']:+g}%"
        return f"{self.type.value.replace('_', ' ').lower()} on {self.target_node}"
