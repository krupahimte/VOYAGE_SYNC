"""
Comprehensive tests for the Task Dependency Graph engine (Phase 1).

Coverage:
  - GraphStore: node/edge CRUD, duplicate IDs, missing references,
    self-edges, cycles, topological ordering, multiple parents/children,
    node history, serialization.
  - Contracts: each template (success + failure + invalid input).
  - API endpoints: POST /graphs, GET /graphs/{id}, POST nodes/edges,
    GET validate, GET history, HTTP error codes.
  - Persistence: graph state survives session reload from SQLite.

Isolation strategy
------------------
Bare ``sqlite://`` (":memory:") creates a *new* database for every
connection, so ``create_all`` and subsequent ``Session`` objects never
see the same tables.  Instead each test fixture creates a **named
shared-cache in-memory database**::

    sqlite:///file:<unique-name>?mode=memory&cache=shared&uri=true

All connections that use the same name share the same in-memory store,
so tables created by ``create_all`` are immediately visible to sessions
opened inside the endpoint handlers.  A unique name (via ``uuid4``) is
generated per test, giving full isolation between tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

# ── App and DB internals ──────────────────────────────────────────────────────
import app.db.models  # noqa: F401 — register table metadata before create_all
import app.db.database as db_module
from app.main import app as fa_app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(name="engine")
def engine_fixture():
    """
    Named shared-cache in-memory SQLite engine, unique per test.

    A unique database name ensures each test is fully isolated while
    allowing multiple connections (from create_all AND from the endpoint
    handler) to share the same in-memory store.
    """
    import app.db.models  # noqa: F401 — ensure metadata is populated
    db_name = f"testdb_{uuid.uuid4().hex}"
    url = f"sqlite:///file:{db_name}?mode=memory&cache=shared&uri=true"
    test_engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    return test_engine


@pytest.fixture(name="session")
def session_fixture(engine):
    """Direct session on the test engine (used by pure-store and repo tests)."""
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(engine):
    """
    TestClient backed by the test engine.

    We patch the module-level ``engine`` in ``app.db.database`` so that
    ``get_session`` (which does ``Session(engine)``) uses our test engine.
    This is necessary because the FastAPI dependency-override mechanism does
    not fire reliably when the app object is shared across the test process.
    """
    with patch.object(db_module, "engine", engine):
        client = TestClient(fa_app)
        yield client


# ── Helpers ───────────────────────────────────────────────────────────────────

def create_graph(client: TestClient, graph_id: str | None = None) -> dict:
    body = {"graph_id": graph_id} if graph_id else {}
    resp = client.post("/graphs", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def add_node(
    client: TestClient,
    graph_id: str,
    *,
    node_id: str | None = None,
    node_type: str = "FLIGHT",
    agent: str = "flight_agent",
    inputs: dict | None = None,
    risk_tier: str | None = None,
) -> dict:
    body: dict = {"type": node_type, "agent": agent, "inputs": inputs or {}}
    if node_id:
        body["id"] = node_id
    if risk_tier:
        body["risk_tier"] = risk_tier
    resp = client.post(f"/graphs/{graph_id}/nodes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def add_edge(
    client: TestClient,
    graph_id: str,
    source: str,
    dest: str,
    contract: str = "FLIGHT_BEFORE_CHECKIN",
    config: dict | None = None,
    description: str = "",
) -> dict:
    body = {
        "source_id": source,
        "destination_id": dest,
        "contract": contract,
        "contract_config": config or {},
        "description": description,
    }
    resp = client.post(f"/graphs/{graph_id}/edges", json=body)
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
# 1. GraphStore unit tests (pure Python — no DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGraphStore:
    """Tests for the in-memory GraphStore logic."""

    def _make_store(self):
        from app.core.graph import GraphStore
        return GraphStore("test-graph")

    def _node(self, node_id: str = "n1", agent: str = "agent"):
        from app.core.models import NodeType, TaskNode
        return TaskNode(id=node_id, type=NodeType.FLIGHT, agent=agent)

    def _edge(self, src: str, dst: str, contract: str = "FLIGHT_BEFORE_CHECKIN"):
        from app.core.models import Edge
        return Edge(source_id=src, destination_id=dst, contract=contract)

    def test_add_node_success(self):
        store = self._make_store()
        node = store.add_node(self._node("n1"))
        assert node.id == "n1"
        assert store.get_node("n1").id == "n1"

    def test_duplicate_node_id_raises(self):
        from app.core.graph import DuplicateNodeError
        store = self._make_store()
        store.add_node(self._node("n1"))
        with pytest.raises(DuplicateNodeError):
            store.add_node(self._node("n1"))

    def test_get_missing_node_raises(self):
        from app.core.graph import NodeNotFoundError
        store = self._make_store()
        with pytest.raises(NodeNotFoundError):
            store.get_node("nonexistent")

    def test_self_edge_raises(self):
        from app.core.graph import SelfEdgeError
        store = self._make_store()
        store.add_node(self._node("n1"))
        with pytest.raises(SelfEdgeError):
            store.add_edge(self._edge("n1", "n1"))

    def test_missing_source_raises(self):
        from app.core.graph import MissingNodeError
        store = self._make_store()
        store.add_node(self._node("n2"))
        with pytest.raises(MissingNodeError):
            store.add_edge(self._edge("n1", "n2"))  # n1 missing

    def test_missing_dest_raises(self):
        from app.core.graph import MissingNodeError
        store = self._make_store()
        store.add_node(self._node("n1"))
        with pytest.raises(MissingNodeError):
            store.add_edge(self._edge("n1", "n2"))  # n2 missing

    def test_duplicate_edge_raises(self):
        from app.core.graph import DuplicateEdgeError
        store = self._make_store()
        store.add_node(self._node("n1"))
        store.add_node(self._node("n2"))
        store.add_edge(self._edge("n1", "n2"))
        with pytest.raises(DuplicateEdgeError):
            store.add_edge(self._edge("n1", "n2"))

    def test_cycle_two_nodes(self):
        from app.core.graph import CycleError
        store = self._make_store()
        store.add_node(self._node("n1"))
        store.add_node(self._node("n2"))
        store.add_edge(self._edge("n1", "n2"))
        with pytest.raises(CycleError):
            store.add_edge(self._edge("n2", "n1"))

    def test_cycle_three_nodes(self):
        from app.core.graph import CycleError
        store = self._make_store()
        for nid in ["n1", "n2", "n3"]:
            store.add_node(self._node(nid))
        store.add_edge(self._edge("n1", "n2"))
        store.add_edge(self._edge("n2", "n3"))
        with pytest.raises(CycleError):
            store.add_edge(self._edge("n3", "n1"))

    def test_cycle_rolled_back(self):
        """After a CycleError, the graph must remain consistent."""
        from app.core.graph import CycleError
        store = self._make_store()
        store.add_node(self._node("n1"))
        store.add_node(self._node("n2"))
        store.add_edge(self._edge("n1", "n2"))
        with pytest.raises(CycleError):
            store.add_edge(self._edge("n2", "n1"))
        # Original edge still present; no partial cycle edge
        assert len(store.list_edges()) == 1

    def test_topological_order_chain(self):
        """n1 → n2 → n3 must produce [n1, n2, n3]."""
        store = self._make_store()
        for nid in ["n1", "n2", "n3"]:
            store.add_node(self._node(nid))
        store.add_edge(self._edge("n1", "n2"))
        store.add_edge(self._edge("n2", "n3"))
        order = store.validate()
        assert order.index("n1") < order.index("n2") < order.index("n3")

    def test_topological_order_multiple_parents(self):
        """n1 → n3, n2 → n3: both n1 and n2 must precede n3."""
        store = self._make_store()
        for nid in ["n1", "n2", "n3"]:
            store.add_node(self._node(nid))
        store.add_edge(self._edge("n1", "n3"))
        store.add_edge(self._edge("n2", "n3"))
        order = store.validate()
        assert order.index("n1") < order.index("n3")
        assert order.index("n2") < order.index("n3")

    def test_topological_order_multiple_children(self):
        """n1 → n2, n1 → n3: n1 must precede both n2 and n3."""
        store = self._make_store()
        for nid in ["n1", "n2", "n3"]:
            store.add_node(self._node(nid))
        store.add_edge(self._edge("n1", "n2"))
        store.add_edge(self._edge("n1", "n3"))
        order = store.validate()
        assert order.index("n1") < order.index("n2")
        assert order.index("n1") < order.index("n3")

    def test_isolated_node_included(self):
        """A node with no edges must still appear in topological order."""
        store = self._make_store()
        store.add_node(self._node("isolated"))
        order = store.validate()
        assert "isolated" in order

    def test_empty_graph_validates(self):
        store = self._make_store()
        order = store.validate()
        assert order == []

    def test_node_history_on_create(self):
        store = self._make_store()
        node = store.add_node(self._node("n1"))
        history = store.get_history("n1")
        assert len(history) == 1
        assert history[0].version == 1
        assert history[0].reason == "created"

    def test_node_history_on_update(self):
        from app.core.models import NodeStatus
        store = self._make_store()
        store.add_node(self._node("n1"))
        store.update_node_status("n1", NodeStatus.DONE, reason="completed")
        history = store.get_history("n1")
        assert len(history) == 2
        assert history[1].version == 2
        assert history[1].status == NodeStatus.DONE
        assert history[1].reason == "completed"

    def test_history_not_overwritten(self):
        """Each update appends; previous entries remain unchanged."""
        from app.core.models import NodeStatus
        store = self._make_store()
        store.add_node(self._node("n1"))
        store.update_node_status("n1", NodeStatus.RUNNING, reason="started")
        store.update_node_status("n1", NodeStatus.DONE, reason="finished")
        history = store.get_history("n1")
        assert len(history) == 3
        statuses = [h.status for h in history]
        assert statuses == [NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.DONE]

    def test_serialization_round_trip(self):
        """to_dict() must produce a JSON-compatible structure."""
        import json
        store = self._make_store()
        store.add_node(self._node("n1"))
        store.add_node(self._node("n2"))
        store.add_edge(self._edge("n1", "n2"))
        data = store.to_dict()
        # Must be JSON-serialisable
        json.dumps(data)
        assert data["graph_id"] == "test-graph"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Contract tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestContracts:
    def test_flight_before_checkin_holds(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("FLIGHT_BEFORE_CHECKIN", {
            "flight_arrival_iso": "2025-06-10T14:00:00+00:00",
            "checkin_cutoff_iso": "2025-06-10T22:00:00+00:00",
        })
        assert result.holds is True
        assert result.contract == "FLIGHT_BEFORE_CHECKIN"

    def test_flight_before_checkin_fails(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("FLIGHT_BEFORE_CHECKIN", {
            "flight_arrival_iso": "2025-06-10T23:00:00+00:00",
            "checkin_cutoff_iso": "2025-06-10T22:00:00+00:00",
        })
        assert result.holds is False
        assert "after" in result.explanation.lower()

    def test_flight_before_checkin_with_buffer(self):
        from app.core.contracts import evaluate_contract
        # Arrives 30 min before cutoff; buffer=60 → should fail
        result = evaluate_contract("FLIGHT_BEFORE_CHECKIN", {
            "flight_arrival_iso": "2025-06-10T21:30:00+00:00",
            "checkin_cutoff_iso": "2025-06-10T22:00:00+00:00",
            "buffer_minutes": 60,
        })
        assert result.holds is False

    def test_flight_before_checkin_missing_input(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("FLIGHT_BEFORE_CHECKIN", {
            "flight_arrival_iso": "2025-06-10T14:00:00+00:00",
            # missing checkin_cutoff_iso
        })
        assert result.holds is False
        assert "Missing" in result.explanation

    def test_flight_before_checkin_invalid_date(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("FLIGHT_BEFORE_CHECKIN", {
            "flight_arrival_iso": "not-a-date",
            "checkin_cutoff_iso": "2025-06-10T22:00:00+00:00",
        })
        assert result.holds is False

    def test_transfer_after_flight_holds(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("TRANSFER_AFTER_FLIGHT", {
            "flight_arrival_iso": "2025-06-10T14:00:00+00:00",
            "transfer_pickup_iso": "2025-06-10T15:30:00+00:00",
            "min_gap_minutes": 60,
        })
        assert result.holds is True

    def test_transfer_after_flight_fails(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("TRANSFER_AFTER_FLIGHT", {
            "flight_arrival_iso": "2025-06-10T14:00:00+00:00",
            "transfer_pickup_iso": "2025-06-10T14:20:00+00:00",
            "min_gap_minutes": 60,
        })
        assert result.holds is False
        assert "below" in result.explanation.lower()

    def test_transfer_missing_input(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("TRANSFER_AFTER_FLIGHT", {
            "flight_arrival_iso": "2025-06-10T14:00:00+00:00",
            # missing transfer_pickup_iso and min_gap_minutes
        })
        assert result.holds is False

    def test_day_plan_feasibility_holds(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("DAY_PLAN_FEASIBILITY", {
            "available_minutes": 480,
            "planned_minutes": 360,
            "buffer_minutes": 60,
        })
        assert result.holds is True
        assert "spare" in result.explanation

    def test_day_plan_feasibility_fails(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("DAY_PLAN_FEASIBILITY", {
            "available_minutes": 300,
            "planned_minutes": 360,
        })
        assert result.holds is False
        assert "shortfall" in result.explanation

    def test_day_plan_missing_input(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("DAY_PLAN_FEASIBILITY", {
            "available_minutes": 480,
            # missing planned_minutes
        })
        assert result.holds is False

    def test_budget_within_limit_holds(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("BUDGET_WITHIN_LIMIT", {
            "total_cost": 2500.0,
            "budget_limit": 3000.0,
        })
        assert result.holds is True
        assert "remaining" in result.explanation

    def test_budget_within_limit_fails(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("BUDGET_WITHIN_LIMIT", {
            "total_cost": 3500.0,
            "budget_limit": 3000.0,
            "currency": "EUR",
        })
        assert result.holds is False
        assert "EUR" in result.explanation

    def test_budget_missing_input(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("BUDGET_WITHIN_LIMIT", {
            "total_cost": 100.0,
            # missing budget_limit
        })
        assert result.holds is False

    def test_budget_negative_limit_fails(self):
        from app.core.contracts import evaluate_contract
        result = evaluate_contract("BUDGET_WITHIN_LIMIT", {
            "total_cost": 100.0,
            "budget_limit": -500.0,
        })
        assert result.holds is False

    def test_unknown_contract_raises(self):
        from app.core.contracts import evaluate_contract
        with pytest.raises(ValueError, match="Unknown contract"):
            evaluate_contract("NONEXISTENT_CONTRACT", {})


# ═══════════════════════════════════════════════════════════════════════════════
# 3. API integration tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGraphsAPI:

    # ── Graph creation ─────────────────────────────────────────────────────────

    def test_create_graph_auto_id(self, client: TestClient):
        resp = client.post("/graphs", json={})
        assert resp.status_code == 201
        data = resp.json()
        assert "graph_id" in data
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_create_graph_explicit_id(self, client: TestClient):
        resp = client.post("/graphs", json={"graph_id": "my-graph"})
        assert resp.status_code == 201
        assert resp.json()["graph_id"] == "my-graph"

    def test_create_graph_duplicate_id(self, client: TestClient):
        client.post("/graphs", json={"graph_id": "dup"})
        resp = client.post("/graphs", json={"graph_id": "dup"})
        assert resp.status_code == 409

    # ── Graph retrieval ────────────────────────────────────────────────────────

    def test_get_graph(self, client: TestClient):
        create_graph(client, "g1")
        resp = client.get("/graphs/g1")
        assert resp.status_code == 200
        assert resp.json()["graph_id"] == "g1"

    def test_get_nonexistent_graph(self, client: TestClient):
        resp = client.get("/graphs/does-not-exist")
        assert resp.status_code == 404

    # ── Node creation ──────────────────────────────────────────────────────────

    def test_add_node_success(self, client: TestClient):
        create_graph(client, "g1")
        data = add_node(client, "g1", node_id="n1", node_type="FLIGHT")
        assert data["id"] == "n1"
        assert data["type"] == "FLIGHT"
        assert data["status"] == "PENDING"
        assert data["version"] == 1

    def test_add_node_auto_id(self, client: TestClient):
        create_graph(client, "g1")
        resp = client.post("/graphs/g1/nodes", json={"type": "STAY", "agent": "stay_agent"})
        assert resp.status_code == 201
        assert resp.json()["id"] != ""

    def test_add_node_duplicate_id(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        resp = client.post(
            "/graphs/g1/nodes",
            json={"id": "n1", "type": "FLIGHT", "agent": "flight_agent"},
        )
        assert resp.status_code == 409

    def test_add_node_to_missing_graph(self, client: TestClient):
        resp = client.post(
            "/graphs/nope/nodes",
            json={"type": "FLIGHT", "agent": "flight_agent"},
        )
        assert resp.status_code == 404

    def test_add_node_invalid_type(self, client: TestClient):
        create_graph(client, "g1")
        resp = client.post(
            "/graphs/g1/nodes",
            json={"type": "INVALID_TYPE", "agent": "agent"},
        )
        assert resp.status_code == 422

    def test_add_node_with_risk_tier(self, client: TestClient):
        create_graph(client, "g1")
        data = add_node(client, "g1", node_id="n1", risk_tier="T0")
        assert data["risk_tier"] == "T0"

    # ── Edge creation ──────────────────────────────────────────────────────────

    def test_add_edge_success(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", node_type="STAY", agent="stay_agent")
        resp = add_edge(client, "g1", "n1", "n2")
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_id"] == "n1"
        assert data["destination_id"] == "n2"

    def test_add_edge_self_edge(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        resp = add_edge(client, "g1", "n1", "n1")
        assert resp.status_code == 422

    def test_add_edge_missing_source(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n2", agent="a")
        resp = add_edge(client, "g1", "nonexistent", "n2")
        assert resp.status_code == 422

    def test_add_edge_missing_dest(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        resp = add_edge(client, "g1", "n1", "nonexistent")
        assert resp.status_code == 422

    def test_add_edge_duplicate(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", agent="a")
        add_edge(client, "g1", "n1", "n2")
        resp = add_edge(client, "g1", "n1", "n2")
        assert resp.status_code == 409

    def test_add_edge_cycle(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", agent="a")
        add_edge(client, "g1", "n1", "n2")
        resp = add_edge(client, "g1", "n2", "n1")
        assert resp.status_code == 422

    def test_add_edge_unknown_contract(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", agent="a")
        resp = add_edge(client, "g1", "n1", "n2", contract="FAKE_CONTRACT")
        assert resp.status_code == 422

    # ── Validation ─────────────────────────────────────────────────────────────

    def test_validate_empty_graph(self, client: TestClient):
        create_graph(client, "g1")
        resp = client.get("/graphs/g1/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["topological_order"] == []

    def test_validate_linear_chain(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", agent="a")
        add_node(client, "g1", node_id="n3", node_type="STAY", agent="stay_agent")
        add_edge(client, "g1", "n1", "n2")
        add_edge(client, "g1", "n2", "n3")
        resp = client.get("/graphs/g1/validate")
        data = resp.json()
        assert data["valid"] is True
        order = data["topological_order"]
        assert order.index("n1") < order.index("n2") < order.index("n3")

    def test_validate_multiple_parents(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", agent="a")
        add_node(client, "g1", node_id="n3", node_type="STAY", agent="stay_agent")
        add_edge(client, "g1", "n1", "n3")
        add_edge(client, "g1", "n2", "n3",
                 contract="TRANSFER_AFTER_FLIGHT")
        resp = client.get("/graphs/g1/validate")
        data = resp.json()
        assert data["valid"] is True
        order = data["topological_order"]
        assert order.index("n1") < order.index("n3")
        assert order.index("n2") < order.index("n3")

    def test_validate_missing_graph(self, client: TestClient):
        resp = client.get("/graphs/nope/validate")
        assert resp.status_code == 404

    # ── History ────────────────────────────────────────────────────────────────

    def test_history_contains_creation_entry(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        resp = client.get("/graphs/g1/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "n1" in data["history"]
        assert data["history"]["n1"][0]["reason"] == "created"
        assert data["history"]["n1"][0]["version"] == 1

    def test_history_missing_graph(self, client: TestClient):
        resp = client.get("/graphs/nope/history")
        assert resp.status_code == 404

    # ── Full graph retrieval with nodes and edges ──────────────────────────────

    def test_get_graph_shows_nodes_and_edges(self, client: TestClient):
        create_graph(client, "g1")
        add_node(client, "g1", node_id="n1")
        add_node(client, "g1", node_id="n2", node_type="STAY", agent="stay_agent")
        add_edge(client, "g1", "n1", "n2")
        resp = client.get("/graphs/g1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Persistence tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    """Tests that graph state survives being reloaded from SQLite."""

    def test_nodes_persist_across_reload(self, session: Session):
        from app.db import graph_repo
        from app.core.models import NodeType, TaskNode
        graph_repo.create_graph("persist-test", session)
        node = TaskNode(id="n1", type=NodeType.FLIGHT, agent="flight_agent")
        graph_repo.persist_node("persist-test", node, session)
        session.commit()

        # Reload
        store = graph_repo.load_graph("persist-test", session)
        assert "n1" in [n.id for n in store.list_nodes()]

    def test_edges_persist_across_reload(self, session: Session):
        from app.db import graph_repo
        from app.core.models import Edge, NodeType, TaskNode
        graph_repo.create_graph("persist-test", session)
        for nid in ["n1", "n2"]:
            node = TaskNode(id=nid, type=NodeType.FLIGHT, agent="agent")
            graph_repo.persist_node("persist-test", node, session)
        graph_repo.persist_edge(
            "persist-test",
            Edge(source_id="n1", destination_id="n2", contract="FLIGHT_BEFORE_CHECKIN"),
            session,
        )
        session.commit()

        store = graph_repo.load_graph("persist-test", session)
        edges = store.list_edges()
        assert len(edges) == 1
        assert edges[0].source_id == "n1"

    def test_history_persists_across_reload(self, session: Session):
        from app.db import graph_repo
        from app.core.models import NodeHistoryEntry, NodeStatus, NodeType, TaskNode
        graph_repo.create_graph("persist-test", session)
        node = TaskNode(id="n1", type=NodeType.FLIGHT, agent="agent")
        graph_repo.persist_node("persist-test", node, session)
        entry = NodeHistoryEntry(
            node_id="n1",
            version=1,
            status=NodeStatus.PENDING,
            output=None,
            inputs={},
            reason="created",
        )
        graph_repo.persist_history("persist-test", entry, session)
        session.commit()

        history = graph_repo.get_graph_history("persist-test", session)
        assert "n1" in history
        assert history["n1"][0].reason == "created"

    def test_graph_not_found_raises_key_error(self, session: Session):
        from app.db import graph_repo
        with pytest.raises(KeyError):
            graph_repo.load_graph("does-not-exist", session)
