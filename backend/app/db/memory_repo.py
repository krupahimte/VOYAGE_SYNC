"""
User profile & memory repository (architecture slide: "User Profile & Memory").

Memory = saved preferences + past trips + approval history. The Orchestrator
reads it as context through ``get_user_context``; it is updated after every
planned trip (``record_trip``) and every approval decision (Phase 6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.db.models import ApprovalRecord, TripRecord, UserProfileRecord
from app.schemas.trip import TripGoal

# Goal fields remembered as profile preferences.
_LEARNED_PREFERENCE_KEYS = ("accommodation", "pace", "cabin")


def get_or_create_profile(user_id: str, session: Session) -> UserProfileRecord:
    profile = session.get(UserProfileRecord, user_id)
    if profile is None:
        profile = UserProfileRecord(user_id=user_id)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def get_user_context(user_id: str, session: Session, *, past_trip_limit: int = 5) -> dict[str, Any]:
    """Preferences, recent past trips and an approval-history summary for a user."""
    profile = session.get(UserProfileRecord, user_id)
    trips = session.exec(
        select(TripRecord)
        .where(TripRecord.user_id == user_id)
        .order_by(TripRecord.created_at.desc())
        .limit(past_trip_limit)
    ).all()
    approvals = session.exec(select(ApprovalRecord).where(ApprovalRecord.user_id == user_id)).all()

    past_trips = []
    for trip in trips:
        goal = trip.get_goal()
        past_trips.append({
            "trip_id": trip.trip_id,
            "destination": goal.get("destination"),
            "departure_date": goal.get("departure_date"),
            "return_date": goal.get("return_date"),
            "budget": goal.get("budget"),
            "currency": goal.get("currency"),
            "travel_style": goal.get("travel_style"),
        })
    return {
        "user_id": user_id,
        "preferences": profile.get_preferences() if profile else {},
        "past_trips": past_trips,
        "approval_history": {
            "approved": sum(1 for a in approvals if a.decision == "approved"),
            "rejected": sum(1 for a in approvals if a.decision == "rejected"),
        },
    }


def record_trip(
    *,
    session: Session,
    trip_id: str,
    user_id: str,
    graph_id: str,
    goal: TripGoal,
    summary: str,
    status: str,
    decomposition: dict[str, Any],
) -> TripRecord:
    """Store the trip and learn preferences from its goal. Commits."""
    record = TripRecord(
        trip_id=trip_id,
        user_id=user_id,
        graph_id=graph_id,
        goal_json=goal.model_dump_json(),
        status=status,
        summary=summary,
        decomposition_json=json.dumps(decomposition),
    )
    session.add(record)

    profile = get_or_create_profile(user_id, session)
    prefs = profile.get_preferences()
    prefs["travel_style"] = goal.travel_style
    if goal.interests:
        prefs["interests"] = list(goal.interests)
    for key in _LEARNED_PREFERENCE_KEYS:
        if goal.preferences.get(key):
            prefs[key] = goal.preferences[key]
    profile.set_preferences(prefs)
    profile.updated_at = datetime.now(timezone.utc)
    session.add(profile)

    session.commit()
    session.refresh(record)
    return record


def get_trip(trip_id: str, session: Session) -> TripRecord | None:
    return session.get(TripRecord, trip_id)
