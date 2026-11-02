"""
Hybrid work, Phase 2 — the read-only GraphView, the hard-constraint validator
and the four-term objective with the feasibility-first ordering.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.scenario_set import load_scenarios, load_trips  # noqa: E402

from app.core.graph import NodeNotFoundError  # noqa: E402
from app.core.monitor.apply import apply_event  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.orchestrator import Orchestrator, check_graph_contracts  # noqa: E402
from app.core.repair.constraints import ValidationReport, empty_day_allowed, validate_plan  # noqa: E402
from app.core.repair.objective import ObjectiveWeights, deb_key, item_change, score, terms  # noqa: E402
from app.core.repair.view import GraphView  # noqa: E402
from app.core.models import NodeType  # noqa: E402
from app.core.scheduler import Scheduler  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402

TRIP = "santorini_solo_luxury"
_planned: dict[str, tuple] = {}


async def planned(trip_id: str = TRIP):
    """(orchestrator, planned store, goal), planned once per trip in mock mode."""
    if trip_id not in _planned:
        orch = Orchestrator()
        goal = TripGoal.model_validate(load_trips()[trip_id]["goal"])
        result = await orch.plan_trip(goal)
        assert result.success
        _planned[trip_id] = (orch, result.graph_store, goal)
    return _planned[trip_id]


def shifted(iso: str, minutes: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()


class TestGraphView:
    async def test_overrides_are_read_through_the_existing_graph_readers(self):
        _, store, _ = await planned()
        flight = dict(store.get_node("flight_outbound").output, arrival_time="2027-06-14T19:00:00")
        view = GraphView(store).with_output("flight_outbound", flight)
        assert view.get_node("flight_outbound").output["arrival_time"] == "2027-06-14T19:00:00"
        ri = Scheduler._resolve_inputs(None, view, "airport_transfer_in")
        assert ri["_parents"]["flight_outbound"]["output"]["arrival_time"] == "2027-06-14T19:00:00"
        assert view.validate() == store.validate() and view.list_edges() == store.list_edges()

    async def test_the_store_is_never_written(self):
        _, store, _ = await planned()
        before = copy.deepcopy(store.to_dict())
        view = GraphView(store)
        changed = view.with_output("hotel_stay", {"hotel_id": "x"})
        changed.list_nodes()
        Scheduler._resolve_inputs(None, changed, "day_1_plan")
        assert store.to_dict() == before
        assert view.overrides == {} and set(changed.overrides) == {"hotel_stay"}

    async def test_unknown_nodes(self):
        _, store, _ = await planned()
        with pytest.raises(KeyError):
            GraphView(store, {"no_such_node": {}})
        with pytest.raises(NodeNotFoundError):
            GraphView(store).get_node("no_such_node")


class TestValidatePlan:
    @pytest.mark.parametrize("trip_id", list(load_trips()))
    async def test_every_planned_trip_is_feasible(self, trip_id):
        _, store, goal = await planned(trip_id)
        report = validate_plan(store, store, set(), goal)
        assert report.feasible, report.violations()

    async def test_broken_contract(self):
        _, store, goal = await planned()
        transfer = store.get_node("airport_transfer_in").output
        early = dict(transfer, pickup_time=shifted(transfer["pickup_time"], -120),
                     dropoff_time=shifted(transfer["dropoff_time"], -120))
        report = validate_plan(GraphView(store).with_output("airport_transfer_in", early), store,
                               {"airport_transfer_in"}, goal)
        assert not report.feasible and report.contracts and not report.schemas
        assert any("TRANSFER_AFTER_FLIGHT" in c for c in report.contracts)

    async def test_change_outside_the_region_is_a_frozen_violation(self):
        _, store, goal = await planned()
        day = dict(store.get_node("day_3_plan").output, theme="something else")
        report = validate_plan(GraphView(store).with_output("day_3_plan", day), store, set(), goal)
        assert report.frozen == ["day_3_plan changed outside the repair region"]
        assert validate_plan(GraphView(store).with_output("day_3_plan", day), store, {"day_3_plan"}, goal).feasible

    async def test_invalid_output_schema(self):
        _, store, goal = await planned()
        transfer = store.get_node("airport_transfer_in").output
        backwards = dict(transfer, dropoff_time=shifted(transfer["pickup_time"], -5))
        report = validate_plan(GraphView(store).with_output("airport_transfer_in", backwards), store,
                               {"airport_transfer_in"}, goal)
        assert any("dropoff_time must be after pickup_time" in s for s in report.schemas)

    async def test_excluded_hotel_cannot_be_reused(self):
        _, planned_store, goal = await planned()
        store = copy.deepcopy(planned_store)
        hotel = store.get_node("hotel_stay")
        store.set_node_inputs("hotel_stay", {**hotel.inputs, "exclude_hotel_ids": [hotel.output["hotel_id"]]})
        report = validate_plan(store, store, {"hotel_stay"}, goal)
        assert report.exclusions == [f"hotel_stay uses excluded hotel_id {hotel.output['hotel_id']}"]

    async def test_party_size_is_a_hard_requirement(self):
        _, store, goal = await planned()
        fewer = dict(store.get_node("flight_return").output, passengers=goal.travelers + 1)
        report = validate_plan(GraphView(store).with_output("flight_return", fewer), store, {"flight_return"}, goal)
        assert report.requirements == ["party_size not met"]

    async def test_budget_is_not_a_hard_constraint(self):
        _, store, goal = await planned()
        assert validate_plan(store, store, set(), goal.model_copy(update={"budget": 1})).feasible

    async def test_empty_day_rule(self):
        _, store, goal = await planned()
        empty = lambda nid: dict(store.get_node(nid).output, activities=[], cost=0.0,  # noqa: E731
                                 start_time=None, end_time=None)
        view = GraphView(store).with_output("day_2_plan", empty("day_2_plan"))
        assert validate_plan(view, store, {"day_2_plan"}, goal).activities == \
            ["day_2_plan has no activities although the day has time for them"]
        transfer = store.get_node("airport_transfer_in").output
        day1 = store.get_node("day_1_plan").output["date"]
        for dropoff, allowed in ((f"{day1}T22:30:00", False), (f"{day1}T22:55:00", True), (f"{day1}T23:30:00", True)):
            late = GraphView(store).with_output("airport_transfer_in", dict(transfer, dropoff_time=dropoff)) \
                .with_output("day_1_plan", empty("day_1_plan"))
            assert empty_day_allowed(late, "day_1_plan") is allowed, dropoff

    async def test_agrees_with_e1_on_every_scenario(self):
        """E1's repaired plans: frozen tasks untouched everywhere, and only the 10-hour delays are infeasible."""
        infeasible = set()
        for s in load_scenarios():
            orch, store, goal = await planned(s["trip"])
            event = DisruptionSimulator(seed=0).make(s["type"], s["target"], **s["facts"])
            reference, after = copy.deepcopy(store), copy.deepcopy(store)
            apply_event(reference, event)
            outcome = await orch.handle_disruption(after, event)
            report = validate_plan(after, reference, outcome.replan.stale if outcome.replan else [], goal)
            assert len(report.contracts) == sum(not c.holds for c in check_graph_contracts(after)), s["id"]
            assert report.violations() == [v for v in report.violations() if v.startswith("contracts:")], s["id"]
            if not report.feasible:
                infeasible.add(s["id"])
        assert infeasible == {s["id"] for s in load_scenarios() if s["id"].endswith("outbound_delay_600")}


