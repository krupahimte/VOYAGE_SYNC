"""Expand trips.yaml × disruptions.yaml into eval/scenarios/scenarios.yaml."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenario_set import load_scenarios, write_scenarios  # noqa: E402

if __name__ == "__main__":
    path = write_scenarios()
    scenarios = load_scenarios()
    print(f"wrote {len(scenarios)} scenarios to {path}")
    print("by kind:", dict(Counter(s["kind"] for s in scenarios)))
    print("by severity:", dict(Counter(s["severity"] for s in scenarios)))
