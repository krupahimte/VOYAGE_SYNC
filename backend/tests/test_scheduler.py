"""
Phase 2 tests: Dependency-aware scheduler and agent interfaces.

Coverage:
  - Linear chain executes in dependency order.
  - Independent tasks execute concurrently.
  - Parent outputs are correctly resolved and passed to children.
  - Status transitions: PENDING → READY → RUNNING → DONE.
  - Outputs are persisted in the GraphStore after execution.
  - Agent failures trigger retry up to the retry limit.
  - Non-retryable failures are not retried.
  - Descendants of failed nodes are skipped (not executed).
  - Already-DONE nodes are not re-executed.
  - Concurrency limit is respected.
  - Isolated nodes (no edges) execute successfully.
  - Empty graph returns an empty result without error.
  - AgentRegistry lookup: by name and by type, KeyError on miss.
  - Phase 0 / Phase 1 tests are unaffected (imports only).

Isolation: every test builds an in-memory GraphStore from scratch.
No SQLite, no HTTP, no timing-based assertions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agents.base import AgentRegistry, AgentResult, BaseAgent
from app.agents.mocks import (
    FailingAgent,
    MockBudgetAgent,
    MockDayPlanAgent,
    MockFlightAgent,
    MockStayAgent,
    MockTransferAgent,
    MockWeatherAgent,
    SlowAgent,
    build_default_registry,
)
from app.core.graph import GraphStore
from app.core.models import Edge, NodeStatus, NodeType, RiskTier, TaskNode
from app.core.scheduler import Scheduler, SchedulerResult


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def make_node(
    node_id: str,
    node_type: NodeType = NodeType.FLIGHT,
    agent: str = "mock_flight",
    inputs: dict | None = None,
) -> TaskNode:
    return TaskNode(id=node_id, type=node_type, agent=agent, inputs=inputs or {})


def make_edge(src: str, dst: str) -> Edge:
    return Edge(
        source_id=src,
        destination_id=dst,
        contract="FLIGHT_BEFORE_CHECKIN",
        description=f"{src} → {dst}",
    )


def registry_with(*pairs: tuple[str, BaseAgent]) -> AgentRegistry:
    """Build a registry keyed by agent-name string."""
    reg = AgentRegistry()
    for name, agent in pairs:
        reg.register_by_name(name, agent)
    return reg


def success_agent() -> BaseAgent:
    """A trivially succeeding agent."""
    class _Ok(BaseAgent):
        async def run(self, node, resolved_inputs, frozen_context):
            return AgentResult.ok({"node_id": node.id, "done": True})
    return _Ok()


def output_agent(output: dict[str, Any]) -> BaseAgent:
    """An agent that always returns *output*."""
    class _Out(BaseAgent):
        async def run(self, node, resolved_inputs, frozen_context):
            return AgentResult.ok(output)
    return _Out()


def capturing_agent() -> tuple[BaseAgent, list[dict]]:
    """Returns an agent that records every resolved_inputs it receives."""
    calls: list[dict] = []

    class _Capture(BaseAgent):
        async def run(self, node, resolved_inputs, frozen_context):
            calls.append(dict(resolved_inputs))
            return AgentResult.ok({"captured": True})
    return _Capture(), calls


# ── AgentResult tests ─────────────────────────────────────────────────────────

class TestAgentResult:
    def test_ok_factory(self):
        r = AgentResult.ok({"x": 1})
        assert r.success is True
        assert r.output == {"x": 1}
        assert r.error == ""

    def test_fail_factory_retryable(self):
        r = AgentResult.fail("boom", retryable=True)
        assert r.success is False
        assert r.retryable is True
        assert "boom" in r.error

    def test_fail_factory_not_retryable(self):
        r = AgentResult.fail("perm", retryable=False)
        assert r.retryable is False


# ── AgentRegistry tests ───────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_lookup_by_type(self):
        reg = AgentRegistry()
        agent = MockFlightAgent()
        reg.register_by_type(NodeType.FLIGHT, agent)
        node = make_node("n1", NodeType.FLIGHT, "some_agent")
        assert reg.get(node) is agent

    def test_lookup_by_name_takes_priority(self):
        reg = AgentRegistry()
        type_agent = MockFlightAgent()
        name_agent = MockStayAgent()
        reg.register_by_type(NodeType.FLIGHT, type_agent)
        reg.register_by_name("my_agent", name_agent)
        node = make_node("n1", NodeType.FLIGHT, "my_agent")
        assert reg.get(node) is name_agent

    def test_lookup_missing_raises_key_error(self):
        reg = AgentRegistry()
        node = make_node("n1", NodeType.FLIGHT, "nonexistent")
        with pytest.raises(KeyError, match="nonexistent"):
            reg.get(node)

    def test_contains_by_type(self):
        reg = AgentRegistry()
        reg.register_by_type(NodeType.WEATHER, MockWeatherAgent())
        node = make_node("n1", NodeType.WEATHER, "anything")
        assert node in reg

    def test_not_contains_for_unregistered(self):
        reg = AgentRegistry()
        node = make_node("n1", NodeType.FLIGHT, "ghost")
        assert node not in reg

    def test_default_registry_has_all_types(self):
        reg = build_default_registry()
        for nt in NodeType:
            node = make_node("x", nt, "dummy")
            assert node in reg


# ── Mock agent output shape tests ─────────────────────────────────────────────

class TestMockAgents:
    @pytest.mark.asyncio
    async def test_flight_agent_returns_valid_output(self):
        agent = MockFlightAgent()
        node = make_node("n1", NodeType.FLIGHT, "mock_flight")
        result = await agent.run(node, {}, {})
        assert result.success
        assert "flight_number" in result.output
        assert "departure_time" in result.output
        assert "arrival_time" in result.output

    @pytest.mark.asyncio
    async def test_stay_agent_returns_valid_output(self):
        agent = MockStayAgent()
        node = make_node("n1", NodeType.STAY, "mock_stay")
        result = await agent.run(node, {"city": "Paris"}, {})
        assert result.success
        assert result.output["city"] == "Paris"
        assert "check_in" in result.output

    @pytest.mark.asyncio
    async def test_transfer_uses_parent_arrival_time(self):
        agent = MockTransferAgent()
        node = make_node("n1", NodeType.TRANSFER, "mock_transfer")
        result = await agent.run(node, {"arrival_time": "2025-06-01T22:00:00Z"}, {})
        assert result.success
        assert result.output["pickup_time"] == "2025-06-01T22:00:00Z"

    @pytest.mark.asyncio
    async def test_weather_agent(self):
        agent = MockWeatherAgent()
        node = make_node("n1", NodeType.WEATHER, "mock_weather")
        result = await agent.run(node, {"date": "2025-07-04", "city": "Rome"}, {})
        assert result.success
        assert result.output["city"] == "Rome"

    @pytest.mark.asyncio
    async def test_budget_agent(self):
        agent = MockBudgetAgent()
        node = make_node("n1", NodeType.BUDGET, "mock_budget")
        result = await agent.run(node, {"total_budget_gbp": 3000, "spent_gbp": 1500}, {})
        assert result.success
        assert result.output["remaining_gbp"] == 1500.0
        assert result.output["within_budget"] is True

    @pytest.mark.asyncio
    async def test_day_plan_agent(self):
        agent = MockDayPlanAgent()
        node = make_node("n1", NodeType.DAY_PLAN, "mock_day_plan")
        result = await agent.run(node, {}, {})
        assert result.success
        assert "activities" in result.output
        assert len(result.output["activities"]) > 0

    @pytest.mark.asyncio
    async def test_failing_agent_always_fails(self):
        agent = FailingAgent(retryable=False)
        node = make_node("n1", NodeType.FLIGHT, "fail")
        result = await agent.run(node, {}, {})
        assert result.success is False
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_failing_agent_recovers_after_n_failures(self):
        agent = FailingAgent(retryable=True, fail_count=2)
        node = make_node("n1", NodeType.FLIGHT, "fail")
        # First two calls fail
        r1 = await agent.run(node, {}, {})
        r2 = await agent.run(node, {}, {})
        assert r1.success is False
        assert r2.success is False
        # Third call succeeds
        r3 = await agent.run(node, {}, {})
        assert r3.success is True


# ── Scheduler: basic execution ────────────────────────────────────────────────

class TestSchedulerBasic:
    @pytest.mark.asyncio
    async def test_empty_graph_returns_empty_result(self):
        store = GraphStore("g_empty")
        scheduler = Scheduler(build_default_registry())
        result = await scheduler.run(store)
        assert result.success
        assert result.done == []
        assert result.failed == []

    @pytest.mark.asyncio
    async def test_single_isolated_node_executes(self):
        store = GraphStore("g_single")
        store.add_node(make_node("n1", NodeType.FLIGHT, "mock"))
        reg = registry_with(("mock", success_agent()))
        result = await Scheduler(reg).run(store)
        assert "n1" in result.done
        assert store.get_node("n1").status == NodeStatus.DONE

    @pytest.mark.asyncio
    async def test_linear_chain_executes_in_order(self):
        """A → B → C: A must finish before B starts, B before C."""
        store = GraphStore("g_chain")
        exec_order: list[str] = []

        class OrderAgent(BaseAgent):
            def __init__(self, nid: str):
                self.nid = nid
            async def run(self, node, resolved_inputs, frozen_context):
                exec_order.append(self.nid)
                return AgentResult.ok({"order_token": self.nid})

        store.add_node(make_node("A", NodeType.FLIGHT, "a"))
        store.add_node(make_node("B", NodeType.STAY, "b"))
        store.add_node(make_node("C", NodeType.TRANSFER, "c"))
        store.add_edge(make_edge("A", "B"))
        store.add_edge(make_edge("B", "C"))

        reg = registry_with(("a", OrderAgent("A")), ("b", OrderAgent("B")), ("c", OrderAgent("C")))
        result = await Scheduler(reg).run(store)

        assert result.done == ["A", "B", "C"]
        assert exec_order == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_already_done_node_is_not_rerun(self):
        store = GraphStore("g_no_rerun")
        run_count = 0

        class CountAgent(BaseAgent):
            async def run(self, node, resolved_inputs, frozen_context):
                nonlocal run_count
                run_count += 1
                return AgentResult.ok({})

        store.add_node(make_node("n1", NodeType.FLIGHT, "count"))
        agent = CountAgent()
        reg = registry_with(("count", agent))
        scheduler = Scheduler(reg)

        # First run — executes n1
        await scheduler.run(store)
        assert run_count == 1
        assert store.get_node("n1").status == NodeStatus.DONE

        # Second run — n1 is already DONE, should not run again
        result2 = await scheduler.run(store)
        assert run_count == 1
        assert result2.done == []


# ── Scheduler: dependency order ───────────────────────────────────────────────

class TestSchedulerDependencyOrder:
    @pytest.mark.asyncio
    async def test_child_not_run_until_parent_done(self):
        """Child must stay unexecuted until parent finishes."""
        store = GraphStore("g_dep_order")
        parent_done_before_child = False

        class ParentAgent(BaseAgent):
            async def run(self, node, resolved_inputs, frozen_context):
                return AgentResult.ok({"token": "parent_result"})

        class ChildAgent(BaseAgent):
            async def run(self, node, resolved_inputs, frozen_context):
                nonlocal parent_done_before_child
                parent = store.get_node("parent")
                parent_done_before_child = parent.status == NodeStatus.DONE
                return AgentResult.ok({})

        store.add_node(make_node("parent", NodeType.FLIGHT, "p"))
        store.add_node(make_node("child", NodeType.STAY, "c"))
        store.add_edge(make_edge("parent", "child"))

        reg = registry_with(("p", ParentAgent()), ("c", ChildAgent()))
        await Scheduler(reg).run(store)
        assert parent_done_before_child is True

    @pytest.mark.asyncio
    async def test_parent_output_resolved_into_child_inputs(self):
        """Child receives parent's output merged into its resolved_inputs."""
        store = GraphStore("g_input_resolution")
        captured: list[dict] = []

        store.add_node(make_node("parent", NodeType.FLIGHT, "src",
                                 inputs={"origin": "LHR"}))
        store.add_node(make_node("child", NodeType.TRANSFER, "cap",
                                 inputs={"child_own": "yes"}))
        store.add_edge(make_edge("parent", "child"))

        parent_output = {"arrival_time": "2025-06-01T21:00:00Z", "flight_number": "VS901"}

        cap_agent, calls = capturing_agent()
        reg = registry_with(
            ("src", output_agent(parent_output)),
            ("cap", cap_agent),
        )
        await Scheduler(reg).run(store)

        assert len(calls) == 1
        child_inputs = calls[0]
        # Parent output keys present
        assert child_inputs["arrival_time"] == "2025-06-01T21:00:00Z"
        assert child_inputs["flight_number"] == "VS901"
        # Child's own input takes precedence
        assert child_inputs["child_own"] == "yes"

    @pytest.mark.asyncio
    async def test_multiple_parents_all_outputs_resolved(self):
        """Child with two parents receives both parents' outputs merged."""
        store = GraphStore("g_multi_parent")
        store.add_node(make_node("p1", NodeType.FLIGHT, "src1"))
        store.add_node(make_node("p2", NodeType.WEATHER, "src2"))
        store.add_node(make_node("child", NodeType.DAY_PLAN, "cap"))
        store.add_edge(make_edge("p1", "child"))
        store.add_edge(make_edge("p2", "child"))

        cap_agent, calls = capturing_agent()
        reg = registry_with(
            ("src1", output_agent({"flight_number": "VS901"})),
            ("src2", output_agent({"weather_condition": "sunny"})),
            ("cap", cap_agent),
        )
        await Scheduler(reg).run(store)

        assert len(calls) == 1
        assert calls[0]["flight_number"] == "VS901"
        assert calls[0]["weather_condition"] == "sunny"

    @pytest.mark.asyncio
    async def test_node_inputs_override_parent_output_on_conflict(self):
        """Node's own inputs win over conflicting parent output keys."""
        store = GraphStore("g_override")
        store.add_node(make_node("parent", NodeType.FLIGHT, "src"))
        store.add_node(make_node("child", NodeType.TRANSFER, "cap",
                                 inputs={"city": "Tokyo"}))
        store.add_edge(make_edge("parent", "child"))

        cap_agent, calls = capturing_agent()
        reg = registry_with(
            ("src", output_agent({"city": "PARENT_CITY"})),
            ("cap", cap_agent),
        )
        await Scheduler(reg).run(store)

        assert calls[0]["city"] == "Tokyo"


