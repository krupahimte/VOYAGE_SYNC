"""
Hybrid work, Phase 1 — candidate generation: every real agent can offer up to k
alternative outputs for the repair search without changing what ``run`` returns.

Checked on every node of the 10 benchmark trips, both as at planning time (no
frozen context) and as in a re-plan (previous output + frozen neighbours).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.scenario_set import load_trips  # noqa: E402

from app.agents.base import AgentResult, BaseAgent, distinct  # noqa: E402
from app.agents.itinerary import ARRIVAL_PICKUP_OFFSETS, DEPARTURE_BUFFERS, PENDING_DESCRIPTIONS  # noqa: E402
from app.agents.providers import build_providers  # noqa: E402
from app.agents.registry import build_agent_registry  # noqa: E402
from app.agents.validation import validate_output  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.models import NodeType, TaskNode  # noqa: E402
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.core.scheduler import Scheduler  # noqa: E402
from app.llm.client import MockLLM  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402

MOCK = Settings(voyagesync_mode="mock")
CHOICE_TYPES = (NodeType.FLIGHT, NodeType.STAY, NodeType.TRANSFER, NodeType.DAY_PLAN)
K = 4
_planned: dict[str, Any] = {}


async def planned(trip_id: str):
    """A trip planned with the real agents in mock mode (cached across tests)."""
    if trip_id not in _planned:
        providers = build_providers(MOCK, llm=MockLLM())
        registry = build_agent_registry(providers=providers)
        orch = Orchestrator(llm=providers.llm, registry=registry)
        result = await orch.plan_trip(TripGoal.model_validate(load_trips()[trip_id]["goal"]))
        assert result.success
        _planned[trip_id] = (result.graph_store, registry)
    return _planned[trip_id]


def contexts(store, nid: str) -> list[dict[str, Any]]:
    """No frozen context (planning) and the replanner's frozen context for this node."""
    neighbours = {e.source_id for e in store.list_edges() if e.destination_id == nid} | \
                 {e.destination_id for e in store.list_edges() if e.source_id == nid}
    replan = {"replan": True, "previous_output": store.get_node(nid).output,
              "frozen_neighbours": {m: store.get_node(m).output for m in sorted(neighbours)}}
    return [{}, replan]


async def same_as_run(agent: BaseAgent, node: TaskNode, ri: dict, ctx: dict, option: AgentResult) -> bool:
    expected = await agent.run(node, ri, ctx)
    output = option.output
    if node.type == NodeType.DAY_PLAN:
        output = await agent.complete_descriptions(ri, ctx, output)
    return expected.success and output == expected.output


def cases():
    return [(tid, t) for tid in load_trips() for t in CHOICE_TYPES]


class TestOptionZeroIsRun:
    @pytest.mark.parametrize("trip_id,node_type", cases())
    async def test_option_zero_reproduces_run(self, trip_id, node_type):
        store, registry = await planned(trip_id)
        for node in [n for n in store.list_nodes() if n.type == node_type]:
            ri = Scheduler._resolve_inputs(None, store, node.id)
            for ctx in contexts(store, node.id):
                options = await registry.get(node).candidates(node, ri, ctx, K)
                assert options and options[0].success, (node.id, options[0].error)
                assert await same_as_run(registry.get(node), node, ri, ctx, options[0]), node.id


