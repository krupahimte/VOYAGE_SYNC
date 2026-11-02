"""
Hybrid work, Phase 3 — the repair search: per-branch contract-pruned BFS, the
option cache, the rank / greedy (E1g) / exhaustive (EXH) policies, the evaluation
budget and the atomic commit, plus the Replanner wiring.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.run_benchmark import DRIFT  # noqa: E402
from eval.scenario_set import load_scenarios, load_trips  # noqa: E402

from app.core.monitor.apply import apply_event  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.orchestrator import Orchestrator, check_graph_contracts  # noqa: E402
from app.core.repair import commit as commit_module  # noqa: E402
from app.core.repair.commit import repair  # noqa: E402
from app.core.repair.constraints import ValidationReport  # noqa: E402
from app.core.repair.search import EvalBudget, OptionGenerator, RepairSearch, SearchParams  # noqa: E402
from app.core.scheduler import Scheduler  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402

TRIP = "santorini_solo_luxury"
_planned: dict[str, tuple] = {}


async def planned(trip_id: str = TRIP):
    if trip_id not in _planned:
        orch = Orchestrator()
        result = await orch.plan_trip(TripGoal.model_validate(load_trips()[trip_id]["goal"]))
        assert result.success
        _planned[trip_id] = (orch, result.graph_store)
    return _planned[trip_id]


def scenario(sid: str) -> dict:
    return next(s for s in load_scenarios() if s["id"] == sid)


def event_of(s: dict):
    return DisruptionSimulator(seed=0).make(s["type"], s["target"], **s["facts"])


async def disrupted(s: dict):
    """(orchestrator, pristine planned store, store with the event applied, ApplyOutcome)."""
    orch, store = await planned(s["trip"])
    work = copy.deepcopy(store)
    return orch, store, work, apply_event(work, event_of(s))


class TestParityWithE1:
    @pytest.mark.parametrize("drift", [False, True])
    async def test_rank_policy_reproduces_e1(self, drift):
        """First surviving option = E1, node for node; E1's unrepairable plans become 'no feasible plan'."""
        infeasible = set()
        for s in load_scenarios():
            orch, store = await planned(s["trip"])
            drift_ctl = orch.registry.providers.drift
            if drift:
                drift_ctl.enable(s["id"], **DRIFT)
            try:
                e1 = copy.deepcopy(store)
                e1_outcome = await orch.handle_disruption(e1, event_of(s))
                mine = copy.deepcopy(store)
                applied = apply_event(mine, event_of(s))
                if not applied.has_effect:
                    assert e1_outcome.status == "no_effect"
                    continue
                outcome, _, err = await repair(orch.registry, mine, applied.target, applied.kind, policy="rank")
            finally:
                drift_ctl.disable()
            assert not err
            if not outcome.feasible:
                infeasible.add(s["id"])
                continue
            assert set(outcome.summary()["region"]) == set(e1_outcome.replan.stale), s["id"]
            assert [n.output for n in e1.list_nodes()] == [mine.get_node(n.id).output for n in e1.list_nodes()], \
                s["id"]
        assert infeasible == {s["id"] for s in load_scenarios() if s["id"].endswith("outbound_delay_600")}


