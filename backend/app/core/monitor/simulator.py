"""
Disruption Simulator: scripted, seeded events for the demo and the benchmark
(real disruptions cannot be summoned on demand).
"""

from __future__ import annotations

import random
from typing import Any

from app.core.graph import GraphStore
from app.core.monitor.events import TARGET_TYPES, DisruptionEvent, DisruptionType

# name → (type, target node, new facts, description)
PRESETS: dict[str, tuple[DisruptionType, str, dict[str, Any], str]] = {
    "delay_10m": (DisruptionType.FLIGHT_DELAY, "flight_outbound", {"delay_minutes": 10},
                  "Outbound flight delayed 10 min"),
    "delay_2h": (DisruptionType.FLIGHT_DELAY, "flight_outbound", {"delay_minutes": 120},
                 "Outbound flight delayed 2 h"),
    "delay_5h": (DisruptionType.FLIGHT_DELAY, "flight_outbound", {"delay_minutes": 300},
                 "Outbound flight delayed 5 h"),
    "return_delay_3h": (DisruptionType.FLIGHT_DELAY, "flight_return", {"delay_minutes": 180},
                        "Return flight delayed 3 h"),
    "cancel_outbound": (DisruptionType.FLIGHT_CANCELLATION, "flight_outbound", {},
                        "Outbound flight cancelled"),
    "cancel_return": (DisruptionType.FLIGHT_CANCELLATION, "flight_return", {}, "Return flight cancelled"),
    "hotel_overbooked": (DisruptionType.HOTEL_OVERBOOKED, "hotel_stay", {}, "Hotel overbooked"),
    "storm_day_2": (DisruptionType.WEATHER_STORM, "weather_day_2", {"condition": "thunderstorm"},
                    "Storm on day 2"),
    "storm_day_3": (DisruptionType.WEATHER_STORM, "weather_day_3", {"condition": "thunderstorm"},
                    "Storm on day 3"),
    "price_spike_hotel": (DisruptionType.PRICE_SPIKE, "hotel_stay", {"pct": 25}, "Hotel price +25 %"),
    "price_spike_flight": (DisruptionType.PRICE_SPIKE, "flight_return", {"pct": 40}, "Return fare +40 %"),
}


class DisruptionSimulator:
    """Seeded: the same seed and calls always produce the same events."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    def _event_id(self) -> str:
        return f"evt_sim_{self.seed}_{self._rng.getrandbits(40):010x}"

    def make(self, event_type: DisruptionType | str, target_node: str, **facts: Any) -> DisruptionEvent:
        return DisruptionEvent(event_id=self._event_id(), type=DisruptionType(event_type),
                               target_node=target_node, new_facts=facts, source="simulator")

    def preset(self, name: str) -> DisruptionEvent:
        if name not in PRESETS:
            raise KeyError(f"Unknown scenario '{name}'. Known: {', '.join(sorted(PRESETS))}")
        event_type, target, facts, _ = PRESETS[name]
        return self.make(event_type, target, **facts)

    def random_event(self, store: GraphStore) -> DisruptionEvent:
        """A random, valid event for the given graph (for benchmark sweeps)."""
        by_type: dict[str, list[str]] = {}
        for node in store.list_nodes():
            by_type.setdefault(node.type.value, []).append(node.id)
        options = [(t, nid) for t, types in TARGET_TYPES.items() for nt in sorted(types)
                   for nid in sorted(by_type.get(nt, []))]
        event_type, target = self._rng.choice(options)
        facts: dict[str, Any] = {}
        if event_type == DisruptionType.FLIGHT_DELAY:
            facts["delay_minutes"] = self._rng.choice([15, 45, 90, 180, 300, 480])
        elif event_type == DisruptionType.PRICE_SPIKE:
            facts["pct"] = self._rng.choice([5, 15, 30, 60])
        elif event_type == DisruptionType.WEATHER_STORM:
            facts["condition"] = "thunderstorm"
        return self.make(event_type, target, **facts)


def preset_catalog() -> list[dict[str, Any]]:
    return [{"name": name, "type": t.value, "target_node": target, "new_facts": facts, "description": desc}
            for name, (t, target, facts, desc) in PRESETS.items()]