# ── Scheduler: concurrency ────────────────────────────────────────────────────

class TestSchedulerConcurrency:
    @pytest.mark.asyncio
    async def test_independent_tasks_run_concurrently(self):
        """Three independent nodes with a slow agent should all run in parallel."""
        store = GraphStore("g_concurrent")
        slow = SlowAgent(delay_seconds=0.05)

        for nid in ("a", "b", "c"):
            store.add_node(make_node(nid, NodeType.FLIGHT, "slow"))

        reg = registry_with(("slow", slow))
        result = await Scheduler(reg, max_concurrency=3).run(store)

        assert len(result.done) == 3
        # All three ran concurrently → peak concurrency == 3
        assert slow.concurrent_peak == 3

    @pytest.mark.asyncio
    async def test_concurrency_limit_is_respected(self):
        """With max_concurrency=1, even independent tasks run sequentially."""
        store = GraphStore("g_serial")
        slow = SlowAgent(delay_seconds=0.02)

        for nid in ("x", "y", "z"):
            store.add_node(make_node(nid, NodeType.FLIGHT, "slow"))

        reg = registry_with(("slow", slow))
        result = await Scheduler(reg, max_concurrency=1).run(store)

        assert len(result.done) == 3
        assert slow.concurrent_peak == 1

    @pytest.mark.asyncio
    async def test_concurrency_limit_of_two(self):
        """max_concurrency=2 allows at most 2 parallel runs."""
        store = GraphStore("g_par2")
        slow = SlowAgent(delay_seconds=0.03)

        for nid in ("p", "q", "r", "s"):
            store.add_node(make_node(nid, NodeType.FLIGHT, "slow"))

        reg = registry_with(("slow", slow))
        await Scheduler(reg, max_concurrency=2).run(store)

        assert slow.concurrent_peak <= 2


