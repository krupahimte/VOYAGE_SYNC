"""
Risk Gate endpoints (plan §8.1).

GET  /approvals                 — T2 approval cards (default: pending); overdue ones are reminded, never executed
POST /approvals/{action_id}     — approve / reject a pending T2 action
GET  /trips/{trip_id}/actions   — every gate action of a trip (T0 log, T1 notifications, T2 approvals)
POST /actions/{action_id}/undo  — undo a T1 action

Approval is the only path to execution: the action is claimed with a
conditional update (so a double approval gets 409), the gate re-checks that
the node still holds the approved offer at the approved price, and only then
issues the single-use grant the booking provider requires.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.live import bus, node_states
from app.api.trips import _load_trip, _node_summary, get_orchestrator
from app.core.orchestrator import Orchestrator, check_graph_contracts
from app.core.risk_gate import ActionStatus, GateConflict, GateExecutionError, RiskGate, RiskPolicy, RiskTier
from app.db import approval_repo, graph_repo
from app.db.database import get_session

router = APIRouter(tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    user_id: str | None = Field(default=None, max_length=64, description="Defaults to the trip's user.")


class ActionOutcomeResponse(BaseModel):
    action: dict[str, Any]
    node: dict[str, Any]
    contracts_total: int
    contracts_holding: int
    broken_edges: list[list[str]] = Field(default_factory=list)


def _outcome(store, action) -> ActionOutcomeResponse:
    checks = check_graph_contracts(store)
    return ActionOutcomeResponse(
        action=action.to_dict(),
        node=_node_summary(store.get_node(action.node_id)).model_dump(mode="json"),
        contracts_total=len(checks),
        contracts_holding=sum(c.holds for c in checks),
        broken_edges=[[c.source_id, c.destination_id] for c in checks if not c.holds],
    )


def _remind_overdue(session: Session, trip_ids: set[str]) -> None:
    """Approval timeout: hold the offer and remind the user. Never executes."""
    gate = RiskGate(RiskPolicy.from_settings())
    for trip_id in sorted(trip_ids):
        ledger = approval_repo.load_ledger(trip_id, session)
        reminded = gate.sweep(ledger)
        if reminded:
            approval_repo.save_ledger(ledger, session)
            for action in reminded:
                bus.publish(trip_id, "approval_reminder", action.to_dict())


@router.get("/approvals", summary="T2 approval cards (default: pending)")
def list_approvals(
    trip_id: str | None = None,
    status_filter: Literal["pending", "executed", "rejected", "superseded", "all"] = Query("pending", alias="status"),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    pending = approval_repo.list_actions(session, trip_id=trip_id, status="pending", tier=RiskTier.T2.value)
    overdue = {a.trip_id for a in pending if a.remind_at and _aware(a.remind_at) <= now}
    if overdue:
        _remind_overdue(session, overdue)
    wanted = None if status_filter == "all" else status_filter
    return [a.to_dict() for a in approval_repo.list_actions(session, trip_id=trip_id, status=wanted,
                                                              tier=RiskTier.T2.value)]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/approvals/{action_id}", response_model=ActionOutcomeResponse,
             summary="Approve or reject a pending T2 action")
async def decide(
    action_id: str,
    body: ApprovalDecisionRequest,
    session: Session = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ActionOutcomeResponse:
    record = approval_repo.find_action(action_id, session)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Action '{action_id}' not found.")
    trip, store = _load_trip(record.trip_id, session)
    ledger = approval_repo.load_ledger(trip.trip_id, session)
    action = ledger.get(action_id)
    if action.tier != RiskTier.T2:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{action_id} is {action.tier.value}; only T2 needs approval.")
    if action.status != ActionStatus.PENDING or not approval_repo.claim(action_id, session):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{action_id} is already {record.status}.")

    user_id = body.user_id or trip.user_id
    error: HTTPException | None = None
    try:
        if body.decision == "approve":
            await orchestrator.approve_action(store, ledger, action_id, decided_by=user_id)
        else:
            orchestrator.reject_action(store, ledger, action_id, decided_by=user_id)
    except GateConflict as exc:
        error = HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except GateExecutionError as exc:
        error = HTTPException(status.HTTP_502_BAD_GATEWAY, f"{exc}; nothing was booked, the approval is still pending.")
    finally:
        approval_repo.save_ledger(ledger, session)     # always releases the claim
        graph_repo.save_graph(store, session)

    if error is not None:
        if action.status == ActionStatus.SUPERSEDED:
            bus.publish(trip.trip_id, "approval_decided", action.to_dict())
        raise error
    approval_repo.record_decision(session, user_id=user_id, action=action)
    bus.publish(trip.trip_id, "approval_decided", action.to_dict())
    bus.publish(trip.trip_id, "node_status", {"nodes": node_states(store)})
    return _outcome(store, action)


@router.get("/trips/{trip_id}/actions", summary="All Risk Gate actions of a trip")
def trip_actions(trip_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    _load_trip(trip_id, session)
    return [a.to_dict() for a in approval_repo.list_actions(session, trip_id=trip_id)]


@router.post("/actions/{action_id}/undo", response_model=ActionOutcomeResponse, summary="Undo a T1 action")
def undo_action(
    action_id: str,
    session: Session = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ActionOutcomeResponse:
    record = approval_repo.find_action(action_id, session)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Action '{action_id}' not found.")
    trip, store = _load_trip(record.trip_id, session)
    ledger = approval_repo.load_ledger(trip.trip_id, session)
    try:
        action = orchestrator.gate.undo(store, ledger, action_id)
    except GateConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    graph_repo.save_graph(store, session)
    approval_repo.save_ledger(ledger, session)
    bus.publish(trip.trip_id, "undo", action.to_dict())
    bus.publish(trip.trip_id, "node_status", {"nodes": node_states(store)})
    return _outcome(store, action)
