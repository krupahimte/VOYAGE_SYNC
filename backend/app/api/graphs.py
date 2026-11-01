"""
Graph API endpoints.

POST   /graphs                        — create a new graph
GET    /graphs/{graph_id}             — retrieve a graph with all nodes and edges
POST   /graphs/{graph_id}/nodes       — add a node to an existing graph
POST   /graphs/{graph_id}/edges       — add an edge to an existing graph
GET    /graphs/{graph_id}/validate    — validate graph and return topological order
GET    /graphs/{graph_id}/history     — retrieve full node version history

No LLM, external API, scheduler or agent execution happens here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.contracts import KNOWN_CONTRACTS
from app.core.graph import (
    CycleError,
    DuplicateEdgeError,
    DuplicateNodeError,
    MissingNodeError,
    NodeNotFoundError,
    SelfEdgeError,
    GraphStore,
)
from app.core.models import Edge, NodeStatus, TaskNode
from app.db.database import get_session
from app.db import graph_repo
from app.schemas.graph import (
    AddEdgeRequest,
    AddNodeRequest,
    CreateGraphRequest,
    EdgeResponse,
    GraphResponse,
    HistoryResponse,
    NodeHistoryEntryResponse,
    NodeResponse,
    ValidationResponse,
)

router = APIRouter(prefix="/graphs", tags=["graphs"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _node_to_response(node: TaskNode) -> NodeResponse:
    return NodeResponse(
        id=node.id,
        type=node.type,
        agent=node.agent,
        inputs=node.inputs,
        output=node.output,
        status=node.status,
        version=node.version,
        risk_tier=node.risk_tier,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _edge_to_response(edge: Edge) -> EdgeResponse:
    return EdgeResponse(
        source_id=edge.source_id,
        destination_id=edge.destination_id,
        contract=edge.contract,
        contract_config=edge.contract_config,
        description=edge.description,
    )


def _get_graph_or_404(graph_id: str, session: Session) -> GraphStore:
    try:
        return graph_repo.load_graph(graph_id, session)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Graph '{graph_id}' not found.",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=GraphResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new task dependency graph",
)
def create_graph(
    body: CreateGraphRequest,
    session: Session = Depends(get_session),
) -> GraphResponse:
    graph_id = body.graph_id or str(uuid.uuid4())

    if graph_repo.graph_exists(graph_id, session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Graph '{graph_id}' already exists.",
        )

    graph_repo.create_graph(graph_id, session)
    return GraphResponse(graph_id=graph_id, nodes=[], edges=[])


@router.get(
    "/{graph_id}",
    response_model=GraphResponse,
    summary="Retrieve a graph with all nodes and edges",
)
def get_graph(
    graph_id: str,
    session: Session = Depends(get_session),
) -> GraphResponse:
    store = _get_graph_or_404(graph_id, session)
    return GraphResponse(
        graph_id=graph_id,
        nodes=[_node_to_response(n) for n in store.list_nodes()],
        edges=[_edge_to_response(e) for e in store.list_edges()],
    )


@router.post(
    "/{graph_id}/nodes",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a node to an existing graph",
)
def add_node(
    graph_id: str,
    body: AddNodeRequest,
    session: Session = Depends(get_session),
) -> NodeResponse:
    store = _get_graph_or_404(graph_id, session)

    node = TaskNode(
        id=body.id or str(uuid.uuid4()),
        type=body.type,
        agent=body.agent,
        inputs=body.inputs,
        status=NodeStatus.PENDING,
        risk_tier=body.risk_tier,
    )

    try:
        node = store.add_node(node)
    except DuplicateNodeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    # Persist
    graph_repo.persist_node(graph_id, node, session)
    history_entries = store.get_history(node.id)
    if history_entries:
        graph_repo.persist_history(graph_id, history_entries[-1], session)
    session.commit()

    return _node_to_response(node)


@router.post(
    "/{graph_id}/edges",
    response_model=EdgeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a directed dependency edge to an existing graph",
)
def add_edge(
    graph_id: str,
    body: AddEdgeRequest,
    session: Session = Depends(get_session),
) -> EdgeResponse:
    # Validate contract identifier
    if body.contract not in KNOWN_CONTRACTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown contract '{body.contract}'. "
                   f"Known: {sorted(KNOWN_CONTRACTS)}",
        )

    store = _get_graph_or_404(graph_id, session)

    edge = Edge(
        source_id=body.source_id,
        destination_id=body.destination_id,
        contract=body.contract,
        contract_config=body.contract_config,
        description=body.description,
    )

    try:
        edge = store.add_edge(edge)
    except SelfEdgeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except MissingNodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except DuplicateEdgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except CycleError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    graph_repo.persist_edge(graph_id, edge, session)
    session.commit()

    return _edge_to_response(edge)


@router.get(
    "/{graph_id}/validate",
    response_model=ValidationResponse,
    summary="Validate graph structure and return topological order",
)
def validate_graph(
    graph_id: str,
    session: Session = Depends(get_session),
) -> ValidationResponse:
    store = _get_graph_or_404(graph_id, session)

    try:
        topo_order = store.validate()
        return ValidationResponse(
            graph_id=graph_id,
            valid=True,
            topological_order=topo_order,
            message="Graph is valid and acyclic.",
        )
    except CycleError as exc:
        return ValidationResponse(
            graph_id=graph_id,
            valid=False,
            topological_order=[],
            message=str(exc),
        )


@router.get(
    "/{graph_id}/history",
    response_model=HistoryResponse,
    summary="Retrieve the full node version history for a graph",
)
def get_history(
    graph_id: str,
    session: Session = Depends(get_session),
) -> HistoryResponse:
    _get_graph_or_404(graph_id, session)  # 404 guard
    raw = graph_repo.get_graph_history(graph_id, session)
    history: dict[str, list[NodeHistoryEntryResponse]] = {
        node_id: [
            NodeHistoryEntryResponse(
                node_id=e.node_id,
                version=e.version,
                status=e.status,
                output=e.output,
                inputs=e.inputs,
                reason=e.reason,
                timestamp=e.timestamp,
            )
            for e in entries
        ]
        for node_id, entries in raw.items()
    }
    return HistoryResponse(graph_id=graph_id, history=history)
