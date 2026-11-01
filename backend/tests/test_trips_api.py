"""
Phase 3 — end-to-end tests for the /trips API (mock mode, isolated SQLite).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import SingletonThreadPool
from sqlmodel import Session, SQLModel, create_engine

import app.db.models  # noqa: F401 — register table metadata
import app.db.database as db_module
from app.api.trips import get_orchestrator
from app.core.orchestrator import Orchestrator
from app.db import memory_repo
from app.llm.client import MockLLM
from app.main import app as fa_app

SANTORINI = {
    "destination": "Santorini",
    "departure_city": "Mumbai",
    "departure_date": "2026-06-14",
    "return_date": "2026-06-19",
    "travelers": 1,
    "budget": 4000,
    "currency": "USD",
    "travel_style": "luxury",
    "interests": ["relaxation", "food"],
    "preferences": {"pace": "relaxed", "accommodation": "hotel"},
}


@pytest.fixture(name="engine")
def engine_fixture():
    url = f"sqlite:///file:trips_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=SingletonThreadPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="llm")
def llm_fixture():
    return MockLLM()


@pytest.fixture(name="client")
def client_fixture(engine, llm):
    fa_app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(llm=llm)
    with patch.object(db_module, "engine", engine):
        yield TestClient(fa_app)
    fa_app.dependency_overrides.pop(get_orchestrator, None)


def create(client, **overrides) -> dict:
    resp = client.post("/trips", json=SANTORINI | overrides)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreateTrip:
    def test_create_trip_plans_and_executes(self, client):
        body = create(client)
        assert body["success"] is True
        assert body["decomposition"]["source"] == "llm"
        assert body["decomposition"]["attempts"] == 1
        assert len(body["nodes"]) == 16
        assert body["tasks_failed"] == [] and body["tasks_skipped"] == []
        assert body["contracts_total"] == body["contracts_holding"] == 27
        # Phase 6: the bookings are proposed as T2 approvals; everything else is done.
        awaiting = {n["id"] for n in body["nodes"] if n["status"] == "AWAITING_APPROVAL"}
        assert awaiting == {"flight_outbound", "flight_return", "hotel_stay"}
        assert all(n["status"] == "DONE" for n in body["nodes"] if n["id"] not in awaiting)
        assert {(a["node_id"], a["tier"], a["kind"]) for a in body["actions"]} == {
            ("flight_outbound", "T2", "book"), ("flight_return", "T2", "book"), ("hotel_stay", "T2", "book")}

    def test_create_trip_without_booking_proposals(self, client):
        body = create(client, propose_bookings=False)
        assert body["actions"] == []
        assert all(n["status"] == "DONE" for n in body["nodes"])

    def test_invalid_goal_rejected(self, client):
        resp = client.post("/trips", json=SANTORINI | {"return_date": "2026-06-10"})
        assert resp.status_code == 422

    def test_missing_field_rejected(self, client):
        payload = {k: v for k, v in SANTORINI.items() if k != "destination"}
        assert client.post("/trips", json=payload).status_code == 422

    def test_fallback_is_reported(self, engine):
        bad_llm = MockLLM(handler=lambda messages: "garbage")
        fa_app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(llm=bad_llm, max_attempts=2)
        try:
            with patch.object(db_module, "engine", engine):
                body = create(TestClient(fa_app))
        finally:
            fa_app.dependency_overrides.pop(get_orchestrator, None)
        assert body["decomposition"]["source"] == "fallback"
        assert body["decomposition"]["attempts"] == 2
        assert body["success"] is True


class TestGraphAndItinerary:
    def test_graph_endpoint(self, client):
        trip_id = create(client)["trip_id"]
        resp = client.get(f"/trips/{trip_id}/graph")
        assert resp.status_code == 200
        graph = resp.json()
        assert len(graph["nodes"]) == 16 and len(graph["edges"]) == 27
        assert all(e["holds"] is True for e in graph["edges"])
        assert all(e["explanation"] for e in graph["edges"])
        # Topological order: outbound flight before its transfer, budget last-ish
        order = [n["id"] for n in graph["nodes"]]
        assert order.index("flight_outbound") < order.index("airport_transfer_in") < order.index("day_1_plan")
        assert order.index("budget_check") > order.index("day_5_plan")

    def test_itinerary_endpoint(self, client):
        trip_id = create(client)["trip_id"]
        resp = client.get(f"/trips/{trip_id}/itinerary")
        assert resp.status_code == 200
        it = resp.json()
        assert it["destination"] == "Santorini" and it["departure_city"] == "Mumbai"
        assert [f["direction"] for f in it["flights"]] == ["outbound", "return"]
        assert [t["direction"] for t in it["transfers"]] == ["arrival", "departure"]
        assert it["stay"]["check_in"] == "2026-06-14"
        assert [d["day"] for d in it["days"]] == [1, 2, 3, 4, 5]
        assert all(d["weather"] is not None and d["activities"] for d in it["days"])
        assert it["budget"]["within_budget"] is True

    def test_unknown_trip_returns_404(self, client):
        assert client.get("/trips/nope/graph").status_code == 404
        assert client.get("/trips/nope/itinerary").status_code == 404


class TestMemory:
    def test_trip_is_remembered_and_used_next_time(self, client, engine, llm):
        create(client, user_id="asha")
        with Session(engine) as session:
            ctx = memory_repo.get_user_context("asha", session)
        assert [t["destination"] for t in ctx["past_trips"]] == ["Santorini"]
        assert ctx["preferences"]["interests"] == ["relaxation", "food"]
        assert ctx["preferences"]["pace"] == "relaxed"

        # Second trip without interests: profile fills them in and the prompt mentions the past trip.
        second = create(client, user_id="asha", destination="Lisbon", interests=[])
        prompt = next(m[1]["content"] for m in reversed(llm.received) if "<trip_goal>" in m[1]["content"])
        assert "Past trips: Santorini" in prompt
        trip_graph = client.get(f"/trips/{second['trip_id']}/graph").json()
        day = next(n for n in trip_graph["nodes"] if n["id"] == "day_2_plan")
        assert day["output"]["theme"].startswith("Relaxation")

    def test_users_are_isolated(self, client, engine):
        create(client, user_id="u1")
        with Session(engine) as session:
            assert memory_repo.get_user_context("u2", session)["past_trips"] == []
