"""
Phase 8 — evaluation harness: scenario suite, market drift, the oracle against
the hand labels, the "no Risk Gate" ablation, and a small benchmark run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.baselines import BY_NAME, STRATEGIES  # noqa: E402
from eval.oracle import expected_for, load_labels  # noqa: E402
from eval.scenario_set import expand, load_scenarios, load_trips  # noqa: E402

from app.core.risk_gate import ActionLedger, unsafe_action_rate, unsafe_actions  # noqa: E402
from app.integrations.drift import Drift  # noqa: E402


class TestScenarioSuite:
    def test_at_least_100_unique_scenarios(self):
        scenarios = load_scenarios()
        assert len(scenarios) >= 100
        assert len({s["id"] for s in scenarios}) == len(scenarios)

    def test_generated_file_is_up_to_date(self):
        assert load_scenarios() == expand()

    def test_coverage(self):
        trips = load_trips()
        assert len(trips) >= 10
        tags = {t for trip in trips.values() for t in trip["tags"]}
        assert {"domestic", "international", "solo", "group"} <= tags
        scenarios = load_scenarios()
        assert len({s["kind"] for s in scenarios}) >= 9
        assert {s["severity"] for s in scenarios} == {"low", "medium", "high", "extreme"}

    def test_labelled_subset(self):
        labels, trips = load_labels(), load_trips()
        labelled = [s for s in load_scenarios() if expected_for(s, trips[s["trip"]]["goal"], labels) is not None]
        assert len(labelled) >= 70


class TestDrift:
    def test_inactive_by_default(self):
        drift = Drift()
        assert drift.price("x", 100.0) == 100.0 and drift.available("x")
        assert drift.filter([1, 2, 3], str) == [1, 2, 3]

    def test_seeded_and_bounded(self):
        a, b = Drift(), Drift()
        a.enable("s1")
        b.enable("s1")
        prices = [a.price(f"k{i}", 100.0) for i in range(200)]
        assert prices == [b.price(f"k{i}", 100.0) for i in range(200)]
        assert all(92.0 <= p <= 108.0 for p in prices) and len(set(prices)) > 150
        kept = a.filter(list(range(200)), str)
        assert 140 < len(kept) < 200
        b.enable("s2")
        assert [b.price(f"k{i}", 100.0) for i in range(5)] != prices[:5]

    def test_never_empties_a_list(self):
        drift = Drift()
        drift.enable("x", drop_pct=1.0)
        assert drift.filter(["only"], str) == ["only"]


class TestStrategies:
    def test_ours_baselines_and_ablations(self):
        assert [s.name for s in STRATEGIES] == ["ours", "b1_full", "b2_naive", "abl_no_contracts",
                                               "abl_no_frozen", "abl_no_gate"]
        assert BY_NAME["abl_no_contracts"].strategy == "naive" and BY_NAME["abl_no_contracts"].frozen_context
        assert not BY_NAME["abl_no_gate"].risk_gate and BY_NAME["ours"].risk_gate


class TestBenchmarkRun:
    @pytest.fixture(scope="class")
    @classmethod
    def rows(cls):
        import asyncio

        from eval.run_benchmark import run

        picked = [s for s in load_scenarios() if s["trip"] == "goa_couple_domestic"
                  and s["kind"] in ("outbound_delay", "outbound_cancellation", "hotel_price_spike")]
        rows, meta = asyncio.run(run(picked, [True]))
        return rows, meta

    def test_oracle_matches_labels(self, rows):
        _, meta = rows
        validation = meta["oracle_validation"]
        assert validation["labelled"] >= 3 and validation["mismatches"] == []

    def test_ours_is_precise_and_safe(self, rows):
        data, _ = rows
        ours = [r for r in data if r["strategy"] == "ours"]
        assert all(r["recall"] == 1.0 for r in ours)
        assert all(r["unsafe_actions"] == 0 for r in data if r["strategy"] != "abl_no_gate")
        b1 = [r for r in data if r["strategy"] == "b1_full"]
        assert sum(r["regenerated_count"] for r in b1) > 3 * sum(r["regenerated_count"] for r in ours)

    def test_no_gate_ablation_is_unsafe(self, rows):
        data, _ = rows
        cancelled = [r for r in data if r["kind"] == "outbound_cancellation"]
        no_gate = next(r for r in cancelled if r["strategy"] == "abl_no_gate")
        ours = next(r for r in cancelled if r["strategy"] == "ours")
        assert no_gate["t2_raised"] >= 1 and no_gate["unsafe_actions"] == no_gate["t2_raised"]
        assert ours["t2_raised"] == no_gate["t2_raised"] and ours["unsafe_actions"] == 0


class TestUnsafeMetric:
    async def test_execute_without_approval_is_counted_unsafe(self):
        from datetime import date

        from app.core.orchestrator import Orchestrator
        from app.schemas.trip import TripGoal

        orch, ledger = Orchestrator(), ActionLedger()
        goal = TripGoal(destination="Goa", departure_city="Mumbai", departure_date=date(2027, 7, 1),
                        return_date=date(2027, 7, 4), travelers=2, budget=90000, currency="INR")
        store = (await orch.plan_trip(goal, ledger=ledger)).graph_store
        first, second = ledger.pending()[:2]
        await orch.approve_action(store, ledger, first.action_id)
        await orch.gate.execute_without_approval(store, ledger, second.action_id, orch.executor())
        executions = orch.registry.providers.booking.executions
        unsafe = unsafe_actions(ledger, executions)
        assert {u["action_id"] for u in unsafe} == {second.action_id}
        assert unsafe_action_rate(ledger, executions) == pytest.approx(1 / 3)
