"""
Contract system for the Task Dependency Graph engine.

Rules:
- All contracts are statically defined templates — no arbitrary code execution.
- Each evaluator receives typed inputs and returns ContractResult.
- Invalid or missing inputs always return failed=True with a clear explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable


# ── Contract identifiers ──────────────────────────────────────────────────────

class ContractType(str, Enum):
    FLIGHT_BEFORE_CHECKIN    = "FLIGHT_BEFORE_CHECKIN"
    TRANSFER_AFTER_FLIGHT    = "TRANSFER_AFTER_FLIGHT"
    DAY_PLAN_FEASIBILITY     = "DAY_PLAN_FEASIBILITY"
    BUDGET_WITHIN_LIMIT      = "BUDGET_WITHIN_LIMIT"
    # Phase 3: edge templates for the trip graph (plan §5.3)
    TRANSFER_BEFORE_DAY_START = "TRANSFER_BEFORE_DAY_START"
    STAY_COVERS_DAY           = "STAY_COVERS_DAY"
    WEATHER_SUITS_PLAN        = "WEATHER_SUITS_PLAN"
    DAY_ENDS_BEFORE_PICKUP    = "DAY_ENDS_BEFORE_PICKUP"
    TRANSFER_BEFORE_DEPARTURE = "TRANSFER_BEFORE_DEPARTURE"
    BUDGET_ITEM_CURRENT       = "BUDGET_ITEM_CURRENT"
    # Phase 4: transfers are routed to/from the booked hotel
    TRANSFER_SERVES_HOTEL     = "TRANSFER_SERVES_HOTEL"


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContractResult:
    holds: bool
    explanation: str
    contract: str
    inputs_used: dict[str, Any] = field(default_factory=dict)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso(value: Any, field_name: str) -> datetime:
    """
    Parse an ISO-8601 datetime string; raise ValueError with a clear message.

    Offset-aware values are converted to UTC and made naive so that they can be
    compared with naive (local-time) values without raising TypeError.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"'{field_name}' must be an ISO-8601 string, got {type(value).__name__}."
        )
    try:
        # Python 3.11+ handles 'Z' suffix natively; for 3.10 normalise it
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"'{field_name}' could not be parsed as an ISO-8601 datetime: {value!r}"
        )
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _require(config: dict[str, Any], *keys: str) -> None:
    """Raise ValueError listing all missing keys."""
    missing = [k for k in keys if k not in config or config[k] is None]
    if missing:
        raise ValueError(f"Missing required contract config keys: {missing}")


# ── Evaluators ────────────────────────────────────────────────────────────────

def _eval_flight_before_checkin(config: dict[str, Any]) -> ContractResult:
    """
    Flight arrival must occur before the hotel check-in cutoff.

    Required config keys:
      - flight_arrival_iso:  ISO-8601 datetime of the flight's scheduled arrival.
      - checkin_cutoff_iso:  ISO-8601 datetime of the hotel's check-in deadline.
    Optional:
      - buffer_minutes:  Extra safety margin in minutes (default 0).
    """
    contract = ContractType.FLIGHT_BEFORE_CHECKIN.value
    try:
        _require(config, "flight_arrival_iso", "checkin_cutoff_iso")
        arrival  = _parse_iso(config["flight_arrival_iso"], "flight_arrival_iso")
        cutoff   = _parse_iso(config["checkin_cutoff_iso"], "checkin_cutoff_iso")
        buffer   = int(config.get("buffer_minutes", 0))
    except (TypeError, ValueError, KeyError) as exc:
        return ContractResult(
            holds=False,
            explanation=f"Invalid input — {exc}",
            contract=contract,
        )

    effective_arrival = arrival + timedelta(minutes=buffer)
    holds = effective_arrival <= cutoff
    if holds:
        delta_min = int((cutoff - effective_arrival).total_seconds() / 60)
        explanation = (
            f"Flight arrives {arrival.isoformat()} — "
            f"{delta_min} minutes before check-in cutoff ({cutoff.isoformat()})."
        )
    else:
        over_min = int((effective_arrival - cutoff).total_seconds() / 60)
        explanation = (
            f"Flight arrives {arrival.isoformat()} which is "
            f"{over_min} minutes after the check-in cutoff ({cutoff.isoformat()})."
        )
    return ContractResult(
        holds=holds,
        explanation=explanation,
        contract=contract,
        inputs_used={"flight_arrival_iso": config["flight_arrival_iso"],
                     "checkin_cutoff_iso": config["checkin_cutoff_iso"],
                     "buffer_minutes": buffer},
    )


