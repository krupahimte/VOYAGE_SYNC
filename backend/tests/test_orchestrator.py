"""
Phase 3 — Orchestrator tests.

Coverage:
  - Valid decomposition (MockLLM) → graph of the §5.3 shape.
  - Invalid LLM output (bad JSON, schema errors, rule violations, call errors)
    → retry with the validation error → template fallback after N failures.
  - LLM-written edges/contracts are ignored; contracts come from templates.
  - Graph-contract validity: every edge contract holds on the executed plan.
  - New contract templates, LLM client selection, memory defaults.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest

from app.agents.mocks import build_default_registry
from app.config import Settings
from app.core.contracts import EDGE_BOUND_CONTRACTS, KNOWN_CONTRACTS, check_edge, evaluate_contract
from app.core.models import NodeStatus, NodeType
from app.core.orchestrator import (
    Orchestrator,
    apply_profile_defaults,
    check_graph_contracts,
    validate_proposal,
)
from app.core.templates import propose_template_tasks
from app.llm.client import (
    LLMError,
    MockLLM,
    OpenAICompatibleLLM,
    extract_json_object,
    get_llm_client,
)
from app.schemas.trip import ProposedPlan, TripGoal


# ── Fixtures ──────────────────────────────────────────────────────────────────

def santorini(**overrides) -> TripGoal:
    data = dict(destination="Santorini", departure_city="Mumbai",
                departure_date=date(2026, 6, 14), return_date=date(2026, 6, 19),
                travelers=1, budget=4000, currency="USD", travel_style="luxury",
                interests=["relaxation", "food"])
    data.update(overrides)
    return TripGoal(**data)


def template_json(goal: TripGoal | None = None) -> str:
    return propose_template_tasks(goal or santorini()).model_dump_json()


def make_orchestrator(llm: MockLLM, max_attempts: int = 3) -> Orchestrator:
    return Orchestrator(llm=llm, registry=build_default_registry(), max_attempts=max_attempts)


def edge_map(store):
    return {(e.source_id, e.destination_id): e.contract for e in store.list_edges()}


# ── Valid decomposition ───────────────────────────────────────────────────────

class TestValidDecomposition:
    async def test_mock_llm_plan_accepted_first_try(self):
        llm = MockLLM()
        result = await make_orchestrator(llm).plan_trip(santorini())
        assert result.success, result.error
        assert result.decomposition.source == "llm"
        assert result.decomposition.attempts == 1
        assert result.decomposition.errors == []
        assert llm.calls == 1

    async def test_graph_has_expected_nodes(self):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        ids = {n.id for n in result.graph_store.list_nodes()}
        expected = {"flight_outbound", "flight_return", "hotel_stay", "airport_transfer_in",
                    "airport_transfer_out", "budget_check"}
        expected |= {f"day_{d}_plan" for d in range(1, 6)} | {f"weather_day_{d}" for d in range(1, 6)}
        assert ids == expected

    async def test_graph_edges_follow_section_5_3(self):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        edges = edge_map(result.graph_store)
        assert edges[("flight_outbound", "hotel_stay")] == "FLIGHT_BEFORE_CHECKIN"
        assert edges[("flight_outbound", "airport_transfer_in")] == "TRANSFER_AFTER_FLIGHT"
        assert edges[("airport_transfer_in", "day_1_plan")] == "TRANSFER_BEFORE_DAY_START"
        assert edges[("flight_return", "airport_transfer_out")] == "TRANSFER_BEFORE_DEPARTURE"
        assert edges[("day_5_plan", "airport_transfer_out")] == "DAY_ENDS_BEFORE_PICKUP"
        for d in range(1, 6):
            assert edges[("hotel_stay", f"day_{d}_plan")] == "STAY_COVERS_DAY"
            assert edges[(f"weather_day_{d}", f"day_{d}_plan")] == "WEATHER_SUITS_PLAN"
        cost_nodes = {"flight_outbound", "flight_return", "hotel_stay", "airport_transfer_in",
                      "airport_transfer_out"} | {f"day_{d}_plan" for d in range(1, 6)}
        budget_parents = {s for (s, d) in edges if d == "budget_check"}
        assert budget_parents == cost_nodes
        assert all(c == "BUDGET_ITEM_CURRENT" for (s, d), c in edges.items() if d == "budget_check")

    async def test_node_inputs_come_from_goal(self):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        store = result.graph_store
        out = store.get_node("flight_outbound").inputs
        assert (out["origin"], out["destination"], out["departure_date"]) == ("Mumbai", "Santorini", "2026-06-14")
        ret = store.get_node("flight_return").inputs
        assert (ret["origin"], ret["departure_date"]) == ("Santorini", "2026-06-19")
        assert store.get_node("day_3_plan").inputs["date"] == "2026-06-16"
        assert store.get_node("budget_check").inputs["budget_limit"] == 4000

    async def test_all_nodes_done_and_budget_sums_costs(self):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        store = result.graph_store
        assert all(n.status == NodeStatus.DONE for n in store.list_nodes())
        budget = store.get_node("budget_check").output
        costs = sum(n.output["cost"] for n in store.list_nodes() if "cost" in (n.output or {}))
        assert budget["total_cost"] == pytest.approx(costs)
        assert len(budget["line_items"]) == 10
        assert budget["within_budget"] is True

    async def test_runs_are_deterministic(self):
        a = await make_orchestrator(MockLLM()).plan_trip(santorini(), trip_id="same")
        b = await make_orchestrator(MockLLM()).plan_trip(santorini(), trip_id="same")
        outputs_a = {n.id: n.output for n in a.graph_store.list_nodes()}
        outputs_b = {n.id: n.output for n in b.graph_store.list_nodes()}
        assert outputs_a == outputs_b

    async def test_llm_may_choose_a_subset_of_tasks(self):
        goal = santorini()
        minimal = {"tasks": [
            {"task_id": "go", "task_type": "FLIGHT", "direction": "outbound"},
            {"task_id": "back", "task_type": "FLIGHT", "direction": "return"},
            {"task_id": "hotel", "task_type": "STAY"},
            {"task_id": "d2", "task_type": "DAY_PLAN", "day": 2, "theme": "Caldera hike"},
            {"task_id": "money", "task_type": "BUDGET"},
        ], "summary": "minimal"}
        result = await make_orchestrator(MockLLM([json.dumps(minimal)])).plan_trip(goal)
        assert result.success
        ids = {n.id for n in result.graph_store.list_nodes()}
        assert ids == {"flight_outbound", "flight_return", "hotel_stay", "day_2_plan", "budget_check"}
        assert result.graph_store.get_node("day_2_plan").output["theme"] == "Caldera hike"
        assert all(c.holds for c in result.contract_checks)

    async def test_one_night_trip(self):
        goal = santorini(return_date=date(2026, 6, 15))
        result = await make_orchestrator(MockLLM()).plan_trip(goal)
        assert result.success
        edges = edge_map(result.graph_store)
        assert ("day_1_plan", "airport_transfer_out") in edges
        assert all(c.holds for c in result.contract_checks), [c for c in result.contract_checks if not c.holds]


# ── Invalid LLM output → retry → fallback ─────────────────────────────────────

class TestRetryAndFallback:
    async def test_bad_json_then_valid(self):
        llm = MockLLM(["Sure! Here is your plan: {tasks: oops", template_json()])
        result = await make_orchestrator(llm).plan_trip(santorini())
        d = result.decomposition
        assert d.source == "llm" and d.attempts == 2 and len(d.errors) == 1
        assert result.success

    async def test_retry_message_contains_validation_error(self):
        llm = MockLLM(['{"tasks": [{"task_id": "x", "task_type": "HOTEL"}]}', template_json()])
        await make_orchestrator(llm).plan_trip(santorini())
        second_call = llm.received[1]
        assert second_call[-2]["role"] == "assistant"
        feedback = second_call[-1]["content"]
        assert "rejected by the validator" in feedback
        assert "task_type" in feedback

    async def test_rule_violation_is_reported(self):
        plan = json.loads(template_json())
        plan["tasks"] = [t for t in plan["tasks"] if t.get("direction") != "return"]
        llm = MockLLM([json.dumps(plan), template_json()])
        result = await make_orchestrator(llm).plan_trip(santorini())
        assert result.decomposition.attempts == 2
        assert "return FLIGHT" in result.decomposition.errors[0]
        assert "return FLIGHT" in llm.received[1][-1]["content"]

    async def test_day_out_of_range_rejected(self):
        plan = json.loads(template_json())
        plan["tasks"].append({"task_id": "day_9", "task_type": "DAY_PLAN", "day": 9})
        problems = validate_proposal(ProposedPlan.model_validate(plan), santorini())
        assert any("between 1 and 5" in p for p in problems)

    async def test_duplicate_roles_rejected(self):
        plan = json.loads(template_json())
        plan["tasks"].append({"task_id": "second_hotel", "task_type": "STAY"})
        problems = validate_proposal(ProposedPlan.model_validate(plan), santorini())
        assert any("exactly one STAY" in p for p in problems)

    async def test_markdown_fenced_reply_accepted(self):
        llm = MockLLM([f"```json\n{template_json()}\n```"])
        result = await make_orchestrator(llm).plan_trip(santorini())
        assert result.decomposition.source == "llm" and result.decomposition.attempts == 1

    async def test_always_invalid_falls_back_to_template(self):
        llm = MockLLM(handler=lambda messages: "not json at all")
        result = await make_orchestrator(llm, max_attempts=3).plan_trip(santorini())
        d = result.decomposition
        assert d.source == "fallback"
        assert d.attempts == 3 and d.llm_calls == 3 and len(d.errors) == 3
        assert result.success
        assert len(result.graph_store.list_nodes()) == 16

    async def test_llm_call_errors_fall_back(self):
        llm = MockLLM([LLMError("timeout"), LLMError("503"), LLMError("503")])
        result = await make_orchestrator(llm, max_attempts=3).plan_trip(santorini())
        assert result.decomposition.source == "fallback"
        assert all("LLM call failed" in e for e in result.decomposition.errors)
        assert result.success

    async def test_llm_edges_and_contracts_are_ignored(self):
        plan = json.loads(template_json())
        for task in plan["tasks"]:
            task["depends_on"] = ["budget_check"]                       # would create cycles
            task["contract"] = "__import__('os').system('echo pwned')"  # free-text contract
            task["agent"] = "evil_agent"
        result = await make_orchestrator(MockLLM([json.dumps(plan)])).plan_trip(santorini())
        assert result.decomposition.source == "llm"
        assert result.success
        store = result.graph_store
        assert {e.contract for e in store.list_edges()} <= KNOWN_CONTRACTS
        assert ("budget_check", "flight_outbound") not in edge_map(store)
        assert all(n.agent != "evil_agent" for n in store.list_nodes())


# ── Graph-contract validity ───────────────────────────────────────────────────

class TestContractValidity:
    async def test_every_edge_contract_holds_after_planning(self):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        assert len(result.contract_checks) == len(result.graph_store.list_edges()) == 27
        broken = [c for c in result.contract_checks if not c.holds]
        assert broken == []

    async def test_every_edge_uses_an_output_bound_template(self):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        assert all(e.contract in EDGE_BOUND_CONTRACTS for e in result.graph_store.list_edges())

    @pytest.mark.parametrize("goal_kwargs", [
        dict(travelers=4, currency="EUR", travel_style="budget", interests=["nature", "adventure"]),
        dict(destination="Tokyo", departure_city="London", travelers=2, currency="GBP",
             departure_date=date(2026, 3, 1), return_date=date(2026, 3, 8), interests=[]),
        dict(currency="INR", budget=300000, preferences={"pace": "relaxed"}),
    ])
    async def test_contracts_hold_across_goals(self, goal_kwargs):
        result = await make_orchestrator(MockLLM()).plan_trip(santorini(**goal_kwargs))
        assert result.success
        assert [c for c in result.contract_checks if not c.holds] == []

    async def test_delayed_flight_breaks_transfer_but_not_hotel(self):
        """Contracts are checked against outputs: a 5 h delay breaks F→T, not F→H."""
        result = await make_orchestrator(MockLLM()).plan_trip(santorini())
        store = result.graph_store
        flight = dict(store.get_node("flight_outbound").output)
        flight["arrival_time"] = "2026-06-14T19:00:00"
        store.update_node_status("flight_outbound", NodeStatus.DONE, output=flight, reason="test delay")
        checks = {(c.source_id, c.destination_id): c for c in check_graph_contracts(store)}
        assert not checks[("flight_outbound", "airport_transfer_in")].holds
        assert checks[("flight_outbound", "hotel_stay")].holds


class TestNewContractTemplates:
    def test_transfer_before_day_start(self):
        ok = evaluate_contract("TRANSFER_BEFORE_DAY_START",
                               {"transfer_dropoff_iso": "2026-06-14T15:15:00", "day_start_iso": "2026-06-14T18:00:00"})
        late = evaluate_contract("TRANSFER_BEFORE_DAY_START",
                                 {"transfer_dropoff_iso": "2026-06-14T20:15:00", "day_start_iso": "2026-06-14T18:00:00"})
        assert ok.holds and not late.holds

    def test_stay_covers_day(self):
        base = {"check_in": "2026-06-14", "check_out": "2026-06-19", "stay_hotel_id": "h1", "plan_hotel_id": "h1"}
        assert evaluate_contract("STAY_COVERS_DAY", base | {"day_date": "2026-06-18"}).holds
        assert not evaluate_contract("STAY_COVERS_DAY", base | {"day_date": "2026-06-19"}).holds
        assert not evaluate_contract("STAY_COVERS_DAY", base | {"day_date": "2026-06-15", "plan_hotel_id": "h2"}).holds

    def test_weather_suits_plan(self):
        assert evaluate_contract("WEATHER_SUITS_PLAN", {"condition": "sunny", "outdoor_activities": ["hike"]}).holds
        assert evaluate_contract("WEATHER_SUITS_PLAN", {"condition": "storm", "outdoor_activities": []}).holds
        assert not evaluate_contract("WEATHER_SUITS_PLAN", {"condition": "storm", "outdoor_activities": ["hike"]}).holds

    def test_transfer_before_departure(self):
        cfg = {"flight_departure_iso": "2026-06-19T16:00:00", "min_buffer_minutes": 120, "max_buffer_minutes": 240}
        assert evaluate_contract("TRANSFER_BEFORE_DEPARTURE", cfg | {"transfer_dropoff_iso": "2026-06-19T13:00:00"}).holds
        assert not evaluate_contract("TRANSFER_BEFORE_DEPARTURE", cfg | {"transfer_dropoff_iso": "2026-06-19T15:00:00"}).holds
        assert not evaluate_contract("TRANSFER_BEFORE_DEPARTURE", cfg | {"transfer_dropoff_iso": "2026-06-19T09:00:00"}).holds

    def test_day_ends_before_pickup(self):
        cfg = {"transfer_pickup_iso": "2026-06-19T12:15:00"}
        assert evaluate_contract("DAY_ENDS_BEFORE_PICKUP", cfg | {"day_end_iso": "2026-06-18T21:30:00"}).holds
        assert not evaluate_contract("DAY_ENDS_BEFORE_PICKUP", cfg | {"day_end_iso": "2026-06-19T11:45:00"}).holds

    def test_budget_item_current(self):
        assert check_edge("BUDGET_ITEM_CURRENT", {}, "f1", {"cost": 100.0}, {"line_items": {"f1": 100.0}}).holds
        assert not check_edge("BUDGET_ITEM_CURRENT", {}, "f1", {"cost": 120.0}, {"line_items": {"f1": 100.0}}).holds
        assert not check_edge("BUDGET_ITEM_CURRENT", {}, "f1", {"cost": 100.0}, {"line_items": {}}).holds

    def test_transfer_after_flight_max_gap(self):
        cfg = {"flight_arrival_iso": "2026-06-14T14:00:00", "min_gap_minutes": 30, "max_gap_minutes": 90}
        assert evaluate_contract("TRANSFER_AFTER_FLIGHT", cfg | {"transfer_pickup_iso": "2026-06-14T14:30:00"}).holds
        result = evaluate_contract("TRANSFER_AFTER_FLIGHT", cfg | {"transfer_pickup_iso": "2026-06-14T17:00:00"})
        assert not result.holds and "maximum" in result.explanation

    def test_bound_contract_without_output_does_not_hold(self):
        result = check_edge("FLIGHT_BEFORE_CHECKIN", {"buffer_minutes": 90}, "f", None, {"checkin_cutoff": "x"})
        assert not result.holds

    def test_mixed_naive_and_aware_times_do_not_crash(self):
        result = evaluate_contract("FLIGHT_BEFORE_CHECKIN",
                                   {"flight_arrival_iso": "2026-06-14T14:00:00Z",
                                    "checkin_cutoff_iso": "2026-06-14T23:59:00"})
        assert result.holds


# ── LLM client and memory ─────────────────────────────────────────────────────

class TestLLMClient:
    def test_mock_mode_uses_mock_llm(self):
        assert isinstance(get_llm_client(Settings(voyagesync_mode="mock")), MockLLM)

    def test_live_without_key_falls_back_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            client = get_llm_client(Settings(voyagesync_mode="live", voyagesync_llm_api_key=""))
        assert isinstance(client, MockLLM)
        assert "falling back" in caplog.text.lower() or "using mockllm" in caplog.text.lower()

    def test_live_with_key_uses_openai_compatible(self):
        client = get_llm_client(Settings(voyagesync_mode="live", voyagesync_llm_api_key="test-key",
                                         voyagesync_llm_model="m", voyagesync_llm_base_url="http://x/v1"))
        assert isinstance(client, OpenAICompatibleLLM) and client.model == "m"

    def test_extract_json_object(self):
        assert json.loads(extract_json_object('Here:\n```json\n{"a": 1}\n```')) == {"a": 1}
        assert json.loads(extract_json_object('prefix {"a": {"b": 2}} suffix')) == {"a": {"b": 2}}
        with pytest.raises(ValueError):
            extract_json_object("no json here")

    async def test_mock_llm_counts_usage(self):
        llm = MockLLM()
        await make_orchestrator(llm).plan_trip(santorini())
        assert llm.calls == 1 and llm.prompt_tokens > 0 and llm.completion_tokens > 0


class TestMemoryContext:
    def test_profile_interests_fill_empty_goal(self):
        goal = santorini(interests=[])
        merged = apply_profile_defaults(goal, {"preferences": {"interests": ["food"], "pace": "relaxed"}})
        assert merged.interests == ["food"]
        assert merged.preferences["pace"] == "relaxed"

    def test_goal_values_win_over_profile(self):
        goal = santorini(interests=["nature"], preferences={"pace": "packed"})
        merged = apply_profile_defaults(goal, {"preferences": {"interests": ["food"], "pace": "relaxed"}})
        assert merged.interests == ["nature"] and merged.preferences["pace"] == "packed"

    async def test_memory_is_in_the_prompt(self):
        llm = MockLLM()
        context = {"preferences": {"interests": ["food"]},
                   "past_trips": [{"destination": "Lisbon", "departure_date": "2025-05-01", "travel_style": "comfort"}],
                   "approval_history": {"approved": 3, "rejected": 1}}
        await make_orchestrator(llm).plan_trip(santorini(), context)
        prompt = llm.received[0][1]["content"]
        assert "Lisbon" in prompt and "3 approved, 1 rejected" in prompt and "Saved profile" in prompt