# ── Scheduler: status transitions ─────────────────────────────────────────────

class TestSchedulerStatusTransitions:
    @pytest.mark.asyncio
    async def test_done_node_has_correct_final_status(self):
        store = GraphStore("g_status")
        store.add_node(make_node("n1", NodeType.FLIGHT, "ok"))
        reg = registry_with(("ok", success_agent()))
        await Scheduler(reg).run(store)
        assert store.get_node("n1").status == NodeStatus.DONE

    @pytest.mark.asyncio
    async def test_done_node_output_persisted(self):
        store = GraphStore("g_output")
        store.add_node(make_node("n1", NodeType.BUDGET, "budget"))
        reg = registry_with(("budget", output_agent({"result": 42})))
        await Scheduler(reg).run(store)
        assert store.get_node("n1").output == {"result": 42}

    @pytest.mark.asyncio
    async def test_history_records_transitions(self):
        """Every status change must be recorded in node history."""
        store = GraphStore("g_history")
        store.add_node(make_node("n1", NodeType.FLIGHT, "ok"))
        reg = registry_with(("ok", success_agent()))
        await Scheduler(reg).run(store)

        history = store.get_history("n1")
        statuses = [h.status for h in history]
        # Must contain: PENDING (creation), READY, RUNNING, DONE
        assert NodeStatus.PENDING in statuses
        assert NodeStatus.READY in statuses
        assert NodeStatus.RUNNING in statuses
        assert NodeStatus.DONE in statuses