def _eval_transfer_after_flight(config: dict[str, Any]) -> ContractResult:
    """
    Transfer pickup time must be derived from (and after) the flight arrival.

    Required config keys:
      - flight_arrival_iso:    ISO-8601 datetime of flight arrival.
      - transfer_pickup_iso:   ISO-8601 datetime of the scheduled transfer.
      - min_gap_minutes:       Minimum required gap between arrival and pickup.
    Optional:
      - max_gap_minutes:       Maximum allowed gap (traveller should not wait longer).
    """
    contract = ContractType.TRANSFER_AFTER_FLIGHT.value
    try:
        _require(config, "flight_arrival_iso", "transfer_pickup_iso", "min_gap_minutes")
        arrival  = _parse_iso(config["flight_arrival_iso"], "flight_arrival_iso")
        pickup   = _parse_iso(config["transfer_pickup_iso"], "transfer_pickup_iso")
        min_gap  = int(config["min_gap_minutes"])
        max_gap  = config.get("max_gap_minutes")
        max_gap  = int(max_gap) if max_gap is not None else None
        if min_gap < 0:
            raise ValueError("'min_gap_minutes' must be non-negative.")
    except (TypeError, ValueError, KeyError) as exc:
        return ContractResult(
            holds=False,
            explanation=f"Invalid input — {exc}",
            contract=contract,
        )

    actual_gap_min = int((pickup - arrival).total_seconds() / 60)
    holds = actual_gap_min >= min_gap
    if holds and max_gap is not None and actual_gap_min > max_gap:
        return ContractResult(
            holds=False,
            explanation=(
                f"Transfer at {pickup.isoformat()} is {actual_gap_min} minutes after "
                f"flight arrival ({arrival.isoformat()}) — "
                f"above the maximum wait of {max_gap} minutes."
            ),
            contract=contract,
            inputs_used={"min_gap_minutes": min_gap, "max_gap_minutes": max_gap,
                         "actual_gap_minutes": actual_gap_min},
        )
    if holds:
        explanation = (
            f"Transfer at {pickup.isoformat()} is {actual_gap_min} minutes after "
            f"flight arrival ({arrival.isoformat()}) — "
            f"meets minimum gap of {min_gap} minutes."
        )
    else:
        explanation = (
            f"Transfer at {pickup.isoformat()} is only {actual_gap_min} minutes after "
            f"flight arrival ({arrival.isoformat()}) — "
            f"below required minimum of {min_gap} minutes."
        )
    return ContractResult(
        holds=holds,
        explanation=explanation,
        contract=contract,
        inputs_used={
            "flight_arrival_iso": config["flight_arrival_iso"],
            "transfer_pickup_iso": config["transfer_pickup_iso"],
            "min_gap_minutes": min_gap,
            "actual_gap_minutes": actual_gap_min,
        },
    )


def _eval_day_plan_feasibility(config: dict[str, Any]) -> ContractResult:
    """
    A day plan must fit within available time.

    Required config keys:
      - available_minutes:   Total minutes available for activities.
      - planned_minutes:     Total minutes the day plan requires (travel + activities).
    Optional:
      - buffer_minutes:      Reserve margin (default 30).
    """
    contract = ContractType.DAY_PLAN_FEASIBILITY.value
    try:
        _require(config, "available_minutes", "planned_minutes")
        available = int(config["available_minutes"])
        planned   = int(config["planned_minutes"])
        buffer    = int(config.get("buffer_minutes", 30))
        if available < 0:
            raise ValueError("'available_minutes' must be non-negative.")
        if planned < 0:
            raise ValueError("'planned_minutes' must be non-negative.")
        if buffer < 0:
            raise ValueError("'buffer_minutes' must be non-negative.")
    except (TypeError, ValueError, KeyError) as exc:
        return ContractResult(
            holds=False,
            explanation=f"Invalid input — {exc}",
            contract=contract,
        )

    required = planned + buffer
    holds = available >= required
    if holds:
        slack = available - required
        explanation = (
            f"Day plan requires {planned} min + {buffer} min buffer = {required} min; "
            f"{available} min available — {slack} min to spare."
        )
    else:
        shortfall = required - available
        explanation = (
            f"Day plan requires {planned} min + {buffer} min buffer = {required} min, "
            f"but only {available} min available — {shortfall} min shortfall."
        )
    return ContractResult(
        holds=holds,
        explanation=explanation,
        contract=contract,
        inputs_used={
            "available_minutes": available,
            "planned_minutes": planned,
            "buffer_minutes": buffer,
        },
    )


