"""
Hybrid work, Phase 4 — conflict handling (paper §V: "report the conflict and request a
user decision instead of silently relaxing constraints"), the T2 DECIDE action, and the
validation flag every replan now carries.
"""

from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.run_benchmark import PlannedTrip  # noqa: E402
from eval.scenario_set import load_scenarios, load_trips  # noqa: E402

from app.core.metrics import _plan_output  # noqa: E402
from app.core.monitor.apply import apply_event  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.models import NodeStatus, RiskTier  # noqa: E402
from app.core.risk_gate import (ALWAYS_T2, SPEND_KINDS, ActionKind, ActionStatus, GateConflict,  # noqa: E402
                                unsafe_actions)

UNREPAIRABLE = [s for s in load_scenarios() if s["id"].endswith("outbound_delay_600")]
_trips: dict[str, PlannedTrip] = {}


async def booked(trip_id: str) -> PlannedTrip:
    """A trip planned and booked by the simulated user, as in the benchmark (cached)."""
    if trip_id not in _trips:
        _trips[trip_id] = await PlannedTrip.create(load_trips()[trip_id]["goal"])
    return _trips[trip_id]


def event_of(s: dict):
    return DisruptionSimulator(seed=0).make(s["type"], s["target"], **s["facts"])


async def run(s: dict, strategy: str):
    """(trip, store before, store after, ledger, outcome) on copies of the booked trip."""
    trip = await booked(s["trip"])
    store, ledger = copy.deepcopy(trip.store), copy.deepcopy(trip.ledger)
    outcome = await trip.orch.handle_disruption(store, event_of(s), strategy=strategy, ledger=ledger)
    return trip, trip.store, store, ledger, outcome


def after_event(trip: PlannedTrip, s: dict):
    reference = copy.deepcopy(trip.store)
    apply_event(reference, event_of(s))
    return reference


class TestConflicts:
    @pytest.mark.parametrize("strategy", ["greedy", "exhaustive"])
    async def test_every_unrepairable_scenario_is_a_conflict(self, strategy):
        assert len(UNREPAIRABLE) == 10
        for s in UNREPAIRABLE:
            trip, _, store, ledger, outcome = await run(s, strategy)
            assert outcome.status == "conflict", s["id"]
            reference = after_event(trip, s)
            assert [n.output for n in store.list_nodes()] == [reference.get_node(n.id).output
                                                             for n in store.list_nodes()], "nothing committed"
            assert outcome.replan.regenerated == [] and not outcome.replan.validation["silent_failure"]
            new = [a for a in ledger.list() if a.action_id not in {x.action_id for x in trip.ledger.list()}]
            assert [(a.kind, a.tier, a.status) for a in new] == [(ActionKind.DECIDE, RiskTier.T2, ActionStatus.PENDING)]
            assert outcome.actions == new
            conflict = new[0].option
            assert conflict["reason"] == "no feasible plan" and conflict["best_effort"]
            assert any("FLIGHT_BEFORE_CHECKIN" in v for v in conflict["violations"])
            assert [o["id"] for o in conflict["options"]] == ["accept_best_effort", "keep_current"]

    async def test_a_conflict_marks_no_task(self):
        trip, _, store, _, _ = await run(UNREPAIRABLE[0], "greedy")
        assert all(store.get_node(n.id).status == n.status for n in trip.store.list_nodes()
                   if n.id != UNREPAIRABLE[0]["target"])

    async def test_e1_keeps_its_status_and_is_flagged(self):
        for s in UNREPAIRABLE:
            _, _, _, _, outcome = await run(s, "contract")
            validation = outcome.replan.validation
            assert outcome.status == "replanned"
            assert not validation["feasible"] and validation["silent_failure"] and validation["contracts"]

    async def test_feasible_replans_are_not_flagged(self):
        for strategy in ("contract", "full", "naive", "greedy"):
            _, _, _, _, outcome = await run(next(s for s in load_scenarios()
                                                 if s["id"] == "santorini_solo_luxury__outbound_delay_300"), strategy)
            assert outcome.status == "replanned"
            assert outcome.replan.validation["feasible"] and not outcome.replan.validation["silent_failure"], strategy


