"""
Phase 6 — approvals API, T1 undo and the live WebSocket (mock mode, isolated
SQLite). Every test ends with an audit: each sandbox booking must be backed by
a T2 action the user explicitly approved (unsafe-action rate = 0).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import SingletonThreadPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.db.models  # noqa: F401 — register table metadata
import app.db.database as db_module
from app.api.trips import get_orchestrator
from app.core.orchestrator import Orchestrator
from app.core.risk_gate import unsafe_action_rate, unsafe_actions
from app.db import approval_repo
from app.db.models import ApprovalRecord
from app.integrations.http import ProviderError
from app.main import app as fa_app

GOAL = {
    "destination": "Santorini", "departure_city": "Mumbai", "departure_date": "2027-06-14",
    "return_date": "2027-06-19", "travelers": 1, "budget": 4000, "currency": "USD",
    "travel_style": "luxury", "interests": ["relaxation", "food"],
}


@pytest.fixture(name="engine")
def engine_fixture():
    url = f"sqlite:///file:appr_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=SingletonThreadPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="orch")
def orch_fixture() -> Orchestrator:
    return Orchestrator()        # shared, so the sandbox's execution log spans requests


@pytest.fixture(name="client")
def client_fixture(engine, orch):
    fa_app.dependency_overrides[get_orchestrator] = lambda: orch
    with patch.object(db_module, "engine", engine):
        yield TestClient(fa_app)
    fa_app.dependency_overrides.pop(get_orchestrator, None)


@pytest.fixture(name="trip_id")
def trip_fixture(client, engine, orch) -> str:
    resp = client.post("/trips", json=GOAL)
    assert resp.status_code == 201, resp.text
    trip_id = resp.json()["trip_id"]
    yield trip_id
    # Audit after every test: no execution without an explicit approval.
    with Session(engine) as session:
        ledger = approval_repo.load_ledger(trip_id, session)
    executions = orch.registry.providers.booking.executions
    assert unsafe_actions(ledger, executions) == []
    assert unsafe_action_rate(ledger, executions) == 0.0


def executions(orch) -> list[dict]:
    return orch.registry.providers.booking.executions


def pending(client, trip_id) -> dict[str, dict]:
    resp = client.get("/approvals", params={"trip_id": trip_id})
    assert resp.status_code == 200, resp.text
    return {a["node_id"]: a for a in resp.json()}


def decide(client, action_id, decision="approve"):
    return client.post(f"/approvals/{action_id}", json={"decision": decision})


def approve_all(client, trip_id) -> None:
    for card in pending(client, trip_id).values():
        assert decide(client, card["action_id"]).status_code == 200


def node(client, trip_id, node_id) -> dict:
    return next(n for n in client.get(f"/trips/{trip_id}/graph").json()["nodes"] if n["id"] == node_id)


class TestApprovals:
    def test_planning_raises_three_cards(self, client, trip_id, orch):
        cards = pending(client, trip_id)
        assert set(cards) == {"flight_outbound", "flight_return", "hotel_stay"}
        for card in cards.values():
            assert card["tier"] == "T2" and card["kind"] == "book" and card["status"] == "pending"
            assert card["title"] and card["why"] and card["amount"] > 0 and card["currency"] == "USD"
            assert card["option"]["offer_id"]
        assert node(client, trip_id, "hotel_stay")["status"] == "AWAITING_APPROVAL"
        assert executions(orch) == []

    def test_approve_books_and_records_memory(self, client, trip_id, orch, engine):
        card = pending(client, trip_id)["flight_outbound"]
        resp = decide(client, card["action_id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"]["status"] == "executed" and body["action"]["decision"] == "approved"
        assert body["node"]["status"] == "DONE"
        assert body["node"]["output"]["booking_status"] == "booked"
        assert body["contracts_holding"] == body["contracts_total"] == 27
        assert [e["action_id"] for e in executions(orch)] == [card["action_id"]]
        with Session(engine) as session:
            rows = session.exec(select(ApprovalRecord)).all()
        assert [(r.node_id, r.decision, r.user_id) for r in rows] == [("flight_outbound", "approved", "demo-user")]
        assert "flight_outbound" not in pending(client, trip_id)

    def test_double_approval_conflicts(self, client, trip_id, orch):
        card = pending(client, trip_id)["flight_return"]
        assert decide(client, card["action_id"]).status_code == 200
        assert decide(client, card["action_id"]).status_code == 409
        assert decide(client, card["action_id"], "reject").status_code == 409
        assert len(executions(orch)) == 1

    def test_reject_then_approve_conflicts(self, client, trip_id, orch, engine):
        card = pending(client, trip_id)["hotel_stay"]
        resp = decide(client, card["action_id"], "reject")
        assert resp.status_code == 200
        assert resp.json()["node"]["output"]["booking_status"] == "rejected"
        assert decide(client, card["action_id"]).status_code == 409
        assert executions(orch) == []
        rejected = client.get("/approvals", params={"trip_id": trip_id, "status": "rejected"}).json()
        assert [a["action_id"] for a in rejected] == [card["action_id"]]

    def test_unknown_and_invalid_requests(self, client, trip_id, orch):
        assert decide(client, "act_doesnotexist").status_code == 404
        card = pending(client, trip_id)["hotel_stay"]
        assert client.post(f"/approvals/{card['action_id']}", json={"decision": "maybe"}).status_code == 422
        assert client.post(f"/approvals/{card['action_id']}", json={}).status_code == 422
        assert executions(orch) == []

    def test_timeout_reminds_but_never_executes(self, client, trip_id, orch, engine):
        with Session(engine) as session:
            ledger = approval_repo.load_ledger(trip_id, session)
            for action in ledger.pending():
                action.remind_at = datetime.now(timezone.utc) - timedelta(hours=1)
            approval_repo.save_ledger(ledger, session)
        cards = pending(client, trip_id)
        assert len(cards) == 3
        assert all(c["status"] == "pending" and c["reminders"] == 1 for c in cards.values())
        assert all(node(client, trip_id, nid)["status"] == "AWAITING_APPROVAL" for nid in cards)
        assert executions(orch) == []

    def test_failed_booking_returns_502_and_stays_pending(self, client, trip_id, orch, monkeypatch):
        booking = orch.registry.providers.booking

        async def down(quote, grant):
            raise ProviderError("sandbox down")

        monkeypatch.setattr(booking, "book_hotel", down)
        card = pending(client, trip_id)["hotel_stay"]
        resp = decide(client, card["action_id"])
        assert resp.status_code == 502 and "nothing was booked" in resp.json()["detail"]
        again = pending(client, trip_id)["hotel_stay"]
        assert again["status"] == "pending" and "sandbox down" in again["last_error"]
        assert executions(orch) == []
        monkeypatch.undo()
        assert decide(client, card["action_id"]).status_code == 200
        assert len(executions(orch)) == 1

    @pytest.mark.parametrize("strategy", ["contract", "full", "naive"])
    def test_cancellation_supersedes_unapproved_card(self, client, trip_id, orch, strategy):
        old = pending(client, trip_id)["flight_outbound"]
        resp = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "cancel_outbound", "strategy": strategy})
        assert resp.status_code == 200, resp.text
        new = pending(client, trip_id)["flight_outbound"]
        assert new["action_id"] != old["action_id"] and new["offer_id"] != old["offer_id"]
        assert decide(client, old["action_id"]).status_code == 409
        assert executions(orch) == []

    @pytest.mark.parametrize("strategy", ["contract", "full", "naive"])
    def test_rebooking_after_cancellation_waits_for_approval(self, client, trip_id, orch, strategy):
        approve_all(client, trip_id)
        assert len(executions(orch)) == 3
        resp = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "cancel_outbound", "strategy": strategy})
        assert resp.status_code == 200, resp.text
        change = next(a for a in resp.json()["actions"] if a["tier"] == "T2")
        assert change["kind"] == "change" and change["node_id"] == "flight_outbound"
        assert change["price_difference"] == pytest.approx(change["amount"] - change["old_amount"])
        assert node(client, trip_id, "flight_outbound")["status"] == "AWAITING_APPROVAL"
        assert len(executions(orch)) == 3
        assert decide(client, change["action_id"]).status_code == 200
        assert [e["operation"] for e in executions(orch)[3:]] == ["book", "cancel"]


class TestT1Undo:
    def test_delay_notifies_and_undo_restores(self, client, trip_id, orch):
        approve_all(client, trip_id)
        resp = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"})
        body = resp.json()
        assert body["stale"] == ["airport_transfer_in", "day_1_plan", "budget_check"]
        t1 = [a for a in body["actions"] if a["tier"] == "T1"]
        assert [(a["title"], a["undoable"]) for a in t1] == [("Rebooked pickup for 19:30", True)]
        assert not [a for a in body["actions"] if a["tier"] == "T2"]

        undo = client.post(f"/actions/{t1[0]['action_id']}/undo")
        assert undo.status_code == 200, undo.text
        assert undo.json()["node"]["output"]["pickup_time"] == "2027-06-14T14:30:00"
        assert ["flight_outbound", "airport_transfer_in"] in undo.json()["broken_edges"]
        assert client.post(f"/actions/{t1[0]['action_id']}/undo").status_code == 409
        assert len(executions(orch)) == 3

    def test_only_t1_can_be_undone_and_t1_needs_no_approval(self, client, trip_id):
        body = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"}).json()
        t0 = next(a for a in body["actions"] if a["tier"] == "T0")
        t1 = next(a for a in body["actions"] if a["tier"] == "T1")
        assert client.post(f"/actions/{t0['action_id']}/undo").status_code == 409
        assert decide(client, t1["action_id"]).status_code == 409
        assert client.post("/actions/act_nope/undo").status_code == 404
        actions = client.get(f"/trips/{trip_id}/actions").json()
        assert {a["tier"] for a in actions} == {"T0", "T1", "T2"}


class TestLiveUpdates:
    def test_websocket_streams_replans_and_approvals(self, client, trip_id):
        with client.websocket_connect(f"/ws/trips/{trip_id}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert len(snapshot["data"]["approvals"]) == 3
            assert {n["id"]: n["status"] for n in snapshot["data"]["nodes"]}["hotel_stay"] == "AWAITING_APPROVAL"

            ws.send_text("ping")
            assert ws.receive_json()["type"] == "pong"

            client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"})
            kinds = [ws.receive_json() for _ in range(3)]
            assert [m["type"] for m in kinds] == ["replan", "node_status", "notification"]
            assert kinds[0]["data"]["regenerated"] == ["airport_transfer_in", "day_1_plan", "budget_check"]
            assert kinds[2]["data"]["title"] == "Rebooked pickup for 19:30"

            card = pending(client, trip_id)["hotel_stay"]
            decide(client, card["action_id"])
            decided = ws.receive_json()
            assert decided["type"] == "approval_decided" and decided["data"]["status"] == "executed"
            statuses = {n["id"]: n["status"] for n in ws.receive_json()["data"]["nodes"]}
            assert statuses["hotel_stay"] == "DONE"

    def test_unknown_trip_closes(self, client):
        with client.websocket_connect("/ws/trips/nope") as ws:
            assert ws.receive_json()["type"] == "error"
