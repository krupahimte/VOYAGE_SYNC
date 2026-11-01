"""
Persistence of handled disruptions (event + re-plan result per trip).
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.db.models import DisruptionRecord


def record_disruption(session: Session, *, trip_id: str, strategy: str, outcome: dict[str, Any]) -> DisruptionRecord:
    event = outcome["event"]
    record = DisruptionRecord(
        trip_id=trip_id,
        event_id=event["event_id"],
        event_type=event["type"],
        target_node=event["target_node"],
        status=outcome["status"],
        strategy=strategy,
        outcome_json=json.dumps(outcome),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_disruptions(trip_id: str, session: Session) -> list[DisruptionRecord]:
    return list(session.exec(
        select(DisruptionRecord).where(DisruptionRecord.trip_id == trip_id).order_by(DisruptionRecord.id)
    ).all())