# ── Scheduler: failure handling ───────────────────────────────────────────────

class TestSchedulerFailureHandling:
    @pytest.mark.asyncio
    async def test_non_retryable_failure_marks_node_failed(self):
        store = GraphStore("g_fail_perm")
        store.add_node(make_node("n1", NodeType.FLIGHT, "fail"))
        reg = registry_with(("fail", FailingAgent(retryable=False)))
        result = await Scheduler(reg, max_retries=3).run(store)

        assert "n1" in result.failed
        assert store.get_node("n1").status == NodeStatus.FAILED

    @pytest.mark.asyncio
    async def test_retryable_failure_retries_up_to_limit(self):
        """Agent fails first 2 times then succeeds. With max_retries=2 it recovers."""
        store = GraphStore("g_retry_ok")
        store.add_node(make_node("n1", NodeType.FLIGHT, "fail2"))
        agent = FailingAgent(retryable=True, fail_count=2)
        reg = registry_with(("fail2", agent))
        result = await Scheduler(reg, max_retries=3).run(store)

        assert "n1" in result.done
        assert store.get_node("n1").status == NodeStatus.DONE
        assert agent._attempts == 3  # 2 failures + 1 success

    @pytest.mark.asyncio
    async def test_retryable_failure_exceeds_limit_marks_failed(self):
        """Agent always fails. After max_retries exhausted → FAILED."""
        store = GraphStore("g_retry_exhaust")
        store.add_node(make_node("n1", NodeType.FLIGHT, "always_fail"))
        agent = FailingAgent(retryable=True, fail_count=-1)
        reg = registry_with(("always_fail", agent))
        result = await Scheduler(reg, max_retries=2).run(store)

        assert "n1" in result.failed
        assert store.get_node("n1").status == NodeStatus.FAILED
        # Should have tried: 1 initial + 2 retries = 3 attempts total
        assert agent._attempts == 3

    @pytest.mark.asyncio
    async def test_descendants_skipped_when_parent_fails(self):
        """If parent fails, child and grandchild must not execute."""
        store = GraphStore("g_cascade")
        store.add_node(make_node("parent", NodeType.FLIGHT, "fail"))
        store.add_node(make_node("child", NodeType.TRANSFER, "ok"))
        store.add_node(make_node("grandchild", NodeType.DAY_PLAN, "ok"))
        store.add_edge(make_edge("parent", "child"))
        store.add_edge(make_edge("child", "grandchild"))

        reg = registry_with(
            ("fail", FailingAgent(retryable=False)),
            ("ok", success_agent()),
        )
        result = await Scheduler(reg, max_retries=0).run(store)

        assert "parent" in result.failed
        assert "child" in result.skipped
        assert "grandchild" in result.skipped
        assert store.get_node("child").status == NodeStatus.FAILED
        assert store.get_node("grandchild").status == NodeStatus.FAILED

    @pytest.mark.asyncio
    async def test_sibling_runs_even_when_other_sibling_fails(self):
        """
        Two independent children of the same parent.
        If parent succeeds, both children run regardless of each other.
        """
        store = GraphStore("g_siblings")
        store.add_node(make_node("parent", NodeType.FLIGHT, "ok"))
        store.add_node(make_node("child_ok", NodeType.STAY, "ok"))
        store.add_node(make_node("child_fail", NodeType.TRANSFER, "fail"))
        store.add_edge(make_edge("parent", "child_ok"))
        store.add_edge(make_edge("parent", "child_fail"))

        reg = registry_with(
            ("ok", success_agent()),
            ("fail", FailingAgent(retryable=False)),
        )
        result = await Scheduler(reg, max_retries=0).run(store)

        assert "parent" in result.done
        assert "child_ok" in result.done
        assert "child_fail" in result.failed

    @pytest.mark.asyncio
    async def test_unregistered_agent_marks_node_failed(self):
        store = GraphStore("g_no_agent")
        store.add_node(make_node("n1", NodeType.FLIGHT, "ghost_agent"))
        result = await Scheduler(AgentRegistry()).run(store)
        assert "n1" in result.failed
        assert store.get_node("n1").status == NodeStatus.FAILED


