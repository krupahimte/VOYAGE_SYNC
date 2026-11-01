"""
Strategies compared by the benchmark: ours, the two baselines (plan §6.2) and
the three ablations (plan §6.6). Every one runs behind the same
``Orchestrator.handle_disruption`` interface on an identical copy of the
planned (and booked) trip, against the same seeded market drift.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    label: str
    strategy: str                  # replanner strategy: contract | full | naive
    frozen_context: bool
    risk_gate: bool
    description: str


STRATEGIES: list[StrategyConfig] = [
    StrategyConfig("ours", "Ours", "contract", True, True,
                   "contract-checked PROPAGATE, frozen context, Risk Gate"),
    StrategyConfig("b1_full", "B1 full replan", "full", False, True,
                   "regenerate everything (except pinned facts); no frozen context"),
    StrategyConfig("b2_naive", "B2 naive downstream", "naive", False, True,
                   "every descendant of the changed node is stale; no frozen context"),
    StrategyConfig("abl_no_contracts", "Ablation: no contracts", "naive", True, True,
                   "ours without contract checks (all descendants stale), frozen context kept"),
    StrategyConfig("abl_no_frozen", "Ablation: no frozen context", "contract", False, True,
                   "ours without frozen context"),
    StrategyConfig("abl_no_gate", "Ablation: no Risk Gate", "contract", True, False,
                   "ours, but T2 actions execute immediately without approval"),
]

BY_NAME = {s.name: s for s in STRATEGIES}
