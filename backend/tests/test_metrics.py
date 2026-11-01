"""
Phase 7 — per-replan metrics for the demo's metrics panel: the oracle, P/R/F1,
plan stability, and the B1 comparison (run on copies, never on the trip).
"""

from __future__ import annotations

import copy
from datetime import date

import pytest

from app.core.metrics import compare_with_baseline, oracle_affected, plan_stability, precision_recall_f1
from app.core.monitor.simulator import DisruptionSimulator
from app.core.orchestrator import Orchestrator
from app.core.risk_gate import ActionLedger
from app.schemas.trip import TripGoal

GOAL = TripGoal(destination="Santorini", departure_city="Mumbai", departure_date=date(2027, 6, 14),
                return_date=date(2027, 6, 19), travelers=1, budget=4000, currency="USD", travel_style="luxury",
                interests=["relaxation", "food"])


@pytest.fixture
async def planned():
    orch = Orchestrator()
    store = (await orch.plan_trip(GOAL)).graph_store
    return orch, store


class TestFormulas:
    def test_precision_recall_f1(self):
        assert precision_recall_f1(["a", "b", "c", "d"], ["a", "b"]) == (0.5, 1.0, 0.6667)
        assert precision_recall_f1(["a"], ["a", "b"]) == (1.0, 0.5, 0.6667)
        assert precision_recall_f1([], []) == (1.0, 1.0, 1.0)
        assert precision_recall_f1(["a"], []) == (0.0, 1.0, 0.0)
        assert precision_recall_f1([], ["a"]) == (0.0, 0.0, 0.0)

    async def test_stability_ignores_bookkeeping_prices_and_target(self, planned):
        _, store = planned
        after = copy.deepcopy(store)
        node = after.get_node("hotel_stay")
        after.update_node_status("hotel_stay", node.status, output={**node.output, "booking_ref": "BK1",
                                                                    "cost": node.output["cost"] * 1.05})
        assert plan_stability(store, after, set()) == 1.0          # same hotel, re-quoted price
        node = after.get_node("day_2_plan")
        acts = [dict(a) for a in node.output["activities"]]
        acts[0]["name"] = "Somewhere else"
        after.update_node_status("day_2_plan", node.status, output={**node.output, "activities": acts})
        assert plan_stability(store, after, set()) == 0.9          # 9 of 10 visible items unchanged
        assert plan_stability(store, after, {"day_2_plan"}) == 1.0


class TestOracle:
    @pytest.mark.parametrize("scenario,expected", [
        ("delay_5h", ["airport_transfer_in", "day_1_plan", "budget_check"]),
        ("delay_10m", []),
        ("storm_day_3", ["day_3_plan", "budget_check"]),
        ("price_spike_hotel", ["budget_check"]),
    ])
    async def test_affected_sets(self, planned, scenario, expected):
        orch, store = planned
        snapshot = copy.deepcopy(store.to_dict())
        affected = await oracle_affected(orch.registry, store, DisruptionSimulator(0).preset(scenario))
        assert affected == expected
        assert store.to_dict() == snapshot                       # the trip itself is untouched

    async def test_invalidated_target_is_affected(self, planned):
        orch, store = planned
        affected = await oracle_affected(orch.registry, store, DisruptionSimulator(0).preset("cancel_outbound"))
        assert affected[0] == "flight_outbound" and "budget_check" in affected


class TestCompare:
    async def test_delay_5h_ours_vs_b1(self, planned):
        orch, store = planned
        before = copy.deepcopy(store)
        event = DisruptionSimulator(0).preset("delay_5h")
        ledger = ActionLedger()
        outcome = await orch.handle_disruption(store, event, ledger=ledger)
        after_snapshot = copy.deepcopy(store.to_dict())
        metrics = await compare_with_baseline(orch, before, event, outcome, store)

        ours, b1 = metrics["replan"], metrics["b1"]
        assert metrics["oracle"]["affected"] == ["airport_transfer_in", "day_1_plan", "budget_check"]
        assert (ours["precision"], ours["recall"], ours["f1"]) == (1.0, 1.0, 1.0)
        assert ours["regenerated_count"] == 3 and b1["regenerated_count"] == 15
        assert b1["precision"] == 0.2 and b1["recall"] == 1.0
        assert ours["contracts_holding"] == ours["contracts_total"] == 27
        assert ours["stability"] == pytest.approx(7 / 9, abs=1e-4)
        assert store.to_dict() == after_snapshot                 # B1 ran on a copy
        assert len(ledger.list()) == 3                           # and raised no gate actions

    async def test_cancellation_b1_less_stable(self, planned):
        orch, store = planned
        before = copy.deepcopy(store)
        event = DisruptionSimulator(0).preset("cancel_outbound")
        outcome = await orch.handle_disruption(store, event)
        metrics = await compare_with_baseline(orch, before, event, outcome, store)
        assert metrics["replan"]["stability"] > metrics["b1"]["stability"]
        assert metrics["replan"]["precision"] > metrics["b1"]["precision"]


class TestEmptyDayContracts:
    """Found by the Phase 8 benchmark: a very late arrival leaves day 1 with no activities."""

    def test_empty_day_cannot_be_missed_or_overrun(self):
        from app.core.contracts import check_edge

        transfer = {"dropoff_time": "2027-06-14T23:40:00", "pickup_time": "2027-06-19T12:50:00"}
        empty_day = {"date": "2027-06-14", "activities": []}
        assert check_edge("TRANSFER_BEFORE_DAY_START", {"buffer_minutes": 30}, "t", transfer, empty_day).holds
        assert check_edge("DAY_ENDS_BEFORE_PICKUP", {"buffer_minutes": 60}, "d", empty_day, transfer).holds

    def test_day_without_activity_list_is_still_checked(self):
        from app.core.contracts import check_edge

        transfer = {"dropoff_time": "2027-06-14T23:40:00"}
        assert not check_edge("TRANSFER_BEFORE_DAY_START", {"buffer_minutes": 30}, "t", transfer,
                              {"date": "2027-06-14"}).holds
