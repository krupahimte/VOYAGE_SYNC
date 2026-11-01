"""
Phase 5 — end-to-end tests for the disruption endpoints (mock mode, isolated SQLite).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import SingletonThreadPool
from sqlmodel import SQLModel, create_engine

import app.db.models  # noqa: F401 — register table metadata
import app.db.database as db_module
from app.main import app as fa_app

GOAL = {
    "destination": "Santorini", "departure_city": "Mumbai", "departure_date": "2027-06-14",
    "return_date": "2027-06-19", "travelers": 1, "budget": 4000, "currency": "USD",
    "travel_style": "luxury", "interests": ["relaxation", "food"],
}


@pytest.fixture(name="client")
def client_fixture():
    url = f"sqlite:///file:disr_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=SingletonThreadPool)
    SQLModel.metadata.create_all(engine)
    with patch.object(db_module, "engine", engine):
        yield TestClient(fa_app)


@pytest.fixture(name="trip_id")
def trip_fixture(client) -> str:
    resp = client.post("/trips", json=GOAL)
    assert resp.status_code == 201, resp.text
    return resp.json()["trip_id"]


def versions(client, trip_id) -> dict[str, int]:
    return {n["id"]: n["version"] for n in client.get(f"/trips/{trip_id}/graph").json()["nodes"]}


class TestDisruptionEndpoint:
    def test_delay_5h_scenario(self, client, trip_id):
        before = versions(client, trip_id)
        resp = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "replanned"
        assert body["stale"] == ["airport_transfer_in", "day_1_plan", "budget_check"]
        assert body["contracts_holding"] == body["contracts_total"] == 27
        assert body["broken_edges"] == [["flight_outbound", "airport_transfer_in"]]

        after = versions(client, trip_id)
        moved = {nid for nid in before if after[nid] != before[nid]}
        assert moved == {"flight_outbound", "airport_transfer_in", "day_1_plan", "budget_check"}
        assert after["hotel_stay"] == before["hotel_stay"]

    def test_itinerary_reflects_the_replan(self, client, trip_id):
        client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"})
        it = client.get(f"/trips/{trip_id}/itinerary").json()
        outbound = next(f for f in it["flights"] if f["direction"] == "outbound")
        assert outbound["arrival_time"] == "2027-06-14T19:00:00" and outbound["status"] == "delayed"
        arrival = next(t for t in it["transfers"] if t["direction"] == "arrival")
        assert arrival["pickup_time"] == "2027-06-14T19:30:00"

    def test_explicit_event_and_history(self, client, trip_id):
        resp = client.post(f"/trips/{trip_id}/disruptions", json={
            "type": "WEATHER_STORM", "target_node": "weather_day_3", "new_facts": {"condition": "thunderstorm"}})
        assert resp.json()["status"] == "replanned"
        client.post(f"/trips/{trip_id}/disruptions", json={
            "type": "WEATHER_STORM", "target_node": "weather_day_3", "new_facts": {"condition": "thunderstorm"}})
        history = client.get(f"/trips/{trip_id}/disruptions").json()
        assert [h["status"] for h in history] == ["replanned", "duplicate"]
        assert history[0]["outcome"]["replan"]["stale"][0] == "day_3_plan"

    def test_baseline_strategy(self, client, trip_id):
        body = client.post(f"/trips/{trip_id}/disruptions",
                           json={"scenario": "delay_5h", "strategy": "full"}).json()
        assert len(body["regenerated"]) == 15

    def test_no_effect(self, client, trip_id):
        before = versions(client, trip_id)
        body = client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_10m"}).json()
        assert body["status"] == "no_effect" and body["regenerated"] == []
        after = versions(client, trip_id)
        assert {nid for nid in before if after[nid] != before[nid]} == {"flight_outbound"}

    def test_validation_errors(self, client, trip_id):
        assert client.post(f"/trips/{trip_id}/disruptions", json={}).status_code == 422
        assert client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "meteor"}).status_code == 422
        bad = {"type": "FLIGHT_DELAY", "target_node": "flight_outbound", "new_facts": {"delay_minutes": -1}}
        assert client.post(f"/trips/{trip_id}/disruptions", json=bad).status_code == 422
        invalid_target = client.post(f"/trips/{trip_id}/disruptions", json={
            "type": "FLIGHT_DELAY", "target_node": "hotel_stay", "new_facts": {"delay_minutes": 60}}).json()
        assert invalid_target["status"] == "invalid"

    def test_unknown_trip(self, client):
        assert client.post("/trips/nope/disruptions", json={"scenario": "delay_5h"}).status_code == 404
        assert client.get("/trips/nope/disruptions").status_code == 404

    def test_presets(self, client):
        presets = client.get("/disruptions/presets").json()
        names = {p["name"] for p in presets}
        assert {"delay_5h", "cancel_outbound", "hotel_overbooked", "storm_day_3", "price_spike_hotel"} <= names

    def test_poll_outside_forecast_window_finds_nothing(self, client, trip_id):
        assert client.post(f"/trips/{trip_id}/monitor/poll").json() == []

    def test_compare_baseline_metrics(self, client, trip_id):
        before = versions(client, trip_id)
        body = client.post(f"/trips/{trip_id}/disruptions",
                           json={"scenario": "delay_5h", "compare_baseline": True}).json()
        metrics = body["metrics"]
        assert metrics["oracle"]["affected"] == ["airport_transfer_in", "day_1_plan", "budget_check"]
        assert metrics["replan"]["precision"] == metrics["replan"]["recall"] == 1.0
        assert metrics["b1"]["regenerated_count"] == 15 and metrics["b1"]["precision"] == 0.2
        # B1 ran on a copy: only the real replan's nodes moved.
        after = versions(client, trip_id)
        assert {n for n in before if after[n] != before[n]} == {
            "flight_outbound", "airport_transfer_in", "day_1_plan", "budget_check"}
        history = client.get(f"/trips/{trip_id}/disruptions").json()
        assert history[-1]["outcome"]["metrics"]["b1"]["regenerated_count"] == 15

    def test_metrics_off_by_default(self, client, trip_id):
        assert client.post(f"/trips/{trip_id}/disruptions", json={"scenario": "delay_5h"}).json()["metrics"] is None
