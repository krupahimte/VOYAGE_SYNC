"""
Phase 5 — Disruption Monitor and partial re-planning.

The key test mirrors plan §5.3: a 5-hour outbound delay regenerates the
airport transfer, day 1 and the budget check, while the hotel (and the rest of
the trip) stays untouched.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.agents.base import AgentRegistry, AgentResult, BaseAgent
from app.core.graph import GraphStore
from app.core.models import Edge, NodeStatus, NodeType, TaskNode
from app.core.monitor.apply import apply_event
from app.core.monitor.events import DisruptionEvent, DisruptionType
from app.core.monitor.monitor import DisruptionMonitor
from app.core.monitor.simulator import PRESETS, DisruptionSimulator
from app.core.orchestrator import Orchestrator, check_graph_contracts
from app.core.replanner import Replanner, _descendants
from app.core.scheduler import Scheduler
from app.integrations.weather import DailyWeather
from app.schemas.trip import TripGoal

SANTORINI = TripGoal(destination="Santorini", departure_city="Mumbai", departure_date=date(2027, 6, 14),
                     return_date=date(2027, 6, 19), travelers=1, budget=4000, currency="USD",
                     travel_style="luxury", interests=["relaxation", "food"])


@pytest.fixture
async def planned():
    orch = Orchestrator()
    result = await orch.plan_trip(SANTORINI)
    assert result.success
    return orch, result.graph_store


def snapshot(store: GraphStore) -> dict[str, tuple[int, Any]]:
    return {n.id: (n.version, n.output) for n in store.list_nodes()}


def untouched(before, store, exclude) -> list[str]:
    after = snapshot(store)
    return [nid for nid in before if nid not in exclude and before[nid] != after[nid]]


async def disrupt(orch, store, preset, **kwargs):
    return await orch.handle_disruption(store, DisruptionSimulator(1).preset(preset), **kwargs)


def all_hold(store) -> bool:
    return all(c.holds for c in check_graph_contracts(store))


# ── The key test (plan §5.3) ──────────────────────────────────────────────────

class TestSantoriniDelay:
    async def test_five_hour_delay_regenerates_transfer_day1_budget_only(self, planned):
        orch, store = planned
        before = snapshot(store)
        hotel_before = store.get_node("hotel_stay")

        outcome = await disrupt(orch, store, "delay_5h")

        assert outcome.status == "replanned"
        assert outcome.applied.broken_edges == [("flight_outbound", "airport_transfer_in")]
        replan = outcome.replan
        assert replan.stale == ["airport_transfer_in", "day_1_plan", "budget_check"]
        assert sorted(replan.regenerated) == sorted(["airport_transfer_in", "day_1_plan", "budget_check"])
        assert replan.failed == []
        # The hotel is untouched: same version, same output.
        hotel_after = store.get_node("hotel_stay")
        assert (hotel_after.version, hotel_after.output) == (hotel_before.version, hotel_before.output)
        # Nothing else changed except the delayed flight itself.
        assert untouched(before, store, {"flight_outbound", "airport_transfer_in", "day_1_plan",
                                         "budget_check"}) == []
        assert all_hold(store) and replan.contracts_holding == replan.contracts_total == 27

    async def test_new_facts_flow_downstream(self, planned):
        orch, store = planned
        await disrupt(orch, store, "delay_5h")
        flight = store.get_node("flight_outbound").output
        assert flight["arrival_time"] == "2027-06-14T19:00:00" and flight["status"] == "delayed"
        assert flight["scheduled_arrival_time"] == "2027-06-14T14:00:00"
        transfer = store.get_node("airport_transfer_in").output
        assert transfer["pickup_time"] == "2027-06-14T19:30:00"
        day1 = store.get_node("day_1_plan").output
        assert day1["start_time"] > transfer["dropoff_time"]
        budget = store.get_node("budget_check").output
        assert budget["line_items"]["day_1_plan"] == day1["cost"]

    async def test_history_shows_stale_then_regenerated(self, planned):
        orch, store = planned
        await disrupt(orch, store, "delay_5h")
        statuses = [h.status for h in store.get_history("day_1_plan")]
        assert NodeStatus.STALE in statuses
        assert statuses[-1] == NodeStatus.DONE
        assert any("TRANSFER_BEFORE_DAY_START broken" in h.reason for h in store.get_history("day_1_plan"))

    async def test_each_node_regenerated_once(self, planned):
        orch, store = planned
        outcome = await disrupt(orch, store, "hotel_overbooked")
        assert len(outcome.replan.regenerated) == len(set(outcome.replan.regenerated))

    async def test_usage_and_latency_recorded(self, planned):
        orch, store = planned
        outcome = await disrupt(orch, store, "delay_5h")
        assert outcome.replan.llm_calls == 1                  # only day 1 needed new descriptions
        assert outcome.replan.latency_ms > 0 and outcome.latency_ms >= outcome.replan.latency_ms
        assert outcome.replan.contract_checks > 0


# ── Monitor behaviour: no-effect, dedup, invalid ──────────────────────────────

class TestMonitor:
    async def test_ten_minute_delay_has_no_effect(self, planned):
        orch, store = planned
        before = snapshot(store)
        outcome = await disrupt(orch, store, "delay_10m")
        assert outcome.status == "no_effect" and outcome.replan is None
        assert store.get_node("flight_outbound").output["arrival_time"] == "2027-06-14T14:10:00"
        assert untouched(before, store, {"flight_outbound"}) == []
        assert all_hold(store)

    async def test_repeated_alert_is_deduplicated(self, planned):
        orch, store = planned
        await disrupt(orch, store, "delay_5h")
        before = snapshot(store)
        again = await disrupt(orch, store, "delay_5h")
        assert again.status == "duplicate" and snapshot(store) == before

    async def test_larger_delay_after_smaller_one(self, planned):
        orch, store = planned
        first = await disrupt(orch, store, "delay_2h")
        second = await disrupt(orch, store, "delay_5h")
        assert second.status == "replanned"
        assert store.get_node("flight_outbound").output["arrival_time"] == "2027-06-14T19:00:00"
        assert all_hold(store), (first.status, second.replan.regenerated)

    async def test_invalid_targets(self, planned):
        orch, store = planned
        wrong_type = DisruptionEvent(type=DisruptionType.FLIGHT_DELAY, target_node="hotel_stay",
                                     new_facts={"delay_minutes": 60})
        missing = DisruptionEvent(type=DisruptionType.WEATHER_STORM, target_node="weather_day_99")
        assert (await orch.handle_disruption(store, wrong_type)).status == "invalid"
        assert (await orch.handle_disruption(store, missing)).status == "invalid"

    def test_event_validation(self):
        with pytest.raises(ValueError):
            DisruptionEvent(type=DisruptionType.FLIGHT_DELAY, target_node="f", new_facts={"delay_minutes": -5})
        with pytest.raises(ValueError):
            DisruptionEvent(type=DisruptionType.PRICE_SPIKE, target_node="f", new_facts={})
        assert DisruptionEvent(type=DisruptionType.FLIGHT_DELAY, target_node="f",
                               new_facts={"delay_minutes": 300}).severity == "high"


# ── Other disruption types ────────────────────────────────────────────────────

class TestDisruptionTypes:
    async def test_cancellation_with_frozen_context_keeps_arrival(self, planned):
        orch, store = planned
        old = store.get_node("flight_outbound").output
        outcome = await disrupt(orch, store, "cancel_outbound")
        new = store.get_node("flight_outbound").output
        assert outcome.status == "replanned"
        assert new["offer_id"] != old["offer_id"] and new["selection"] == "least-change"
        assert new["arrival_time"] == old["arrival_time"]              # a later departure would move it
        assert sorted(outcome.replan.regenerated) == ["budget_check", "flight_outbound"]
        assert old["offer_id"] in store.get_node("flight_outbound").inputs["exclude_offer_ids"]
        assert all_hold(store)

    async def test_cancellation_without_frozen_context_disturbs_more(self, planned):
        orch, store = planned
        outcome = await disrupt(orch, store, "cancel_outbound", use_frozen_context=False)
        new = store.get_node("flight_outbound").output
        assert new["selection"] == "cheapest" and new["arrival_time"] != "2027-06-14T14:00:00"
        assert {"airport_transfer_in", "day_1_plan"} <= set(outcome.replan.regenerated)
        assert all_hold(store)

    async def test_stale_cancellation_alert_is_duplicate(self, planned):
        orch, store = planned
        old_offer = store.get_node("flight_outbound").output["offer_id"]
        await disrupt(orch, store, "cancel_outbound")
        late_alert = DisruptionEvent(type=DisruptionType.FLIGHT_CANCELLATION, target_node="flight_outbound",
                                     new_facts={"offer_id": old_offer})
        assert (await orch.handle_disruption(store, late_alert)).status == "duplicate"

    async def test_hotel_overbooked(self, planned):
        orch, store = planned
        before = snapshot(store)
        old = store.get_node("hotel_stay").output
        outcome = await disrupt(orch, store, "hotel_overbooked")
        new = store.get_node("hotel_stay").output
        assert new["hotel_id"] != old["hotel_id"]
        assert {"airport_transfer_in", "airport_transfer_out", "budget_check"} <= set(outcome.replan.regenerated)
        assert {f"day_{d}_plan" for d in range(1, 6)} <= set(outcome.replan.regenerated)
        # flights and weather untouched
        assert untouched(before, store, set(outcome.replan.regenerated)) == []
        # frozen context: day plans keep their activities, now based at the new hotel
        day2_before = before["day_2_plan"][1]
        day2 = store.get_node("day_2_plan").output
        assert [a["name"] for a in day2["activities"]] == [a["name"] for a in day2_before["activities"]]
        assert day2["hotel_id"] == new["hotel_id"]
        assert all_hold(store)

    async def test_storm_makes_day_indoor_only(self, planned):
        orch, store = planned
        before = snapshot(store)
        assert any(a["outdoor"] for a in store.get_node("day_3_plan").output["activities"])
        outcome = await disrupt(orch, store, "storm_day_3")
        assert outcome.replan.stale[0] == "day_3_plan"
        day3 = store.get_node("day_3_plan").output
        assert not any(a["outdoor"] for a in day3["activities"])
        assert untouched(before, store, {"weather_day_3", "day_3_plan", "budget_check"}) == []
        assert all_hold(store)

    async def test_pinned_storm_survives_full_replan(self, planned):
        orch, store = planned
        await disrupt(orch, store, "storm_day_3")
        await disrupt(orch, store, "delay_5h", strategy="full")
        assert store.get_node("weather_day_3").output["is_storm"] is True
        assert not any(a["outdoor"] for a in store.get_node("day_3_plan").output["activities"])
        assert all_hold(store)

    async def test_price_spike_only_updates_budget(self, planned):
        orch, store = planned
        total_before = store.get_node("budget_check").output["total_cost"]
        cost_before = store.get_node("hotel_stay").output["cost"]
        outcome = await disrupt(orch, store, "price_spike_hotel")
        assert outcome.replan.regenerated == ["budget_check"]
        hotel = store.get_node("hotel_stay").output
        assert hotel["cost"] == pytest.approx(cost_before * 1.25, abs=0.01)
        assert store.get_node("budget_check").output["total_cost"] == pytest.approx(
            total_before + hotel["cost"] - cost_before, abs=0.02)


# ── Baselines B1 / B2 ─────────────────────────────────────────────────────────

class TestBaselines:
    async def test_full_replan_regenerates_everything_but_the_facts(self, planned):
        orch, store = planned
        outcome = await disrupt(orch, store, "delay_5h", strategy="full")
        assert outcome.status == "replanned"
        assert set(outcome.replan.regenerated) == {n.id for n in store.list_nodes()} - {"flight_outbound"}
        assert outcome.replan.llm_calls >= 5
        assert all_hold(store)

    async def test_naive_marks_every_descendant(self, planned):
        orch, store = planned
        descendants = set(_descendants(store, "flight_outbound"))
        outcome = await disrupt(orch, store, "delay_5h", strategy="naive")
        assert set(outcome.replan.regenerated) == descendants
        assert "hotel_stay" in descendants
        assert all_hold(store)

    async def test_ours_is_a_subset_of_naive_and_full(self):
        sizes = {}
        for strategy in ("contract", "naive", "full"):
            orch = Orchestrator()
            store = (await orch.plan_trip(SANTORINI)).graph_store
            sizes[strategy] = set((await disrupt(orch, store, "delay_5h", strategy=strategy)).replan.regenerated)
        assert sizes["contract"] < sizes["naive"] < sizes["full"]
        assert len(sizes["contract"]) == 3

    async def test_baselines_replan_even_without_effect(self, planned):
        orch, store = planned
        outcome = await disrupt(orch, store, "delay_10m", strategy="naive")
        assert outcome.status == "replanned" and outcome.replan.regenerated


# ── Simulator, polling, scheduler context ────────────────────────────────────

class TestSimulatorAndPolling:
    def test_simulator_is_seeded(self, planned):
        _, store = planned
        a = [DisruptionSimulator(7).random_event(store) for _ in range(1)]
        sim1, sim2 = DisruptionSimulator(7), DisruptionSimulator(7)
        seq1 = [sim1.random_event(store).model_dump(exclude={"timestamp"}) for _ in range(10)]
        seq2 = [sim2.random_event(store).model_dump(exclude={"timestamp"}) for _ in range(10)]
        assert seq1 == seq2 and a
        assert len({(e["type"], e["target_node"]) for e in seq1}) > 3

    def test_presets_are_valid(self):
        sim = DisruptionSimulator()
        for name in PRESETS:
            assert sim.preset(name).target_node
        with pytest.raises(KeyError):
            sim.preset("meteor_strike")

    async def test_every_preset_keeps_the_plan_valid(self):
        for name in PRESETS:
            orch = Orchestrator()
            store = (await orch.plan_trip(SANTORINI)).graph_store
            outcome = await disrupt(orch, store, name)
            assert outcome.status in ("replanned", "no_effect"), (name, outcome.message)
            assert all_hold(store), name

    async def test_weather_poll_turns_forecast_storm_into_replan(self):
        orch = Orchestrator()
        soon = date.today() + timedelta(days=5)
        goal = SANTORINI.model_copy(update={"departure_date": soon, "return_date": soon + timedelta(days=4)})
        store = (await orch.plan_trip(goal)).graph_store
        providers = orch.registry.providers
        storm_day = (soon + timedelta(days=1)).isoformat()

        class Forecast:
            name = "forecast"

            async def daily(self, location, start, end):
                return [DailyWeather(date=start, condition="thunderstorm" if start.isoformat() == storm_day else "sunny",
                                     is_storm=start.isoformat() == storm_day, temperature_high_c=22,
                                     temperature_low_c=17, source="open-meteo-forecast")]

        providers.weather = Forecast()
        monitor = DisruptionMonitor(Replanner(orch.registry))
        events = await monitor.poll_weather(store, providers)
        assert [e.target_node for e in events] == ["weather_day_2"]
        assert events[0].source == "live:open-meteo-forecast"
        outcome = await monitor.handle(store, events[0])
        assert outcome.status in ("replanned", "no_effect")
        assert not any(a["outdoor"] for a in store.get_node("day_2_plan").output["activities"])

    async def test_scheduler_passes_per_node_context(self):
        seen: dict[str, dict] = {}

        class Capture(BaseAgent):
            async def run(self, node, resolved_inputs, frozen_context):
                seen[node.id] = dict(frozen_context)
                return AgentResult.ok({"ok": True})

        store = GraphStore("g")
        for nid in ("a", "b"):
            store.add_node(TaskNode(id=nid, type=NodeType.WEATHER, agent="cap"))
        store.add_edge(Edge(source_id="a", destination_id="b", contract="BUDGET_WITHIN_LIMIT"))
        reg = AgentRegistry()
        reg.register_by_name("cap", Capture())
        await Scheduler(reg).run(store, context={"shared": 1}, node_context={"b": {"previous_output": {"x": 1}}})
        assert seen["a"] == {"shared": 1, "graph_id": "g"}
        assert seen["b"]["previous_output"] == {"x": 1} and seen["b"]["shared"] == 1

    async def test_apply_event_reports_broken_edges_before_replanning(self, planned):
        _, store = planned
        outcome = apply_event(store, DisruptionSimulator().preset("delay_5h"))
        assert outcome.kind == "facts" and outcome.has_effect
        assert store.get_node("airport_transfer_in").status == NodeStatus.DONE   # not touched yet