# ── Scheduler: execution log ──────────────────────────────────────────────────

class TestSchedulerLog:
    @pytest.mark.asyncio
    async def test_log_contains_done_event(self):
        store = GraphStore("g_log")
        store.add_node(make_node("n1", NodeType.FLIGHT, "ok"))
        reg = registry_with(("ok", success_agent()))
        result = await Scheduler(reg).run(store)

        events = [e.event for e in result.log if e.node_id == "n1"]
        assert "READY" in events
        assert "RUNNING" in events
        assert "DONE" in events

    @pytest.mark.asyncio
    async def test_log_contains_failed_event(self):
        store = GraphStore("g_log_fail")
        store.add_node(make_node("n1", NodeType.FLIGHT, "fail"))
        reg = registry_with(("fail", FailingAgent(retryable=False)))
        result = await Scheduler(reg, max_retries=0).run(store)

        events = [e.event for e in result.log if e.node_id == "n1"]
        assert "FAILED" in events

    @pytest.mark.asyncio
    async def test_log_records_retry_events(self):
        store = GraphStore("g_log_retry")
        store.add_node(make_node("n1", NodeType.FLIGHT, "fail1"))
        agent = FailingAgent(retryable=True, fail_count=1)
        reg = registry_with(("fail1", agent))
        result = await Scheduler(reg, max_retries=2).run(store)

        events = [e.event for e in result.log if e.node_id == "n1"]
        assert "RETRY" in events
        assert "DONE" in events


