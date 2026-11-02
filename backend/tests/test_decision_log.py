"""
Hybrid work, Phase 8 — the decision log (paper §VIII: the disruption, the affected tasks,
the violated contracts, the alternatives, the selected repair, the validation outcome and
the approval), built for every strategy and served by the API.
"""

from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.scenario_set import load_scenarios, load_trips  # noqa: E402

from app.core.monitor.apply import apply_event  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.core.repair.commit import repair  # noqa: E402
from app.core.repair.decision import CONSIDERED_LIMIT, decision_log, option_label, search_log  # noqa: E402
from app.core.repair.search import RepairSearch, SearchParams, exhaustive  # noqa: E402
from app.core.models import NodeType  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402

PAPER_FIELDS = {"disruption", "affected", "alternatives", "selected", "validation", "approvals"}
_planned: dict[str, tuple] = {}


async def planned(trip_id: str = "santorini_solo_luxury"):
    if trip_id not in _planned:
        orch = Orchestrator()
        result = await orch.plan_trip(TripGoal.model_validate(load_trips()[trip_id]["goal"]))
        _planned[trip_id] = (orch, result.graph_store)
    return _planned[trip_id]


def scenario(sid: str) -> dict:
    return next(s for s in load_scenarios() if s["id"] == sid)


def event_of(s: dict):
    return DisruptionSimulator(seed=0).make(s["type"], s["target"], **s["facts"])


async def disrupted(sid: str):
    s = scenario(sid)
    orch, store = await planned(s["trip"])
    work = copy.deepcopy(store)
    return orch, work, apply_event(work, event_of(s))


class TestSearchLog:
    async def test_options_come_from_the_cache_and_are_consistent(self):
        for sid in ("santorini_solo_luxury__hotel_overbooked", "santorini_solo_luxury__outbound_delay_300",
                    "santorini_solo_luxury__return_cancellation", "santorini_solo_luxury__storm_mid_stay"):
            orch, work, applied = await disrupted(sid)
            search = RepairSearch(orch.registry, work, applied.target, applied.kind, params=SearchParams(timeout_s=60),
                                  max_plan_evaluations=20000)
            outcome = await exhaustive(search)
            generations = search.budget.option_generations
            log = await search_log(search, outcome)
            assert search.budget.option_generations == generations, "no agent was called again"
            best = outcome.best
            options = log["alternatives"]["options_by_task"]
            assert list(options) == [n for n in search.order if n in best.state.region]
            for nid, entries in options.items():
                chosen = [e for e in entries if e["chosen"]]
                assert len(chosen) == 1 and chosen[0]["rank"] == best.state.choices[nid]
                assert chosen[0]["rejected_because"] is None
            considered = log["alternatives"]["considered"]
            assert len(considered) == min(CONSIDERED_LIMIT, len(outcome.plans))
            assert considered[0] == log["selected"] and log["selected"]["choices"] == best.state.choices
            assert [a["task"] for a in log["affected"]] == list(best.state.region)
            assert all(a["reason"] for a in log["affected"])

    def test_option_labels(self):
        assert option_label(NodeType.TRANSFER, {"pickup_time": "2027-06-14T19:30:00",
                                                "dropoff_time": "2027-06-14T19:40:00"}) == \
            "pickup 19:30, drop-off 19:40"
        assert option_label("DAY_PLAN", {"activities": []}) == "0 activities: none"


class TestStages:
    async def test_feasible_repair_has_search_and_commit_stages(self):
        orch, work, applied = await disrupted("santorini_solo_luxury__outbound_delay_300")
        outcome, changed, _ = await repair(orch.registry, work, applied.target, applied.kind, policy="greedy")
        assert set(outcome.stages) == {"search", "commit"} and changed
        assert outcome.stages["search"]["llm_calls"] == 0, "options are generated without descriptions"
        assert outcome.stages["commit"]["llm_calls"] >= 1, "the committed day plan gets its descriptions"
        assert all(stage["ms"] >= 0 for stage in outcome.stages.values())

    async def test_conflict_has_search_and_conflict_stages(self):
        orch, work, applied = await disrupted("santorini_solo_luxury__outbound_delay_600")
        outcome, _, _ = await repair(orch.registry, work, applied.target, applied.kind, policy="greedy")
        assert set(outcome.stages) == {"search", "conflict"} and outcome.decision_log["selected"] is None