class TestObjective:
    async def test_unchanged_plan_scores_zero(self):
        _, store, _ = await planned()
        assert score(store, GraphView(store), "flight_outbound").fitness == 0
        assert set(terms(store, store, "flight_outbound").values()) == {0.0}

    async def test_disruption_per_task(self):
        _, store, _ = await planned()
        transfer = store.get_node("airport_transfer_in").output
        moved = dict(transfer, pickup_time=shifted(transfer["pickup_time"], 90))
        assert item_change(NodeType.TRANSFER, transfer, moved) == 0.5
        day = store.get_node("day_2_plan").output
        half = len(day["activities"]) // 2
        renamed = dict(day, activities=[dict(a, name=f"other {i}") if i < half else a
                                        for i, a in enumerate(day["activities"])])
        assert item_change(NodeType.DAY_PLAN, day, renamed) == pytest.approx(half / len(day["activities"]))
        hotel = store.get_node("hotel_stay").output
        assert item_change(NodeType.STAY, hotel, dict(hotel, hotel_id="other")) == 1.0
        visible = [n for n in store.list_nodes()
                   if n.type in (NodeType.FLIGHT, NodeType.STAY, NodeType.TRANSFER, NodeType.DAY_PLAN)]
        view = GraphView(store).with_output("airport_transfer_in", moved)
        assert terms(store, view, "flight_outbound")["disruption"] == pytest.approx(0.5 / (len(visible) - 1), abs=1e-6)

    async def test_the_disrupted_task_is_not_counted(self):
        _, store, _ = await planned()
        flight = dict(store.get_node("flight_outbound").output, flight_number="XX1", offer_id="other")
        assert terms(store, GraphView(store).with_output("flight_outbound", flight), "flight_outbound")[
            "disruption"] == 0.0

    async def test_money_counts_overrun_twice_and_savings_negatively(self):
        _, store, _ = await planned()
        budget = store.get_node("budget_check").output
        limit, total = budget["budget_limit"], budget["total_cost"]
        with_total = lambda t: GraphView(store).with_output("budget_check", dict(budget, total_cost=t))  # noqa: E731
        assert terms(store, with_total(total + 0.01 * limit), "x")["money"] == pytest.approx(0.01, abs=1e-6)
        assert terms(store, with_total(total - 0.02 * limit), "x")["money"] == pytest.approx(-0.02, abs=1e-6)
        over = limit + 0.1 * limit                      # 0.1 above the limit, from below or at it
        expected = (over - total) / limit + max(0.0, over - limit - max(0.0, total - limit)) / limit
        assert terms(store, with_total(over), "x")["money"] == pytest.approx(expected, abs=1e-6)

    async def test_lost_activity_time_is_not_offset_by_other_days(self):
        _, store, _ = await planned()
        d2, d3 = store.get_node("day_2_plan").output, store.get_node("day_3_plan").output
        lost = d2["activities"][0]["duration_minutes"]
        shorter = dict(d2, activities=d2["activities"][1:])
        longer = dict(d3, activities=d3["activities"] + [dict(d3["activities"][-1], name="extra")])
        view = GraphView(store).with_output("day_2_plan", shorter).with_output("day_3_plan", longer)
        planned_minutes = sum(a["duration_minutes"] for n in store.list_nodes() if n.type == NodeType.DAY_PLAN
                              for a in n.output["activities"])
        assert terms(store, view, "x")["activity_time_lost"] == pytest.approx(lost / planned_minutes, abs=1e-6)

    async def test_approvals_count_rebooked_items(self):
        _, store, _ = await planned()
        booked = {nid: dict(store.get_node(nid).output, booking_status="booked")
                  for nid in ("flight_outbound", "flight_return", "hotel_stay")}
        reference = GraphView(store, booked)
        rebooked = reference.with_output("hotel_stay", dict(booked["hotel_stay"], offer_id="other"))
        assert terms(reference, rebooked, "x")["approvals"] == pytest.approx(1 / 3, abs=1e-6)
        unbooked = GraphView(store).with_output("hotel_stay", dict(store.get_node("hotel_stay").output, offer_id="o"))
        assert terms(store, unbooked, "x")["approvals"] == 0.0

    async def test_weights(self):
        _, store, _ = await planned()
        transfer = store.get_node("airport_transfer_in").output
        view = GraphView(store).with_output("airport_transfer_in",
                                            dict(transfer, pickup_time=shifted(transfer["pickup_time"], 180)))
        base = score(store, view, "x")
        doubled = score(store, view, "x", ObjectiveWeights(disruption=2.0))
        assert doubled.fitness == pytest.approx(2 * base.fitness, abs=1e-6) and base.fitness < 0


class TestDebOrdering:
    def test_feasible_first_then_fewer_violations_then_fitness(self):
        ok, one, two = ValidationReport(), ValidationReport(contracts=["a"]), ValidationReport(contracts=["a", "b"])
        ranked = sorted([(two, 0.0), (ok, -0.9), (one, -0.1), (ok, -0.2)], key=lambda p: deb_key(*p))
        assert [(r.violation_count, f) for r, f in ranked] == [(0, -0.2), (0, -0.9), (1, -0.1), (2, 0.0)]