# ── Scheduler: full trip graph integration ────────────────────────────────────

class TestSchedulerIntegration:
    @pytest.mark.asyncio
    async def test_full_trip_graph_executes_successfully(self):
        """
        Example trip graph:
            FLIGHT → TRANSFER → DAY_PLAN
                  ↘ STAY

        All tasks complete; outputs flow down the chain.
        """
        store = GraphStore("g_trip")

        flight = TaskNode(id="flight", type=NodeType.FLIGHT, agent="flight",
                          inputs={"origin": "LHR", "destination": "NRT", "departure_date": "2025-06-01"})
        transfer = TaskNode(id="transfer", type=NodeType.TRANSFER, agent="transfer")
        stay = TaskNode(id="stay", type=NodeType.STAY, agent="stay",
                        inputs={"city": "Tokyo", "check_in": "2025-06-01", "check_out": "2025-06-08"})
        day_plan = TaskNode(id="day1", type=NodeType.DAY_PLAN, agent="day_plan",
                            inputs={"date": "2025-06-02", "city": "Tokyo"})

        store.add_node(flight)
        store.add_node(transfer)
        store.add_node(stay)
        store.add_node(day_plan)

        store.add_edge(make_edge("flight", "transfer"))
        store.add_edge(make_edge("flight", "stay"))
        store.add_edge(make_edge("stay", "day1"))

        reg = registry_with(
            ("flight", MockFlightAgent()),
            ("transfer", MockTransferAgent()),
            ("stay", MockStayAgent()),
            ("day_plan", MockDayPlanAgent()),
        )
        result = await Scheduler(reg, max_concurrency=4).run(store)

        assert result.success
        assert set(result.done) == {"flight", "transfer", "stay", "day1"}

        # Transfer received flight's arrival_time
        transfer_node = store.get_node("transfer")
        assert transfer_node.output is not None
        assert "pickup_time" in transfer_node.output

        # Day plan output exists
        day_node = store.get_node("day1")
        assert day_node.output is not None
        assert "activities" in day_node.output

    @pytest.mark.asyncio
    async def test_diamond_graph_executes_correctly(self):
        """
        Diamond: A → B, A → C, B → D, C → D.
        D must wait for both B and C.
        """
        store = GraphStore("g_diamond")
        exec_order: list[str] = []

        class TrackAgent(BaseAgent):
            def __init__(self, nid: str):
                self.nid = nid
            async def run(self, node, resolved_inputs, frozen_context):
                exec_order.append(self.nid)
                return AgentResult.ok({"from": self.nid})

        for nid in ("A", "B", "C", "D"):
            store.add_node(make_node(nid, NodeType.FLIGHT, nid.lower()))

        store.add_edge(make_edge("A", "B"))
        store.add_edge(make_edge("A", "C"))
        store.add_edge(make_edge("B", "D"))
        store.add_edge(make_edge("C", "D"))

        reg = registry_with(
            ("a", TrackAgent("A")),
            ("b", TrackAgent("B")),
            ("c", TrackAgent("C")),
            ("d", TrackAgent("D")),
        )
        result = await Scheduler(reg, max_concurrency=4).run(store)

        assert result.success
        assert set(result.done) == {"A", "B", "C", "D"}
        # A must come first, D must come last
        assert exec_order.index("A") < exec_order.index("D")
        assert exec_order.index("B") < exec_order.index("D")
        assert exec_order.index("C") < exec_order.index("D")
