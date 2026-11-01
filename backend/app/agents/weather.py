"""
Weather data source (Open-Meteo or mock) — read by the Itinerary Agent for
outdoor planning and, from Phase 5, by the Disruption Monitor.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.agents.common import locate
from app.agents.validation import finish
from app.core.models import NodeType, TaskNode
from app.integrations.http import ProviderError


class WeatherAgent(BaseAgent):
    def __init__(self, providers: Any) -> None:
        self.p = providers

    async def run(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any]) -> AgentResult:
        city = resolved_inputs.get("city", "")
        try:
            day = date.fromisoformat(str(resolved_inputs.get("date"))[:10])
        except ValueError:
            return AgentResult.fail("invalid date", retryable=False)
        try:
            loc = await locate(city, self.p)
            days = [d for d in await self.p.weather.daily(loc, day, day) if d.date == day]
        except ProviderError as exc:
            return AgentResult.fail(f"weather lookup failed: {exc}")
        if not days:
            return AgentResult.fail(f"no weather data for {day}", retryable=False)
        w = days[0]
        return finish(NodeType.WEATHER, {
            "day": resolved_inputs.get("day"),
            "date": day.isoformat(),
            "city": city,
            "condition": w.condition,
            "is_storm": w.is_storm,
            "weather_code": w.weather_code,
            "temperature_high_c": w.temperature_high_c,
            "temperature_low_c": w.temperature_low_c,
            "precipitation_probability": w.precipitation_probability,
            "precipitation_mm": w.precipitation_mm,
            "wind_speed_kmh": w.wind_speed_kmh,
            "source": w.source,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
        })
