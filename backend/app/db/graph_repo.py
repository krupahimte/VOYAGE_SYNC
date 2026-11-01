"""
Graph repository: translates between GraphStore (in-memory domain logic)
and the SQLite persistence layer.

All DB interactions go through this module; the API layer never touches
SQLModel records directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.core.graph import GraphStore
from app.core.models import (
    Edge,
    NodeHistoryEntry,
    NodeStatus,
    NodeType,
    RiskTier,
    TaskNode,
)
from app.db.models import (
    EdgeRecord,
    GraphRecord,
    NodeHistoryRecord,
    NodeRecord,
)


# ── Read helpers ──────────────────────────────────────────────────────────────

def _node_record_to_domain(rec: NodeRecord) -> TaskNode:
    return TaskNode(
        id=rec.node_id,
        type=NodeType(rec.node_type),
        agent=rec.agent,
        inputs=rec.get_inputs(),
        output=rec.get_output(),
        status=NodeStatus(rec.status),
        version=rec.version,
        risk_tier=RiskTier(rec.risk_tier) if rec.risk_tier else None,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


def _edge_record_to_domain(rec: EdgeRecord) -> Edge:
    return Edge(
        source_id=rec.source_id,
        destination_id=rec.destination_id,
        contract=rec.contract,
        contract_config=rec.get_contract_config(),
        description=rec.description,
    )


def _history_record_to_domain(rec: NodeHistoryRecord) -> NodeHistoryEntry:
    return NodeHistoryEntry(
        node_id=rec.node_id,
        version=rec.version,
        status=NodeStatus(rec.status),
        output=rec.get_output(),
        inputs=rec.get_inputs(),
        reason=rec.reason,
        timestamp=rec.timestamp,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def create_graph(graph_id: str, session: Session) -> GraphRecord:
    """Persist a new graph record. Caller must ensure ID is unique."""
    record = GraphRecord(id=graph_id)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def graph_exists(graph_id: str, session: Session) -> bool:
    return session.get(GraphRecord, graph_id) is not None


def load_graph(graph_id: str, session: Session) -> GraphStore:
    """
    Reconstruct a GraphStore from persisted records.
    Raises KeyError if the graph does not exist.
    """
    if not graph_exists(graph_id, session):
        raise KeyError(f"Graph '{graph_id}' not found.")

    store = GraphStore(graph_id=graph_id)

    # Load nodes (bypasses duplicate-check; we trust the DB is consistent)
    node_recs = session.exec(
        select(NodeRecord).where(NodeRecord.graph_id == graph_id)
    ).all()
    for rec in node_recs:
        node = _node_record_to_domain(rec)
        store._nodes[node.id] = node

    # Load history
    history_recs = session.exec(
        select(NodeHistoryRecord)
        .where(NodeHistoryRecord.graph_id == graph_id)
        .order_by(NodeHistoryRecord.id)
    ).all()
    for rec in history_recs:
        store._history[rec.node_id].append(_history_record_to_domain(rec))

    # Load edges
    edge_recs = session.exec(
        select(EdgeRecord).where(EdgeRecord.graph_id == graph_id)
    ).all()
    for rec in edge_recs:
        store._edges.append(_edge_record_to_domain(rec))

    return store


def persist_node(graph_id: str, node: TaskNode, session: Session) -> None:
    """Insert or replace the current node state and append a history record."""
    now = datetime.now(timezone.utc)

    # Upsert node record
    existing = session.get(NodeRecord, (graph_id, node.id))
    if existing:
        existing.status = node.status.value
        existing.version = node.version
        existing.risk_tier = node.risk_tier.value if node.risk_tier else None
        existing.set_inputs(node.inputs)
        existing.set_output(node.output)
        existing.updated_at = now
        session.add(existing)
    else:
        rec = NodeRecord(
            graph_id=graph_id,
            node_id=node.id,
            node_type=node.type.value,
            agent=node.agent,
            status=node.status.value,
            version=node.version,
            risk_tier=node.risk_tier.value if node.risk_tier else None,
            created_at=node.created_at,
            updated_at=now,
        )
        rec.set_inputs(node.inputs)
        rec.set_output(node.output)
        session.add(rec)


def persist_history(
    graph_id: str,
    entry: NodeHistoryEntry,
    session: Session,
) -> None:
    """Append a history record. Never updates existing rows."""
    rec = NodeHistoryRecord(
        graph_id=graph_id,
        node_id=entry.node_id,
        version=entry.version,
        status=entry.status.value,
        reason=entry.reason,
        timestamp=entry.timestamp,
    )
    rec.inputs_json = __import__("json").dumps(entry.inputs)
    rec.output_json = __import__("json").dumps(entry.output) if entry.output is not None else None
    session.add(rec)


def persist_edge(graph_id: str, edge: Edge, session: Session) -> None:
    """Persist an edge. Does NOT check for duplicates (caller's responsibility)."""
    rec = EdgeRecord(
        graph_id=graph_id,
        source_id=edge.source_id,
        destination_id=edge.destination_id,
        contract=edge.contract,
        description=edge.description,
    )
    rec.set_contract_config(edge.contract_config)
    session.add(rec)


def get_graph_history(graph_id: str, session: Session) -> dict[str, list[NodeHistoryEntry]]:
    """Fetch the full node history for a graph, grouped by node_id."""
    recs = session.exec(
        select(NodeHistoryRecord)
        .where(NodeHistoryRecord.graph_id == graph_id)
        .order_by(NodeHistoryRecord.id)
    ).all()
    result: dict[str, list[NodeHistoryEntry]] = {}
    for rec in recs:
        entry = _history_record_to_domain(rec)
        result.setdefault(rec.node_id, []).append(entry)
    return result


def save_graph(store: GraphStore, session: Session) -> None:
    """
    Persist a whole GraphStore snapshot: create the graph record if needed,
    upsert every node, add edges not yet stored, and append only the history
    entries that are newer than what is already stored. Commits.
    """
    graph_id = store.graph_id
    record = session.get(GraphRecord, graph_id)
    if record is None:
        session.add(GraphRecord(id=graph_id))
    else:
        record.updated_at = datetime.now(timezone.utc)
        session.add(record)

    for node in store.list_nodes():
        persist_node(graph_id, node, session)

    stored_edges = {
        (rec.source_id, rec.destination_id)
        for rec in session.exec(select(EdgeRecord).where(EdgeRecord.graph_id == graph_id)).all()
    }
    for edge in store.list_edges():
        if (edge.source_id, edge.destination_id) not in stored_edges:
            persist_edge(graph_id, edge, session)

    stored_counts: dict[str, int] = {}
    for node_id in session.exec(
        select(NodeHistoryRecord.node_id).where(NodeHistoryRecord.graph_id == graph_id)
    ).all():
        stored_counts[node_id] = stored_counts.get(node_id, 0) + 1
    for node_id, entries in store.get_all_history().items():
        for entry in entries[stored_counts.get(node_id, 0):]:
            persist_history(graph_id, entry, session)

    session.commit()
