"""Health endpoint tests."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape():
    response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert "service" in body
    assert "version" in body


def test_health_service_name():
    response = client.get("/health")
    body = response.json()
    assert body["service"] == "VoyageSync"