def _eval_budget_within_limit(config: dict[str, Any]) -> ContractResult:
    """
    Total cost must not exceed the configured budget limit.

    Required config keys:
      - total_cost:    Numeric total cost (float or int).
      - budget_limit:  Numeric budget ceiling (float or int).
    Optional:
      - currency:      Currency code for display (default "USD").
    """
    contract = ContractType.BUDGET_WITHIN_LIMIT.value
    try:
        _require(config, "total_cost", "budget_limit")
        total_cost   = float(config["total_cost"])
        budget_limit = float(config["budget_limit"])
        currency     = str(config.get("currency", "USD"))
        if budget_limit <= 0:
            raise ValueError("'budget_limit' must be greater than zero.")
        if total_cost < 0:
            raise ValueError("'total_cost' must be non-negative.")
    except (TypeError, ValueError, KeyError) as exc:
        return ContractResult(
            holds=False,
            explanation=f"Invalid input — {exc}",
            contract=contract,
        )

    holds = total_cost <= budget_limit
    if holds:
        remaining = budget_limit - total_cost
        explanation = (
            f"Total cost {total_cost:,.2f} {currency} is within the "
            f"{budget_limit:,.2f} {currency} limit "
            f"({remaining:,.2f} {currency} remaining)."
        )
    else:
        overage = total_cost - budget_limit
        explanation = (
            f"Total cost {total_cost:,.2f} {currency} exceeds the "
            f"{budget_limit:,.2f} {currency} limit "
            f"by {overage:,.2f} {currency}."
        )
    return ContractResult(
        holds=holds,
        explanation=explanation,
        contract=contract,
        inputs_used={
            "total_cost": total_cost,
            "budget_limit": budget_limit,
            "currency": currency,
        },
    )


def _invalid(contract: str, exc: Exception) -> ContractResult:
    return ContractResult(holds=False, explanation=f"Invalid input — {exc}", contract=contract)


def _eval_transfer_before_day_start(config: dict[str, Any]) -> ContractResult:
    """
    The arrival transfer must drop the traveller at the hotel before the day
    plan's first activity (plus a buffer to check in and freshen up).

    Required: transfer_dropoff_iso, day_start_iso. Optional: buffer_minutes (30).
    """
    contract = ContractType.TRANSFER_BEFORE_DAY_START.value
    try:
        _require(config, "transfer_dropoff_iso", "day_start_iso")
        dropoff = _parse_iso(config["transfer_dropoff_iso"], "transfer_dropoff_iso")
        start   = _parse_iso(config["day_start_iso"], "day_start_iso")
        buffer  = int(config.get("buffer_minutes", 30))
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid(contract, exc)

    ready = dropoff + timedelta(minutes=buffer)
    holds = ready <= start
    explanation = (
        f"Traveller reaches the hotel at {dropoff.isoformat()} (+{buffer} min) "
        + ("before" if holds else "after")
        + f" the day plan starts at {start.isoformat()}."
    )
    return ContractResult(holds, explanation, contract,
                          {"transfer_dropoff_iso": config["transfer_dropoff_iso"],
                           "day_start_iso": config["day_start_iso"], "buffer_minutes": buffer})