class TestPolicies:
    async def test_exhaustive_is_never_beaten_and_agrees_on_feasibility(self):
        params = SearchParams(timeout_s=60)
        for s in load_scenarios():
            orch, store, _, applied = await disrupted(s)
            if not applied.has_effect:
                continue
            results = {}
            for policy in ("rank", "greedy", "exhaustive"):
                work = copy.deepcopy(store)
                apply_event(work, event_of(s))
                outcome, _, err = await repair(orch.registry, work, applied.target, applied.kind, policy=policy,
                                               params=params)
                assert not err and not outcome.truncated
                results[policy] = outcome
            assert len({o.feasible for o in results.values()}) == 1, s["id"]
            if results["exhaustive"].feasible:
                best = results["exhaustive"].best.score.fitness
                assert results["greedy"].best.score.fitness <= best + 1e-9, s["id"]
                assert results["rank"].best.score.fitness <= best + 1e-9, s["id"]

    async def test_greedy_can_be_suboptimal(self):
        """Recorded in Phase 3: E1g misses the exhaustive optimum on some scenarios (14 of 130)."""
        gaps = 0
        for s in [x for x in load_scenarios() if x["kind"] in ("return_cancellation", "hotel_overbooked")]:
            orch, store, _, applied = await disrupted(s)
            fitness = {}
            for policy in ("greedy", "exhaustive"):
                work = copy.deepcopy(store)
                apply_event(work, event_of(s))
                outcome, _, _ = await repair(orch.registry, work, applied.target, applied.kind, policy=policy)
                fitness[policy] = outcome.best.score.fitness
            gaps += fitness["exhaustive"] > fitness["greedy"] + 1e-9
        assert gaps > 0

    async def test_deterministic(self):
        s = scenario("santorini_solo_luxury__hotel_overbooked")
        orch, store, _, applied = await disrupted(s)
        summaries = []
        for _ in range(2):
            work = copy.deepcopy(store)
            apply_event(work, event_of(s))
            outcome, _, _ = await repair(orch.registry, work, applied.target, applied.kind, policy="exhaustive")
            summary = outcome.summary()
            summary["budget"].pop("timed_out")
            summaries.append(summary)
        assert summaries[0] == summaries[1]

    async def test_facts_target_is_never_regenerated(self):
        for s in [x for x in load_scenarios() if x["kind"] in ("outbound_delay", "storm_mid_stay", "hotel_price_spike")]:
            orch, store, work, applied = await disrupted(s)
            if not applied.has_effect:
                continue
            outcome, _, _ = await repair(orch.registry, work, applied.target, applied.kind, policy="exhaustive")
            assert s["target"] not in outcome.summary()["region"]

    async def test_evaluation_budget_truncates_exhaustive(self):
        s = scenario("santorini_solo_luxury__hotel_overbooked")
        orch, _, work, applied = await disrupted(s)
        outcome, _, _ = await repair(orch.registry, work, applied.target, applied.kind, policy="exhaustive",
                                     params=SearchParams(max_exhaustive=3))
        assert outcome.truncated and len(outcome.plans) == 3 and outcome.budget.plan_evaluations == 3

    async def test_timeout_is_flagged(self):
        s = scenario("santorini_solo_luxury__hotel_overbooked")
        orch, _, work, applied = await disrupted(s)
        outcome, _, _ = await repair(orch.registry, work, applied.target, applied.kind, policy="exhaustive",
                                     params=SearchParams(timeout_s=0))
        assert outcome.truncated and outcome.budget.timed_out


class TestOptionCache:
    async def test_key_includes_the_frozen_context(self):
        orch, store = await planned()
        node = store.get_node("flight_outbound")
        ri = Scheduler._resolve_inputs(None, store, node.id)
        budget = EvalBudget(10, 60)
        gen = OptionGenerator(orch.registry, budget)
        await gen.options(node, ri, {}, 3)
        await gen.options(node, ri, {}, 3)
        assert (budget.option_generations, budget.option_cache_hits) == (1, 1)
        await gen.options(node, ri, {"previous_output": store.get_node(node.id).output}, 3)
        await gen.options(node, ri, {}, 2)
        assert budget.option_generations == 3

    async def test_an_agent_exception_is_a_dead_end_not_a_crash(self):
        orch, store = await planned()

        class Boom:
            async def candidates(self, *args):
                raise RuntimeError("boom")

        registry = type("R", (), {"get": lambda self, node: Boom()})()
        found = await OptionGenerator(registry, EvalBudget(10, 60)).options(store.get_node("hotel_stay"), {}, {}, 3)
        assert len(found) == 1 and not found[0].success and "boom" in found[0].error