class TestOptions:
    @pytest.mark.parametrize("trip_id", list(load_trips()))
    async def test_valid_distinct_bounded_and_deterministic(self, trip_id):
        store, registry = await planned(trip_id)
        for node in [n for n in store.list_nodes() if n.type in CHOICE_TYPES]:
            ri = Scheduler._resolve_inputs(None, store, node.id)
            for ctx in contexts(store, node.id):
                agent = registry.get(node)
                first = await agent.candidates(node, ri, ctx, K)
                assert 1 <= len(first) <= K
                assert all(o.success and not validate_output(node.type, o.output) for o in first)
                outputs = [o.output for o in first]
                assert all(a != b for i, a in enumerate(outputs) for b in outputs[i + 1:]), node.id
                assert [o.output for o in await agent.candidates(node, ri, ctx, K)] == outputs

    async def test_more_than_one_option_where_there_is_a_choice(self):
        store, registry = await planned("santorini_solo_luxury")
        for nid in ("flight_outbound", "hotel_stay", "airport_transfer_in", "airport_transfer_out", "day_2_plan"):
            node = store.get_node(nid)
            options = await registry.get(node).candidates(node, Scheduler._resolve_inputs(None, store, nid), {}, K)
            assert len(options) >= 2, nid

    async def test_k_one_is_run_only(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("flight_outbound")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        options = await registry.get(node).candidates(node, ri, {}, 1)
        assert len(options) == 1 and await same_as_run(registry.get(node), node, ri, {}, options[0])

    async def test_agents_without_a_choice_return_run(self):
        store, registry = await planned("santorini_solo_luxury")
        for nid in ("weather_day_1", "budget_check"):
            node = store.get_node(nid)
            ri = Scheduler._resolve_inputs(None, store, nid)
            options = await registry.get(node).candidates(node, ri, {}, K)
            assert [o.output for o in options] == [(await registry.get(node).run(node, ri, {})).output]


class TestPerAgent:
    async def test_flight_options_honour_exclusions(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("flight_outbound")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        cancelled = store.get_node(node.id).output["offer_id"]
        options = await registry.get(node).candidates(node, ri | {"exclude_offer_ids": [cancelled]}, {}, K)
        assert options and all(o.output["offer_id"] != cancelled for o in options)

    async def test_hotel_options_honour_exclusions_and_cost_one_quote_each(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("hotel_stay")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        overbooked = store.get_node(node.id).output["hotel_id"]
        usage = registry.providers.usage
        before = usage.snapshot()
        options = await registry.get(node).candidates(node, ri | {"exclude_hotel_ids": [overbooked]}, {}, 3)
        quotes = usage.snapshot().get("mock_booking", 0) - before.get("mock_booking", 0)
        assert options and all(o.output["hotel_id"] != overbooked for o in options)
        assert quotes == len(options)

    async def test_transfer_timings(self):
        store, registry = await planned("santorini_solo_luxury")
        for nid, timings, key in (("airport_transfer_in", (30,) + ARRIVAL_PICKUP_OFFSETS, "pickup_offset_minutes"),
                                  ("airport_transfer_out", (180,) + DEPARTURE_BUFFERS, "airport_buffer_minutes")):
            node = store.get_node(nid)
            ri = Scheduler._resolve_inputs(None, store, nid)
            options = await registry.get(node).candidates(node, ri, {}, len(timings))
            agent = registry.get(node)
            expected = [(await agent.run(node, ri | {key: t}, {})).output for t in timings]
            assert [o.output for o in options] == expected

    async def test_transfer_options_share_one_route_lookup(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("airport_transfer_in")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        usage = registry.providers.usage
        agent = registry.get(node)
        before = usage.snapshot()
        await agent.run(node, ri, {})
        one = usage.total - sum(before.values())
        before = usage.snapshot()
        await agent.candidates(node, ri, {}, K)
        assert usage.total - sum(before.values()) == one

    async def test_day_plan_options_make_no_llm_call_and_keep_kept_activities(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("day_2_plan")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        ctx = contexts(store, node.id)[1]
        llm = registry.providers.llm
        calls = llm.calls
        options = await registry.get(node).candidates(node, ri, ctx, K)
        assert llm.calls == calls
        previous = {a["name"] for a in store.get_node(node.id).output["activities"]}
        for o in options:
            assert PENDING_DESCRIPTIONS in o.output and o.output["descriptions_source"] == "pending"
        # With frozen context every still-feasible activity is kept, so no variant can differ.
        assert len(options) == 1 and {a["name"] for a in options[0].output["activities"]} == previous

    async def test_day_plan_variants_differ_when_activities_are_refilled(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("day_2_plan")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        options = await registry.get(node).candidates(node, ri, {}, K)
        assert len(options) >= 2
        assert len({tuple(a["name"] for a in o.output["activities"]) for o in options}) == len(options)

    async def test_complete_descriptions_is_idempotent(self):
        store, registry = await planned("santorini_solo_luxury")
        node = store.get_node("day_3_plan")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        agent = registry.get(node)
        done = await agent.complete_descriptions(ri, {}, (await agent.candidates(node, ri, {}, K))[1].output)
        assert PENDING_DESCRIPTIONS not in done and done["descriptions_source"] != "pending"
        assert await agent.complete_descriptions(ri, {}, done) == done


class TestDistinct:
    def test_keeps_order_drops_duplicates_and_failures(self):
        a, b = AgentResult.ok({"x": 1}), AgentResult.ok({"x": 2})
        bad = AgentResult.fail("no")
        assert distinct([bad, a, AgentResult.ok({"x": 1}), b], 3) == [a, b]
        assert distinct([a, b], 1) == [a]

    def test_all_failed_returns_the_first_failure(self):
        bad = AgentResult.fail("no")
        assert distinct([bad, AgentResult.fail("also no")], 3) == [bad]