def _eval_stay_covers_day(config: dict[str, Any]) -> ContractResult:
    """
    The day plan must fall inside the stay and be based at that hotel.

    Required: check_in, check_out, day_date (ISO dates), stay_hotel_id, plan_hotel_id.
    """
    contract = ContractType.STAY_COVERS_DAY.value
    try:
        _require(config, "check_in", "check_out", "day_date", "stay_hotel_id", "plan_hotel_id")
        check_in  = date.fromisoformat(str(config["check_in"])[:10])
        check_out = date.fromisoformat(str(config["check_out"])[:10])
        day       = date.fromisoformat(str(config["day_date"])[:10])
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid(contract, exc)

    if not (check_in <= day < check_out):
        return ContractResult(False, f"Day {day} is outside the stay {check_in} – {check_out}.", contract)
    if config["stay_hotel_id"] != config["plan_hotel_id"]:
        return ContractResult(
            False,
            f"Day plan is based at '{config['plan_hotel_id']}' but the stay is '{config['stay_hotel_id']}'.",
            contract,
        )
    return ContractResult(True, f"Day {day} is within the stay and based at the booked hotel.", contract)


STORM_CONDITIONS = frozenset({"storm", "thunderstorm", "heavy_rain", "hurricane", "snowstorm"})


def _eval_weather_suits_plan(config: dict[str, Any]) -> ContractResult:
    """
    No outdoor activities on a storm day.

    Required: condition (str), outdoor_activities (list of names).
    """
    contract = ContractType.WEATHER_SUITS_PLAN.value
    try:
        _require(config, "condition", "outdoor_activities")
        condition = str(config["condition"])
        outdoor = list(config["outdoor_activities"])
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid(contract, exc)

    stormy = condition in STORM_CONDITIONS or bool(config.get("is_storm"))
    if stormy and outdoor:
        return ContractResult(
            False,
            f"Forecast is '{condition}' but the plan has outdoor items: {', '.join(outdoor)}.",
            contract,
        )
    detail = "no outdoor items scheduled" if stormy else "weather does not restrict the plan"
    return ContractResult(True, f"Forecast '{condition}' — {detail}.", contract)


def _eval_day_ends_before_pickup(config: dict[str, Any]) -> ContractResult:
    """
    The last day's activities must end before the departure transfer pickup.

    Required: day_end_iso, transfer_pickup_iso. Optional: buffer_minutes (60).
    """
    contract = ContractType.DAY_ENDS_BEFORE_PICKUP.value
    try:
        _require(config, "day_end_iso", "transfer_pickup_iso")
        end    = _parse_iso(config["day_end_iso"], "day_end_iso")
        pickup = _parse_iso(config["transfer_pickup_iso"], "transfer_pickup_iso")
        buffer = int(config.get("buffer_minutes", 60))
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid(contract, exc)

    holds = end + timedelta(minutes=buffer) <= pickup
    explanation = (
        f"Last activity ends {end.isoformat()}; pickup at {pickup.isoformat()} — "
        + ("enough time to pack." if holds else f"less than {buffer} min to pack.")
    )
    return ContractResult(holds, explanation, contract)


def _eval_transfer_before_departure(config: dict[str, Any]) -> ContractResult:
    """
    The departure transfer must reach the airport early enough for the flight.

    Required: transfer_dropoff_iso, flight_departure_iso.
    Optional: min_buffer_minutes (120), max_buffer_minutes.
    """
    contract = ContractType.TRANSFER_BEFORE_DEPARTURE.value
    try:
        _require(config, "transfer_dropoff_iso", "flight_departure_iso")
        dropoff   = _parse_iso(config["transfer_dropoff_iso"], "transfer_dropoff_iso")
        departure = _parse_iso(config["flight_departure_iso"], "flight_departure_iso")
        min_buf   = int(config.get("min_buffer_minutes", 120))
        max_buf   = config.get("max_buffer_minutes")
        max_buf   = int(max_buf) if max_buf is not None else None
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid(contract, exc)

    gap = int((departure - dropoff).total_seconds() / 60)
    if gap < min_buf:
        return ContractResult(False, f"Arrives at the airport {gap} min before departure — needs {min_buf}.", contract)
    if max_buf is not None and gap > max_buf:
        return ContractResult(False, f"Arrives at the airport {gap} min before departure — more than {max_buf}.", contract)
    return ContractResult(True, f"Arrives at the airport {gap} min before departure.", contract)


