"""
Persistence of Risk Gate actions (``gate_actions``) and approval decisions
(``approval_history``, read back into the user's memory).

Like graph_repo, the gate works on an in-memory ``ActionLedger``; this module
loads it per trip and saves it back. Leaving "pending" is guarded by
``claim``: a conditional UPDATE … WHERE status='pending', so two approvals of
the same action can never both proceed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import update
from sqlmodel import Session, select

from app.core.risk_gate import Action, ActionLedger, ActionStatus
from app.db.models import ApprovalRecord, GateActionRecord


def _to_action(rec: GateActionRecord) -> Action:
    action = Action.model_validate(rec.get_data())
    action.status = ActionStatus(rec.status)
    return action


def load_ledger(trip_id: str, session: Session) -> ActionLedger:
    rows = session.exec(select(GateActionRecord).where(GateActionRecord.trip_id == trip_id)
                        .order_by(GateActionRecord.created_at)).all()
    return ActionLedger(trip_id, [_to_action(r) for r in rows])


def save_ledger(ledger: ActionLedger, session: Session) -> None:
    """Upsert every action of the ledger. Commits."""
    now = datetime.now(timezone.utc)
    for action in ledger.list():
        data = json.dumps(action.model_dump(mode="json"))
        rec = session.get(GateActionRecord, action.action_id)
        if rec is None:
            rec = GateActionRecord(action_id=action.action_id, trip_id=action.trip_id or ledger.trip_id,
                                   node_id=action.node_id, tier=action.tier.value, kind=action.kind.value,
                                   status=action.status.value, data_json=data, created_at=action.created_at)
        else:
            rec.status, rec.tier, rec.data_json, rec.updated_at = action.status.value, action.tier.value, data, now
        session.add(rec)
    session.commit()


def find_action(action_id: str, session: Session) -> GateActionRecord | None:
    return session.get(GateActionRecord, action_id)


def list_actions(session: Session, *, trip_id: str | None = None, status: str | None = None,
                 tier: str | None = None) -> list[Action]:
    query = select(GateActionRecord)
    if trip_id:
        query = query.where(GateActionRecord.trip_id == trip_id)
    if status:
        query = query.where(GateActionRecord.status == status)
    if tier:
        query = query.where(GateActionRecord.tier == tier)
    return [_to_action(r) for r in session.exec(query.order_by(GateActionRecord.created_at)).all()]


def claim(action_id: str, session: Session) -> bool:
    """Atomically move a pending action to "approving"; False if it was not pending."""
    result = session.execute(update(GateActionRecord)
                             .where(GateActionRecord.action_id == action_id,
                                    GateActionRecord.status == ActionStatus.PENDING.value)
                             .values(status=ActionStatus.APPROVING.value,
                                     updated_at=datetime.now(timezone.utc)))
    session.commit()
    return result.rowcount == 1


def record_decision(session: Session, *, user_id: str, action: Action) -> ApprovalRecord:
    record = ApprovalRecord(user_id=user_id, trip_id=action.trip_id, node_id=action.node_id,
                            action=action.kind.value, decision=action.decision or "", amount=action.amount)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