class TestAssembledLog:
    @pytest.mark.parametrize("strategy", ["contract", "full", "naive", "greedy", "beam", "ga", "hybrid"])
    async def test_every_strategy_gives_the_paper_fields(self, strategy):
        orch, store = await planned()
        outcome = await orch.handle_disruption(copy.deepcopy(store),
                                               event_of(scenario("santorini_solo_luxury__outbound_delay_300")),
                                               strategy=strategy)
        data = outcome.to_dict()
        log = decision_log("t", strategy, "2026-11-01T00:00:00", data, [])
        assert PAPER_FIELDS <= set(log) and log["status"] == "replanned"
        assert log["validation"]["feasible"] and log["affected"]
        assert all(a["reason"] for a in log["affected"])
        searched = strategy not in ("contract", "full", "naive")
        assert ("considered" in log["alternatives"]) == searched
        assert log["alternatives"].get("searched", True) == searched
        if searched:
            assert log["selected"]["plan"]["feasible"] and log["cost"]["stages"]


class TestApi:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import SingletonThreadPool
        from sqlmodel import SQLModel, create_engine

        import app.db.database as db_module
        import app.db.models  # noqa: F401
        from app.main import app as fa_app

        url = f"sqlite:///file:decision_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=SingletonThreadPool)
        SQLModel.metadata.create_all(engine)
        with patch.object(db_module, "engine", engine):
            yield TestClient(fa_app)

    def _trip(self, client) -> str:
        from tests.test_disruptions_api import GOAL

        trip_id = client.post("/trips", json=GOAL).json()["trip_id"]
        for card in client.get("/approvals", params={"trip_id": trip_id}).json():
            client.post(f"/approvals/{card['action_id']}", json={"decision": "approve"})
        return trip_id

    def test_repair_then_conflict_then_approval(self, client):
        trip_id = self._trip(client)
        first = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h", "strategy": "greedy"}).json()
        log = client.get(f"/trips/{trip_id}/disruptions/{first['event']['event_id']}/decision").json()
        assert PAPER_FIELDS <= set(log) and log["status"] == "replanned" and log["strategy"] == "greedy"
        assert log["disruption"]["contracts_broken_by_event"] == [["flight_outbound", "airport_transfer_in"]]
        assert log["alternatives"]["options_by_task"]["airport_transfer_in"]
        assert log["approvals"] and all(a["action_id"] for a in log["approvals"])

        conflict = client.post(f"/trips/{trip_id}/disruptions",
                               json={"type": "FLIGHT_DELAY", "target_node": "flight_outbound",
                                     "new_facts": {"delay_minutes": 600}, "strategy": "greedy"}).json()
        # Phase 8 fix: each disruption of a trip has its own event ID (they used to repeat).
        assert conflict["event"]["event_id"] != first["event"]["event_id"]
        url = f"/trips/{trip_id}/disruptions/{conflict['event']['event_id']}/decision"
        log = client.get(url).json()
        assert log["status"] == "conflict" and log["conflict"]["best_effort_violations"]
        decide = [a for a in log["approvals"] if a["kind"] == "decide"]
        assert len(decide) == 1 and decide[0]["status"] == "pending"

        client.post(f"/approvals/{decide[0]['action_id']}", json={"decision": "approve"})
        log = client.get(url).json()
        decide = next(a for a in log["approvals"] if a["kind"] == "decide")
        assert decide["status"] == "executed" and decide["decision"] == "approved" and decide["decided_by"]

    def test_e1_log_and_unknown_event(self, client):
        trip_id = self._trip(client)
        e1 = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"}).json()
        log = client.get(f"/trips/{trip_id}/disruptions/{e1['event']['event_id']}/decision").json()
        assert log["alternatives"]["searched"] is False and log["validation"]["feasible"]
        assert [a["task"] for a in log["affected"]] == ["airport_transfer_in", "day_1_plan", "budget_check"]
        assert client.get(f"/trips/{trip_id}/disruptions/evt_nope/decision").status_code == 404