def _eval_budget_item_current(config: dict[str, Any]) -> ContractResult:
    """
    The budget check must have used the current cost of this item.

    Required: item_id, item_cost. ``recorded_cost`` is None when the item is
    missing from the budget's line items.
    """
    contract = ContractType.BUDGET_ITEM_CURRENT.value
    try:
        _require(config, "item_id", "item_cost")
        item_cost = float(config["item_cost"])
        recorded = config.get("recorded_cost")
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid(contract, exc)

    if recorded is None:
        return ContractResult(False, f"Budget has no line item for '{config['item_id']}'.", contract)
    holds = abs(float(recorded) - item_cost) < 0.005
    explanation = (
        f"Budget uses {float(recorded):,.2f} for '{config['item_id']}'; current cost is {item_cost:,.2f}."
    )
    return ContractResult(holds, explanation, contract)


def _eval_transfer_serves_hotel(config: dict[str, Any]) -> ContractResult:
    """
    The transfer must go to (or from) the booked hotel.

    Required: stay_hotel_id, transfer_hotel_id.
    """
    contract = ContractType.TRANSFER_SERVES_HOTEL.value
    try:
        _require(config, "stay_hotel_id", "transfer_hotel_id")
    except ValueError as exc:
        return _invalid(contract, exc)
    if config["stay_hotel_id"] != config["transfer_hotel_id"]:
        return ContractResult(False, f"Transfer serves '{config['transfer_hotel_id']}' but the stay is "
                                     f"'{config['stay_hotel_id']}'.", contract)
    return ContractResult(True, "Transfer is routed to the booked hotel.", contract)


# ── Registry and public API ───────────────────────────────────────────────────

_EVALUATORS: dict[str, Callable[[dict[str, Any]], ContractResult]] = {
    ContractType.FLIGHT_BEFORE_CHECKIN.value     : _eval_flight_before_checkin,
    ContractType.TRANSFER_AFTER_FLIGHT.value     : _eval_transfer_after_flight,
    ContractType.DAY_PLAN_FEASIBILITY.value      : _eval_day_plan_feasibility,
    ContractType.BUDGET_WITHIN_LIMIT.value       : _eval_budget_within_limit,
    ContractType.TRANSFER_BEFORE_DAY_START.value : _eval_transfer_before_day_start,
    ContractType.STAY_COVERS_DAY.value           : _eval_stay_covers_day,
    ContractType.WEATHER_SUITS_PLAN.value        : _eval_weather_suits_plan,
    ContractType.DAY_ENDS_BEFORE_PICKUP.value    : _eval_day_ends_before_pickup,
    ContractType.TRANSFER_BEFORE_DEPARTURE.value : _eval_transfer_before_departure,
    ContractType.BUDGET_ITEM_CURRENT.value       : _eval_budget_item_current,
    ContractType.TRANSFER_SERVES_HOTEL.value     : _eval_transfer_serves_hotel,
}

KNOWN_CONTRACTS: frozenset[str] = frozenset(_EVALUATORS.keys())


def evaluate_contract(contract: str, config: dict[str, Any]) -> ContractResult:
    """
    Evaluate a named contract against the provided configuration.

    Raises ValueError for unknown contract identifiers.
    Never executes arbitrary code or LLM-generated expressions.
    """
    evaluator = _EVALUATORS.get(contract)
    if evaluator is None:
        known = ", ".join(sorted(KNOWN_CONTRACTS))
        raise ValueError(
            f"Unknown contract '{contract}'. Known contracts: {known}"
        )
    return evaluator(config)


# ── Edge binding: holds(src.output, dst.output) ───────────────────────────────
#
# A binder reads the facts a contract needs from the two node outputs.
# Static knobs (buffers, gaps) come from edge.contract_config; facts read from
# outputs are layered on top of them.

Binder = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _bind_flight_before_checkin(src_id, flight, stay):
    return {"flight_arrival_iso": flight.get("arrival_time"),
            "checkin_cutoff_iso": stay.get("checkin_cutoff")}


def _bind_transfer_after_flight(src_id, flight, transfer):
    return {"flight_arrival_iso": flight.get("arrival_time"),
            "transfer_pickup_iso": transfer.get("pickup_time")}


