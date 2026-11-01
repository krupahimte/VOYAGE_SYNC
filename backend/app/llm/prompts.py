"""
Prompts for LLM-assisted trip decomposition.

The LLM picks tasks from a fixed vocabulary and labels their role.
It never writes edges, contracts or code: the Orchestrator attaches those
from templates (app.core.templates).
"""

from __future__ import annotations

import json
from typing import Any

from app.schemas.trip import ProposedPlan, TripGoal


SYSTEM_PROMPT = """\
You are the planning component of VoyageSync, a travel planner.

Decompose the trip goal into tasks. Return ONLY a JSON object of this shape:

{
  "tasks": [
    {"task_id": "<snake_case id>", "task_type": "<TYPE>", "direction": "<role or null>", "day": <int or null>, "theme": "<short label>"}
  ],
  "summary": "<one sentence>"
}

Task types (use only these):
- FLIGHT    direction "outbound" or "return". Exactly one of each.
- STAY      accommodation for the whole trip. Exactly one.
- TRANSFER  direction "arrival" (airport -> hotel) or "departure" (hotel -> airport). At most one of each.
- DAY_PLAN  "day" = 1..N where N is the number of nights; day 1 is the arrival day. At most one per day. Give a short "theme".
- WEATHER   "day" = 1..N; forecast for that day's plan. At most one per day.
- BUDGET    exactly one; checks all costs against the budget.

Rules:
- Do not include dependencies, edges, contracts, prices or dates; the system adds them.
- task_id must be unique lowercase snake_case.
- Themes should reflect the traveller's interests, pace and past preferences.
- Output JSON only, no markdown.
"""


def build_user_prompt(goal: TripGoal, user_context: dict[str, Any] | None = None) -> str:
    """Build the user-message content from a validated TripGoal and user memory."""
    lines = [
        f"Plan a trip for {goal.travelers} traveller(s).",
        f"From: {goal.departure_city}",
        f"To: {goal.destination}",
        f"Departure: {goal.departure_date.isoformat()}",
        f"Return: {goal.return_date.isoformat()}",
        f"Nights: {goal.nights}",
        f"Budget: {goal.budget} {goal.currency}",
        f"Travel style: {goal.travel_style}",
    ]
    if goal.interests:
        lines.append(f"Interests: {', '.join(goal.interests)}")
    if goal.preferences:
        lines.append(f"Preferences: {json.dumps(goal.preferences, sort_keys=True)}")
    if goal.notes:
        lines.append(f"Notes: {goal.notes}")

    ctx = user_context or {}
    if ctx.get("preferences"):
        lines.append(f"Saved profile preferences: {json.dumps(ctx['preferences'], sort_keys=True)}")
    if ctx.get("past_trips"):
        past = "; ".join(
            f"{t['destination']} ({t['departure_date']}, {t.get('travel_style', '')})"
            for t in ctx["past_trips"]
        )
        lines.append(f"Past trips: {past}")
    approvals = ctx.get("approval_history") or {}
    if approvals.get("approved") or approvals.get("rejected"):
        lines.append(
            f"Approval history: {approvals.get('approved', 0)} approved, "
            f"{approvals.get('rejected', 0)} rejected."
        )

    # Machine-readable copy of the goal (also lets the offline MockLLM answer).
    lines.append(f"<trip_goal>{goal.model_dump_json()}</trip_goal>")
    return "\n".join(lines)


def build_retry_prompt(error: str) -> str:
    """Feedback message after an invalid reply."""
    return (
        "Your previous reply was rejected by the validator:\n"
        f"{error}\n"
        "Return a corrected JSON object only, following the schema and rules exactly."
    )


def get_response_schema() -> dict:
    """Return the JSON Schema for ProposedPlan for structured-output requests."""
    return ProposedPlan.model_json_schema()


# ── Day-plan descriptions (Itinerary Agent) ───────────────────────────────────

DAY_PLAN_SYSTEM_PROMPT = """\
You write short, friendly descriptions for a traveller's day plan.
You receive the activities already chosen (names, times, outdoor flag).
Return ONLY a JSON object: {"descriptions": {"<activity name>": "<one sentence, max 200 characters>"}}
Describe only the given activities, use their exact names as keys, and do not add new activities,
prices, opening hours or booking claims.
"""


def build_day_plan_prompt(request: dict) -> str:
    """User message for the day-plan description call (tagged JSON for MockLLM)."""
    return (
        f"Day {request.get('day')} in {request.get('city')} on {request.get('date')} "
        f"— theme: {request.get('theme')}.\n"
        f"<day_plan_request>{json.dumps(request, sort_keys=True)}</day_plan_request>"
    )
