"""
Evaluation metrics v2: executability, user requirements, task preservation, travel
cost, activity-time retention and recovery; the N/A cases (zero denominators,
nothing checkable); the real-LLM guard; scenario and legacy parity; and a small
benchmark run that must make zero LLM calls while computing metrics.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import metrics as M  # noqa: E402
from app.core.contracts import ContractResult  # noqa: E402
from app.core.graph import GraphStore  # noqa: E402
from app.core.models import NodeStatus  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.llm.client import OpenAICompatibleLLM  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402
from eval import metrics_v2 as V2  # noqa: E402

GOAL = TripGoal(destination="Santorini", departure_city="Mumbai", departure_date=date(2027, 6, 14),
                return_date=date(2027, 6, 19), travelers=1, budget=4000, currency="USD", travel_style="luxury",
                interests=["relaxation", "food"])


@pytest.fixture
async def planned():
    orch = Orchestrator()
    store = (await orch.plan_trip(GOAL)).graph_store
    return orch, store


def edit(store: GraphStore, node_id: str, *, status: NodeStatus | None = None, **changes) -> None:
    node = store.get_node(node_id)
    store.update_node_status(node_id, status or node.status, output={**(node.output or {}), **changes})


class TestRatio:
    def test_zero_denominator_is_none(self):
        assert M.ratio(0, 0) is None and M.ratio(5, 0) is None

    def test_rounds(self):
        assert M.ratio(1, 3) == 0.3333 and M.ratio(0, 4) == 0.0


class TestTaskPreservation:
    async def test_identical_plan_preserves_every_unaffected_task(self, planned):
        _, before = planned
        result = M.task_preservation(before, copy.deepcopy(before), ["day_1_plan", "budget_check"], "flight_outbound")
        assert result["unaffected"] == 16 - 3 and result["preserved"] == 13 and result["rate"] == 1.0
        assert result["visible_unaffected"] == 8 and result["visible_rate"] == 1.0

    async def test_only_unaffected_tasks_count(self, planned):
        _, before = planned
        after = copy.deepcopy(before)
        acts = [dict(a) for a in after.get_node("day_1_plan").output["activities"]]
        acts[0]["name"] = "Somewhere else"
        edit(after, "day_1_plan", activities=acts)                       # affected: ignored
        edit(after, "flight_outbound", arrival_time="2027-06-14T19:00:00")   # target: ignored
        assert M.task_preservation(before, after, ["day_1_plan"], "flight_outbound")["rate"] == 1.0

    async def test_changed_unaffected_task_lowers_the_rate(self, planned):
        _, before = planned
        after = copy.deepcopy(before)
        acts = [dict(a) for a in after.get_node("day_2_plan").output["activities"]]
        acts[0]["start"] = "2027-06-15T10:30:00"
        edit(after, "day_2_plan", activities=acts)
        result = M.task_preservation(before, after, [], "flight_outbound")
        assert result["unaffected"] == 15 and result["preserved"] == 14
        assert result["rate"] == round(14 / 15, 4) and result["visible_rate"] == round(8 / 9, 4)

    async def test_requoted_price_is_not_a_change_but_budget_total_is(self, planned):
        _, before = planned
        after = copy.deepcopy(before)
        edit(after, "hotel_stay", cost=999.0, booking_ref="BK1")         # visible: same hotel and dates
        assert M.task_preservation(before, after, [], "flight_outbound")["rate"] == 1.0
        edit(after, "budget_check", total_cost=1.0)                       # non-visible: whole output
        assert M.task_preservation(before, after, [], "flight_outbound")["preserved"] == 14

    async def test_empty_unaffected_set_is_not_applicable(self, planned):
        _, before = planned
        everything = [n.id for n in before.list_nodes()]
        result = M.task_preservation(before, before, everything, "flight_outbound")
        assert result["unaffected"] == 0 and result["rate"] is None and result["visible_rate"] is None


class TestExecutability:
    async def test_completed_plan_is_valid_and_ready(self, planned):
        _, store = planned
        result = M.executability(store)
        assert result["plan_valid"] and result["execution_ready"]
        assert result["task_plan_valid_rate"] == 1.0 and result["task_ready_rate"] == 1.0
        assert result["tasks"] == 16 and result["broken_contracts"] == 0

    async def test_awaiting_approval_is_valid_but_not_ready(self, planned):
        _, store = planned
        edit(store, "flight_outbound", status=NodeStatus.AWAITING_APPROVAL)
        result = M.executability(store)
        assert result["plan_valid"] is True and result["execution_ready"] is False
        assert result["awaiting_approval"] == 1
        assert result["task_plan_valid_rate"] == 1.0 and result["task_ready_rate"] == round(15 / 16, 4)

    async def test_stale_or_failed_task_breaks_completion(self, planned):
        _, store = planned
        store.update_node_status("day_3_plan", NodeStatus.STALE)
        result = M.executability(store)
        assert result["e1_completion"] is False and result["plan_valid"] is False
        assert result["task_plan_valid_rate"] == round(15 / 16, 4)

    async def test_invalid_output_fails_validation(self, planned):
        _, store = planned
        edit(store, "flight_return", arrival_time="2027-06-19T10:00:00")   # arrives before it departs (16:00)
        result = M.executability(store)
        assert result["e2_output_valid"] is False and result["invalid_outputs"] == 1
        assert result["plan_valid"] is False

    async def test_unevaluable_contract_is_distinguished_from_a_broken_one(self, planned):
        _, store = planned
        node = store.get_node("airport_transfer_in")
        store.update_node_status(node.id, node.status,
                                 output={k: v for k, v in node.output.items() if k != "dropoff_time"})
        result = M.executability(store)
        assert result["e3_dependencies_evaluable"] is False and result["unevaluable_contracts"] >= 1

    async def test_broken_contract(self, planned):
        orch, store = planned
        await orch.handle_disruption(store, DisruptionSimulator(0).preset("delay_5h"))
        assert M.executability(store)["plan_valid"] is True                # repaired
        edit(store, "airport_transfer_in", pickup_time="2027-06-14T14:30:00", dropoff_time="2027-06-14T14:40:00")
        result = M.executability(store)
        assert result["e3_dependencies_evaluable"] is True and result["e4_contracts_hold"] is False
        assert result["plan_valid"] is False and result["broken_contracts"] >= 1

    def test_empty_graph_is_not_applicable(self):
        result = M.executability(GraphStore("empty"))
        assert result["tasks"] == 0 and result["plan_valid"] is None and result["task_ready_rate"] is None


class TestContractEvaluable:
    @pytest.mark.parametrize("text,expected", [
        ("Invalid input — missing day_start_iso", False),
        ("Contract cannot be checked: a node has no output yet.", False),
        ("Transfer at 14:30 is 30 minutes after arrival", True),
    ])
    def test_prefixes(self, text, expected):
        assert M.contract_evaluable(ContractResult(False, text, "X")) is expected


class TestUserRequirements:
    async def test_planned_trip_meets_all(self, planned):
        _, store = planned
        assert M.user_requirements(GOAL, store) == {"dates": True, "party_size": True, "budget": True,
                                                    "interests": True}

    async def test_each_requirement_can_fail(self, planned):
        _, store = planned
        assert M.user_requirements(GOAL.model_copy(update={"budget": 1000}), store)["budget"] is False
        assert M.user_requirements(GOAL.model_copy(update={"travelers": 3}), store)["party_size"] is False
        moved = copy.deepcopy(store)
        edit(moved, "flight_return", departure_date="2027-06-20")
        assert M.user_requirements(GOAL, moved)["dates"] is False
        themed = copy.deepcopy(store)
        edit(themed, "day_2_plan", theme="Shopping day in Santorini")
        assert M.user_requirements(GOAL, themed)["interests"] is False

    async def test_not_checkable_is_none(self, planned):
        _, store = planned
        assert M.user_requirements(GOAL.model_copy(update={"interests": []}), store)["interests"] is None
        assert M.user_requirements(GOAL.model_copy(update={"currency": "EUR"}), store)["budget"] is None
        partial = GraphStore("partial")
        partial.add_node(copy.deepcopy(store.get_node("flight_outbound")))
        assert set(M.user_requirements(GOAL, partial).values()) == {None}

    def test_satisfaction_uses_checkable_only(self):
        assert M.requirement_satisfaction({"a": True, "b": False, "c": None}) == {"met": 1, "checkable": 2,
                                                                                  "rate": 0.5}
        assert M.requirement_satisfaction({"a": None}) == {"met": 0, "checkable": 0, "rate": None}

    def test_regressions(self):
        before = {"dates": True, "party_size": False, "budget": True, "interests": True}
        after = {"dates": False, "party_size": False, "budget": None, "interests": True}
        assert M.requirement_regressions(before, after) == ["dates"]     # False→False and True→None are not


class TestTravelCost:
    async def test_change_as_share_of_budget(self, planned):
        _, before = planned
        after = copy.deepcopy(before)
        total = before.get_node("budget_check").output["total_cost"]
        edit(after, "budget_check", total_cost=total + 400, within_budget=False)
        result = M.travel_cost_change(before, after)
        assert result["change_of_budget"] == 0.1 and result["within_budget_after"] is False

    async def test_missing_or_zero_limit_is_none(self, planned):
        _, before = planned
        assert M.travel_cost_change(before, GraphStore("x"))["change_of_budget"] is None
        zero = copy.deepcopy(before)
        edit(zero, "budget_check", budget_limit=0)
        edit(before, "budget_check", budget_limit=0)
        assert M.travel_cost_change(before, zero)["change_of_budget"] is None


class TestActivityRetention:
    async def test_affected_day_plans_only(self, planned):
        orch, before = planned
        after = copy.deepcopy(before)
        await orch.handle_disruption(after, DisruptionSimulator(0).preset("delay_5h"))
        result = M.activity_time_retention(before, after, ["airport_transfer_in", "day_1_plan", "budget_check"])
        assert result["days"] == 1 and result["minutes_after"] < result["minutes_before"]
        assert result["rate"] == round(result["minutes_after"] / result["minutes_before"], 4)

    async def test_no_affected_day_plan_is_not_applicable(self, planned):
        _, before = planned
        assert M.activity_time_retention(before, before, ["budget_check"])["rate"] is None

    async def test_zero_minutes_before_is_not_applicable(self, planned):
        _, before = planned
        edit(before, "day_2_plan", activities=[])
        assert M.activity_time_retention(before, before, ["day_2_plan"])["rate"] is None


class TestRecovery:
    def test_contract_recovery_is_the_existing_definition(self):
        assert M.contract_recovered(27, 27, 0) and not M.contract_recovered(26, 27, 0)
        assert not M.contract_recovered(27, 27, 1)

    def test_strict_recovery(self):
        assert M.strict_recovered(True, []) is True
        assert M.strict_recovered(True, ["budget"]) is False
        assert M.strict_recovered(False, []) is False and M.strict_recovered(None, []) is False


class TestNoLLM:
    async def test_metrics_make_no_llm_calls(self, planned):
        orch, before = planned
        after = copy.deepcopy(before)
        await orch.handle_disruption(after, DisruptionSimulator(0).preset("delay_5h"))
        calls = orch.llm.calls
        V2.run_metrics(goal=GOAL, before=before, after=after, affected=["airport_transfer_in", "day_1_plan"],
                       target="flight_outbound", selected=["airport_transfer_in", "day_1_plan"],
                       requirements_before=M.user_requirements(GOAL, before), contracts_holding=27,
                       contracts_total=27, failed=0, facts={"delay_minutes": 300})
        assert orch.llm.calls == calls

    async def test_real_llm_is_blocked(self):
        client = OpenAICompatibleLLM(api_key="test", model="m", base_url="http://127.0.0.1:9")
        with V2.no_real_llm() as guard:
            with pytest.raises(V2.RealLLMCalled):
                await client.complete([{"role": "user", "content": "hi"}])
        assert guard["real_llm_calls"] == 1

    def test_mock_llm_required(self):
        assert V2.assert_mock_llm(Orchestrator()) == "mock"
        live = OpenAICompatibleLLM(api_key="test", model="m", base_url="http://127.0.0.1:9")
        with pytest.raises(V2.RealLLMCalled):
            V2.assert_mock_llm(Orchestrator(llm=live))


class TestParity:
    def _row(self, strategy, affected="a;b", drift="on"):
        return {"scenario": "s1", "strategy": strategy, "drift": drift, "event_type": "FLIGHT_DELAY",
                "target": "flight_outbound", "facts": "{}", "affected": affected}

    def test_scenario_parity(self):
        ok = V2.scenario_parity([self._row("ours"), self._row("b1_full")])
        assert ok["event_or_oracle_mismatches"] == [] and ok["missing_strategy_runs"] == []
        bad = V2.scenario_parity([self._row("ours"), self._row("b1_full", affected="a")])
        assert bad["event_or_oracle_mismatches"] == ["s1"]
        missing = V2.scenario_parity([self._row("ours"), self._row("b1_full"),
                                      {**self._row("ours"), "scenario": "s2"}])
        assert missing["missing_strategy_runs"] == ["s2"]

    def test_legacy_parity(self, tmp_path):
        legacy = tmp_path / "results.csv"
        with open(legacy, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["scenario", "strategy", "precision", "latency_ms"])
            w.writeheader()
            w.writerow({"scenario": "s1", "strategy": "ours", "precision": 1.0, "latency_ms": 0.5})
        fields = ["scenario", "strategy", "precision", "latency_ms"]
        same = V2.legacy_parity(legacy, [{"scenario": "s1", "strategy": "ours", "precision": 1.0,
                                          "latency_ms": 9.9}], fields)
        assert same["compared"] == 1 and same["mismatch_count"] == 0
        diff = V2.legacy_parity(legacy, [{"scenario": "s1", "strategy": "ours", "precision": 0.5,
                                          "latency_ms": 0.5}], fields)
        assert diff["mismatch_count"] == 1 and diff["mismatches"][0]["field"] == "precision"
        assert V2.legacy_parity(tmp_path / "none.csv", [], fields) == {"file": str(tmp_path / "none.csv"),
                                                                        "available": False}


class TestAggregation:
    def test_stat_never_imputes(self):
        assert V2._stat([1.0, None, 0.0]) == {"value": 0.5, "n": 2, "na": 1}
        assert V2._stat([None, None]) == {"value": None, "n": 0, "na": 2}
        assert V2._stat([True, False, True, True]) == {"value": 0.75, "n": 4, "na": 0}


class TestDetails:
    def _row(self, strategy, budget_ok, after, kind="return_cancellation", change=0.1):
        return {"scenario": "s1", "drift": "on", "strategy": strategy, "kind": kind, "req_budget": budget_ok,
                "budget_limit": 3000.0, "cost_total_before": 2900.0, "cost_total_after": after,
                "cost_change_of_budget": change}

    def test_budget_worse_lists_only_runs_where_a_baseline_meets_it(self):
        rows = [self._row("ours", False, 3229.0), self._row("b2_naive", True, 2981.0),
                self._row("b1_full", False, 3100.0)]
        worse = V2._budget_worse(rows)
        assert worse == [{"scenario": "s1", "drift": "on", "budget": 3000.0, "before": 2900.0, "ours": 3229.0,
                          "b2_naive": 2981.0, "b1_full": 3100.0}]
        assert V2._budget_worse([self._row("ours", False, 1), self._row("b2_naive", False, 1)]) == []
        assert V2._budget_worse([self._row("ours", None, 1), self._row("b2_naive", True, 1)]) == []

    def test_cost_by_kind_skips_missing_values(self):
        rows = [self._row("ours", True, 1, change=0.1), self._row("ours", True, 1, change=None),
                self._row("b2_naive", True, 1, change=0.04)]
        assert V2._cost_by_kind(rows) == [{"kind": "return_cancellation", "drift": "on", "ours": 0.1,
                                           "b2_naive": 0.04, "b1_full": None}]


class TestBenchmarkV2:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls):
        from eval.run_benchmark import CSV_FIELDS, run
        from eval.scenario_set import load_scenarios

        picked = [s for s in load_scenarios() if s["trip"] == "goa_couple_domestic"
                  and s["kind"] in ("outbound_delay", "hotel_price_spike")]
        legacy_rows, _ = asyncio.run(run(picked, [True]))
        rows, meta = asyncio.run(run(picked, [True], v2=True))
        return legacy_rows, rows, meta, CSV_FIELDS

    def test_zero_llm_calls_for_metrics_and_no_real_llm(self, result):
        _, rows, meta, _ = result
        assert meta["real_llm_calls"] == 0 and meta["metric_llm_calls_total"] == 0
        assert meta["llm_providers"] == ["mock"] and meta["mode"] == "mock"
        assert all(r["metric_llm_calls"] == 0 for r in rows)

    def test_v2_columns_present_and_legacy_columns_unchanged(self, result):
        legacy_rows, rows, _, fields = result
        assert all(set(V2.V2_FIELDS) <= set(r) for r in rows)
        strip = lambda r: {k: r[k] for k in fields if k != "latency_ms"}  # noqa: E731
        assert [strip(r) for r in rows] == [strip(r) for r in legacy_rows]

    def test_identical_scenarios_for_every_strategy(self, result):
        _, rows, _, _ = result
        parity = V2.scenario_parity(rows)
        assert parity["event_or_oracle_mismatches"] == [] and parity["missing_strategy_runs"] == []

    def test_ours_preserves_more_and_regenerates_less_than_b1(self, result):
        _, rows, _, _ = result
        mean = lambda name, key: sum(r[key] for r in rows if r["strategy"] == name) / sum(  # noqa: E731
            1 for r in rows if r["strategy"] == name)
        assert mean("ours", "tpr") > mean("b1_full", "tpr")
        assert mean("ours", "replanning_scope") < mean("b1_full", "replanning_scope")