class TestCommit:
    async def test_committed_plan_keeps_frozen_tasks_and_all_contracts(self):
        s = scenario("santorini_solo_luxury__outbound_delay_300")
        orch, _, work, applied = await disrupted(s)
        reference = copy.deepcopy(work)
        outcome, changed, err = await repair(orch.registry, work, applied.target, applied.kind, policy="greedy")
        region = set(outcome.summary()["region"])
        assert not err and outcome.feasible and changed
        assert all(n.output == reference.get_node(n.id).output for n in reference.list_nodes() if n.id not in region)
        assert all(c.holds for c in check_graph_contracts(work))
        assert all("pending_descriptions" not in (n.output or {}) for n in work.list_nodes())
        assert work.get_history("airport_transfer_in")[-1].reason.startswith("repair (greedy)")

    async def test_failed_commit_leaves_the_store_unchanged(self, monkeypatch):
        s = scenario("santorini_solo_luxury__outbound_delay_300")
        orch, _, work, applied = await disrupted(s)
        before = copy.deepcopy(work.to_dict())
        monkeypatch.setattr(commit_module, "validate_plan",
                            lambda *a, **k: ValidationReport(contracts=["forced failure"]))
        outcome, changed, err = await repair(orch.registry, work, applied.target, applied.kind, policy="greedy")
        assert outcome.feasible and changed == [] and "forced failure" in err
        assert work.to_dict() == before

    async def test_no_feasible_plan_commits_nothing(self):
        s = scenario("santorini_solo_luxury__outbound_delay_600")
        orch, _, work, applied = await disrupted(s)
        before = copy.deepcopy(work.to_dict())
        outcome, changed, err = await repair(orch.registry, work, applied.target, applied.kind, policy="exhaustive")
        assert not outcome.feasible and changed == [] and err == ""
        assert outcome.summary()["dead_ends"]
        assert work.to_dict() == before


class TestReplannerWiring:
    @pytest.mark.parametrize("strategy", ["greedy", "exhaustive"])
    async def test_handle_disruption(self, strategy):
        orch, store = await planned()
        work = copy.deepcopy(store)
        outcome = await orch.handle_disruption(work, event_of(scenario("santorini_solo_luxury__outbound_delay_300")),
                                               strategy=strategy)
        replan = outcome.replan
        assert outcome.status == "replanned" and replan.strategy == strategy
        assert replan.repair["committed"] and replan.repair["feasible"]
        assert set(replan.stale) == set(replan.regenerated) == set(replan.repair["region"])
        assert replan.contracts_holding == replan.contracts_total

    async def test_no_effect_shortcut(self):
        orch, store = await planned()
        outcome = await orch.handle_disruption(copy.deepcopy(store),
                                               event_of(scenario("santorini_solo_luxury__outbound_delay_15")),
                                               strategy="greedy")
        assert outcome.status == "no_effect"

    async def test_infeasible_is_reported_not_committed(self):
        orch, store = await planned()
        work = copy.deepcopy(store)
        outcome = await orch.handle_disruption(work, event_of(scenario("santorini_solo_luxury__outbound_delay_600")),
                                               strategy="greedy")
        assert not outcome.replan.repair["committed"] and outcome.replan.regenerated == []
        untouched = [n for n in store.list_nodes() if n.id != "flight_outbound"]
        assert all(work.get_node(n.id).output == n.output for n in untouched)


class TestApi:
    @pytest.mark.parametrize("strategy", ["greedy", "exhaustive"])
    def test_disruption_endpoint_accepts_search_strategies(self, strategy):
        import uuid
        from unittest.mock import patch

        from fastapi.testclient import TestClient
        from sqlalchemy.pool import SingletonThreadPool
        from sqlmodel import SQLModel, create_engine

        import app.db.database as db_module
        import app.db.models  # noqa: F401
        from app.main import app as fa_app
        from tests.test_disruptions_api import GOAL

        url = f"sqlite:///file:repair_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=SingletonThreadPool)
        SQLModel.metadata.create_all(engine)
        with patch.object(db_module, "engine", engine):
            client = TestClient(fa_app)
            trip_id = client.post("/trips", json=GOAL).json()["trip_id"]
            resp = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h", "strategy": strategy})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "replanned" and body["contracts_holding"] == body["contracts_total"]
        assert body["replan"]["repair"]["committed"] and body["replan"]["strategy"] == strategy