def _bind_transfer_before_day_start(src_id, transfer, day):
    return {"transfer_dropoff_iso": transfer.get("dropoff_time"),
            "day_start_iso": day.get("start_time")}


def _bind_stay_covers_day(src_id, stay, day):
    return {"check_in": stay.get("check_in"), "check_out": stay.get("check_out"),
            "day_date": day.get("date"),
            "stay_hotel_id": stay.get("hotel_id"), "plan_hotel_id": day.get("hotel_id")}


def _bind_weather_suits_plan(src_id, weather, day):
    activities = day.get("activities")
    outdoor = None
    if isinstance(activities, list):
        outdoor = [a.get("name", "?") for a in activities if isinstance(a, dict) and a.get("outdoor")]
    return {"condition": weather.get("condition"), "is_storm": weather.get("is_storm", False),
            "outdoor_activities": outdoor}


def _bind_day_ends_before_pickup(src_id, day, transfer):
    return {"day_end_iso": day.get("end_time"), "transfer_pickup_iso": transfer.get("pickup_time")}


def _bind_transfer_before_departure(src_id, flight, transfer):
    return {"transfer_dropoff_iso": transfer.get("dropoff_time"),
            "flight_departure_iso": flight.get("departure_time")}


def _bind_transfer_serves_hotel(src_id, stay, transfer):
    return {"stay_hotel_id": stay.get("hotel_id"), "transfer_hotel_id": transfer.get("hotel_id")}


def _bind_budget_item_current(src_id, item, budget):
    line_items = budget.get("line_items") if isinstance(budget.get("line_items"), dict) else {}
    return {"item_id": src_id, "item_cost": item.get("cost"), "recorded_cost": line_items.get(src_id)}


_BINDERS: dict[str, Binder] = {
    ContractType.FLIGHT_BEFORE_CHECKIN.value     : _bind_flight_before_checkin,
    ContractType.TRANSFER_AFTER_FLIGHT.value     : _bind_transfer_after_flight,
    ContractType.TRANSFER_BEFORE_DAY_START.value : _bind_transfer_before_day_start,
    ContractType.STAY_COVERS_DAY.value           : _bind_stay_covers_day,
    ContractType.WEATHER_SUITS_PLAN.value        : _bind_weather_suits_plan,
    ContractType.DAY_ENDS_BEFORE_PICKUP.value    : _bind_day_ends_before_pickup,
    ContractType.TRANSFER_BEFORE_DEPARTURE.value : _bind_transfer_before_departure,
    ContractType.BUDGET_ITEM_CURRENT.value       : _bind_budget_item_current,
    ContractType.TRANSFER_SERVES_HOTEL.value     : _bind_transfer_serves_hotel,
}

EDGE_BOUND_CONTRACTS: frozenset[str] = frozenset(_BINDERS.keys())


def check_edge(
    contract: str,
    contract_config: dict[str, Any],
    source_id: str,
    src_output: dict[str, Any] | None,
    dst_output: dict[str, Any] | None,
) -> ContractResult:
    """
    Evaluate an edge contract against the current outputs of its two nodes —
    ``holds(src.output, dst.output)`` from plan §6.1.

    Contracts without a binder are evaluated on ``contract_config`` alone.
    A node without output never satisfies a bound contract.
    """
    binder = _BINDERS.get(contract)
    if binder is None:
        return evaluate_contract(contract, dict(contract_config))
    if not src_output or not dst_output:
        return ContractResult(False, "Contract cannot be checked: a node has no output yet.", contract)
    # A day plan with nothing scheduled (e.g. after a very late arrival) cannot be missed or overrun.
    day = {ContractType.TRANSFER_BEFORE_DAY_START.value: dst_output,
           ContractType.DAY_ENDS_BEFORE_PICKUP.value: src_output}.get(contract)
    if day is not None and day.get("activities") == []:
        return ContractResult(True, "Day plan has no scheduled activities, so there is nothing to reach or finish.",
                              contract)
    config = dict(contract_config)
    config.update(binder(source_id, src_output, dst_output))
    return evaluate_contract(contract, config)
