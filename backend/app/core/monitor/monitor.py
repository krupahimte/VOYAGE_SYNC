"""
Disruption Monitor: event in → facts applied (with dedup) → impact pre-check →
re-plan through the Orchestrator's Replanner. Also offers optional live
weather polling (Open-Meteo forecast) that turns a newly forecast storm into
a WEATHER_STORM event.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from app.core.graph import GraphStore
from app.core.models import NodeType
from app.core.monitor.apply import ApplyOutcome, apply_event
from app.core.monitor.events import DisruptionEvent, DisruptionType
from app.core.replanner import Replanner, ReplanResult, Strategy
from app.integrations.http import ProviderError
from app.integrations.weather import FORECAST_HORIZON_DAYS

logger = logging.getLogger(__name__)

Status = Literal["replanned", "no_effect", "duplicate", "invalid"]


@dataclass
class MonitorOutcome:
    event: DisruptionEvent
    status: Status
    message: str
    applied: ApplyOutcome
    replan: ReplanResult | None = None
    latency_ms: float = 0.0           # event received → valid plan
    actions: list[Any] = field(default_factory=list)   # Risk Gate actions (set by the Orchestrator)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.model_dump(mode="json"),
            "status": self.status,
            "message": self.message,
            "broken_edges": [list(e) for e in self.applied.broken_edges],
            "replan": self.replan.to_dict() if self.replan else None,
            "latency_ms": self.latency_ms,
            "actions": [a.to_dict() for a in self.actions],
        }


class DisruptionMonitor:
    def __init__(self, replanner: Replanner) -> None:
        self.replanner = replanner

    async def handle(
        self,
        store: GraphStore,
        event: DisruptionEvent,
        *,
        strategy: Strategy | str = Strategy.CONTRACT,
        use_frozen_context: bool = True,
        context: dict[str, Any] | None = None,
    ) -> MonitorOutcome:
        started = time.perf_counter()
        strategy = Strategy(strategy)
        applied = apply_event(store, event)
        if applied.kind in ("duplicate", "invalid"):
            logger.info("Disruption %s dropped (%s): %s", event.event_id, applied.kind, applied.message)
            return MonitorOutcome(event, applied.kind, applied.message, applied)
        if strategy == Strategy.CONTRACT and not applied.has_effect:
            logger.info("Disruption %s recorded with no effect: %s", event.event_id, applied.message)
            return MonitorOutcome(event, "no_effect", applied.message, applied,
                                  latency_ms=round((time.perf_counter() - started) * 1000, 2))

        replan = await self.replanner.replan(store, applied.target, applied.kind, strategy=strategy,
                                             use_frozen_context=use_frozen_context, event_id=event.event_id,
                                             context=context)
        message = f"{applied.message}; regenerated {len(replan.regenerated)} task(s)"
        return MonitorOutcome(event, "replanned", message, applied, replan,
                              latency_ms=round((time.perf_counter() - started) * 1000, 2))

    async def poll_weather(self, store: GraphStore, providers: Any, *, today: date | None = None
                           ) -> list[DisruptionEvent]:
        """
        Live source: re-check the forecast for weather nodes within the forecast
        horizon; a newly forecast storm becomes a WEATHER_STORM event.
        """
        from app.agents.common import locate

        today = today or date.today()
        events = []
        for node in store.list_nodes():
            out = node.output or {}
            if node.type != NodeType.WEATHER or not out.get("date") or out.get("is_storm"):
                continue
            day = date.fromisoformat(out["date"])
            if not today <= day <= today + timedelta(days=FORECAST_HORIZON_DAYS):
                continue
            try:
                loc = await locate(out.get("city") or node.inputs.get("city", ""), providers)
                forecast = [d for d in await providers.weather.daily(loc, day, day) if d.date == day]
            except ProviderError as exc:
                logger.warning("Weather poll failed for %s: %s", node.id, exc)
                continue
            if forecast and forecast[0].is_storm:
                events.append(DisruptionEvent(type=DisruptionType.WEATHER_STORM, target_node=node.id,
                                              new_facts={"condition": forecast[0].condition},
                                              source=f"live:{forecast[0].source or 'weather'}"))
        return events