class TestDecide:
    async def test_decide_is_t2_by_its_own_rule_and_spends_nothing(self):
        _, _, _, ledger, outcome = await run(UNREPAIRABLE[0], "greedy")
        action = outcome.actions[0]
        assert ActionKind.DECIDE not in ALWAYS_T2 and ActionKind.DECIDE not in SPEND_KINDS
        assert action.amount == 0 and any("user decides" in r for r in action.reasons)

    @pytest.mark.parametrize("s", UNREPAIRABLE, ids=lambda s: s["trip"])
    async def test_accepting_commits_the_best_effort_plan_as_e1_would(self, s):
        trip, _, store, ledger, outcome = await run(s, "greedy")
        action = outcome.actions[0]
        offer = action.option["best_effort"]
        await trip.orch.approve_action(store, ledger, action.action_id, decided_by="user")
        assert action.status == ActionStatus.EXECUTED and action.decision == "approved"
        assert action.result["violations_accepted"] == offer["violations"]
        # The gate then re-stamps a kept booking (booking_ref etc.), as after any replan; the plan itself is the offer.
        assert all(_plan_output(store.get_node(nid).output) == out for nid, out in offer["outputs"].items())
        assert all(store.get_node(n).status in (NodeStatus.DONE, NodeStatus.AWAITING_APPROVAL) for n in offer["outputs"])
        # Best effort is E1's own plan made explicit: the same region, outputs and booking stamps.
        _, _, e1_store, _, e1 = await run(s, "contract")
        assert set(offer["region"]) == set(e1.replan.stale)
        assert all(e1_store.get_node(nid).output == store.get_node(nid).output for nid in offer["outputs"])

    async def test_rejecting_keeps_the_plan(self):
        s = UNREPAIRABLE[0]
        trip, _, store, ledger, outcome = await run(s, "greedy")
        before = copy.deepcopy(store.to_dict())
        trip.orch.reject_action(store, ledger, outcome.actions[0].action_id)
        assert outcome.actions[0].status == ActionStatus.REJECTED and store.to_dict() == before

    async def test_a_plan_changed_since_the_conflict_supersedes_it(self):
        s = UNREPAIRABLE[0]
        trip, _, store, ledger, outcome = await run(s, "greedy")
        day = store.get_node("day_3_plan")
        store.update_node_status(day.id, day.status, output={**day.output, "theme": "changed meanwhile"})
        before = copy.deepcopy(store.to_dict())
        with pytest.raises(GateConflict):
            await trip.orch.approve_action(store, ledger, outcome.actions[0].action_id)
        assert outcome.actions[0].status == ActionStatus.SUPERSEDED and store.to_dict() == before

    async def test_auto_accepting_without_approval_is_unsafe(self):
        trip, _, store, ledger, outcome = await run(UNREPAIRABLE[0], "greedy")
        action = outcome.actions[0]
        await trip.orch.gate.execute_without_approval(store, ledger, action.action_id, trip.orch.executor())
        assert action.decision == "auto"
        assert action.action_id in {u["action_id"] for u in unsafe_actions(ledger, [])}


class TestApi:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import SingletonThreadPool
        from sqlmodel import SQLModel, create_engine

        import app.db.database as db_module
        import app.db.models  # noqa: F401
        from app.main import app as fa_app

        url = f"sqlite:///file:conflict_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=SingletonThreadPool)
        SQLModel.metadata.create_all(engine)
        with patch.object(db_module, "engine", engine):
            yield TestClient(fa_app)

    def _conflict(self, client):
        from tests.test_disruptions_api import GOAL

        trip_id = client.post("/trips", json=GOAL).json()["trip_id"]
        for card in client.get("/approvals", params={"trip_id": trip_id}).json():
            assert client.post(f"/approvals/{card['action_id']}", json={"decision": "approve"}).status_code == 200
        resp = client.post(f"/trips/{trip_id}/disruptions",
                           json={"type": "FLIGHT_DELAY", "target_node": "flight_outbound",
                                 "new_facts": {"delay_minutes": 600}, "strategy": "greedy"})
        assert resp.status_code == 200, resp.text
        return trip_id, resp.json()

    @pytest.mark.parametrize("decision", ["approve", "reject"])
    def test_conflict_round_trip(self, client, decision):
        trip_id, body = self._conflict(client)
        assert body["status"] == "conflict" and body["regenerated"] == []
        decide = [a for a in body["actions"] if a["kind"] == "decide"]
        assert len(decide) == 1 and decide[0]["tier"] == "T2"
        pending = client.get("/approvals", params={"trip_id": trip_id}).json()
        assert [a["action_id"] for a in pending] == [decide[0]["action_id"]]
        history = client.get(f"/trips/{trip_id}/disruptions").json()
        assert history[-1]["status"] == "conflict"

        # The decision is made on the graph as stored and reloaded from the database.
        resp = client.post(f"/approvals/{decide[0]['action_id']}", json={"decision": decision})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["action"]["status"] == ("executed" if decision == "approve" else "rejected")
        assert out["contracts_holding"] < out["contracts_total"]      # the accepted plan still breaks the cutoff
        if decision == "approve":
            graph = client.get(f"/trips/{trip_id}/graph").json()
            versions = {n["id"]: n["version"] for n in graph["nodes"]}
            assert all(versions[nid] > 1 for nid in decide[0]["option"]["best_effort"]["region"])
