"""
Risk Gate (plan §6.4): every action with consequences is classified into a
risk tier before it can take effect.

  T0 autonomous — search, compare, fetch weather, draft a plan. Runs at once.
  T1 notify     — small reversible changes (reschedule a pending transfer).
                  Runs, then the user is told, with an undo option.
  T2 approve    — book, cancel, change a booking, pay, any spend, non-refundable
                  items. Pauses: the node goes to AWAITING_APPROVAL and an
                  approval card is queued until the user decides.

Hard rules override the score: book / change / cancel / pay are always T2,
and money is never spent in T0 or T1. A T2 action never executes without an
explicit approval — not after a timeout (the offer is held and the user is
reminded), not after a retry, not after a re-plan (a pending approval whose
offer or price changed is superseded and a new one is raised).

Execution is guarded twice:
  1. the gate only executes an action it moved from PENDING with an explicit
     "approved" decision, after re-checking that the node still carries the
     approved offer at the approved price;
  2. the booking provider refuses any call without a signed, single-use
     ``ApprovalGrant``, which only ``RiskGate.approve`` issues.

The gate does not book anything itself: an executor (app.agents.actions)
performs the approved action through the booking provider.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.graph import GraphStore
from app.core.models import NodeStatus, NodeType, RiskTier, TaskNode


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Vocabulary ────────────────────────────────────────────────────────────────

class ActionKind(str, Enum):
    SEARCH = "search"
    COMPARE = "compare"
    DRAFT = "draft"
    NOTIFY = "notify"
    REORDER = "reorder"
    RESCHEDULE = "reschedule"
    BOOK = "book"
    CHANGE = "change"
    CANCEL = "cancel"
    PAY = "pay"


class ActionStatus(str, Enum):
    APPLIED = "applied"          # T0 / T1: already in effect
    UNDONE = "undone"            # T1 reverted by the user
    PENDING = "pending"          # T2: waiting for the user (held, never auto-executed)
    APPROVING = "approving"      # T2: claimed by an approval that is executing
    EXECUTED = "executed"        # T2: approved and carried out
    REJECTED = "rejected"        # T2: the user said no
    SUPERSEDED = "superseded"    # T2: the offer or price changed before a decision


ALWAYS_T2 = {ActionKind.BOOK, ActionKind.CHANGE, ActionKind.CANCEL, ActionKind.PAY}
SPEND_KINDS = ALWAYS_T2
BOOKABLE = {NodeType.FLIGHT, NodeType.STAY}
OPEN = {ActionStatus.PENDING, ActionStatus.APPROVING}

IRREVERSIBILITY = {
    ActionKind.SEARCH: 0.0, ActionKind.COMPARE: 0.0, ActionKind.NOTIFY: 0.0, ActionKind.DRAFT: 0.05,
    ActionKind.REORDER: 0.15, ActionKind.RESCHEDULE: 0.2,
    ActionKind.BOOK: 0.6, ActionKind.CHANGE: 0.8, ActionKind.CANCEL: 1.0, ActionKind.PAY: 1.0,
}
VISIBILITY = {
    NodeType.FLIGHT: 1.0, NodeType.STAY: 1.0, NodeType.TRANSFER: 0.6,
    NodeType.DAY_PLAN: 0.5, NodeType.BUDGET: 0.2, NodeType.WEATHER: 0.0,
}


class RiskPolicy(BaseModel):
    """Score weights and thresholds (plan §6.4). Hard rules apply on top."""
    spend_threshold: float = Field(default=250.0, gt=0)
    w_irreversibility: float = 0.4
    w_cost: float = 0.3
    w_visibility: float = 0.2
    w_uncertainty: float = 0.1
    t1_score: float = 0.15
    t2_score: float = 0.6
    remind_after_minutes: int = Field(default=30, ge=1)

    @classmethod
    def from_settings(cls, settings: Any = None) -> "RiskPolicy":
        from app.config import get_settings

        s = settings or get_settings()
        return cls(spend_threshold=s.voyagesync_risk_spend_threshold,
                   remind_after_minutes=s.voyagesync_approval_remind_minutes)


# ── Actions ───────────────────────────────────────────────────────────────────

class Action(BaseModel):
    """One proposed action and, for T2, its approval card and decision."""
    model_config = ConfigDict(validate_assignment=False)

    action_id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:12]}")
    trip_id: str = ""
    node_id: str
    node_type: NodeType
    kind: ActionKind
    tier: RiskTier = RiskTier.T0
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.APPLIED

    # Approval card: option, old price → new price, what changes, why
    title: str = ""
    why: str = ""
    event_id: str = ""
    option: dict[str, Any] = Field(default_factory=dict)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    currency: str = ""
    amount: float = 0.0                    # money this action commits when executed
    old_amount: float | None = None
    cost_delta: float = 0.0                # change in planned cost (drafts, reschedules)
    refundable: bool = True
    confidence: float = 0.9
    offer_id: str | None = None
    previous_offer_id: str | None = None
    previous_booking_ref: str | None = None

    undo: dict[str, Any] | None = None     # {"previous_output", "new_output"}
    result: dict[str, Any] | None = None   # executor result (booking confirmation)
    decision: str | None = None            # "approved" | "rejected"
    decided_by: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    remind_at: datetime | None = None
    reminders: int = 0
    last_error: str = ""

    @property
    def price_difference(self) -> float | None:
        if self.old_amount is None:
            return None
        return round(self.amount - self.old_amount, 2)

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["price_difference"] = self.price_difference
        data["undoable"] = self.tier == RiskTier.T1 and self.status == ActionStatus.APPLIED
        return data


def risk_score(action: Action, policy: RiskPolicy) -> float:
    """w1·irreversibility + w2·normalised_cost + w3·user_visibility + w4·(1 − confidence)."""
    irreversibility = IRREVERSIBILITY[action.kind]
    if action.kind == ActionKind.BOOK and not action.refundable:
        irreversibility = 1.0
    money = max(action.amount, action.cost_delta, 0.0)
    cost = min(1.0, money / policy.spend_threshold)
    visibility = VISIBILITY.get(action.node_type, 0.5)
    uncertainty = 1.0 - min(1.0, max(0.0, action.confidence))
    return round(policy.w_irreversibility * irreversibility + policy.w_cost * cost
                 + policy.w_visibility * visibility + policy.w_uncertainty * uncertainty, 4)


def classify(action: Action, policy: RiskPolicy) -> tuple[RiskTier, float, list[str]]:
    """Tier for *action*: hard rules first, then the score thresholds."""
    score = risk_score(action, policy)
    reasons: list[str] = []
    if action.kind in (ActionKind.PAY, ActionKind.CANCEL):
        reasons.append(f"{action.kind.value} is always T2")
    elif action.kind in (ActionKind.BOOK, ActionKind.CHANGE):
        reasons.append(f"{action.kind.value} commits to a booking: always T2")
    if action.amount > 0:
        reasons.append(f"spends money ({action.amount:.2f} {action.currency}); money is never spent in T0/T1")
    if action.amount > policy.spend_threshold:
        reasons.append(f"spend above the {policy.spend_threshold:g} threshold")
    if action.amount > 0 and not action.refundable:
        reasons.append("non-refundable")
    if reasons:
        return RiskTier.T2, score, reasons
    if score >= policy.t2_score:
        return RiskTier.T2, score, [f"risk score {score:.2f} ≥ {policy.t2_score:g}"]
    if score >= policy.t1_score:
        return RiskTier.T1, score, [f"risk score {score:.2f} ≥ {policy.t1_score:g}: run and notify"]
    return RiskTier.T0, score, [f"risk score {score:.2f} < {policy.t1_score:g}: autonomous"]


# ── Approval grants (the only key that opens the booking provider) ────────────

class UnapprovedActionError(PermissionError):
    """Raised by an executor/provider asked to act without a valid approval grant."""


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    node_id: str
    offer_id: str
    amount: float
    nonce: str
    signature: str


_SECRET = secrets.token_bytes(32)
_USED_NONCES: set[str] = set()


def _sign(action_id: str, node_id: str, offer_id: str, amount: float, nonce: str) -> str:
    message = f"{action_id}|{node_id}|{offer_id}|{amount:.2f}|{nonce}".encode()
    return hmac.new(_SECRET, message, hashlib.sha256).hexdigest()


def verify_grant(grant: Any, *, offer_id: str | None = None, consume: bool = True) -> ApprovalGrant:
    """Check a grant's signature (and offer); consume it unless ``consume=False``."""
    if not isinstance(grant, ApprovalGrant):
        raise UnapprovedActionError("no approval grant: T2 actions run only after the user approves them")
    expected = _sign(grant.action_id, grant.node_id, grant.offer_id, grant.amount, grant.nonce)
    if not hmac.compare_digest(expected, grant.signature):
        raise UnapprovedActionError(f"invalid approval grant for {grant.action_id}")
    if offer_id is not None and offer_id != grant.offer_id:
        raise UnapprovedActionError(f"grant {grant.action_id} covers offer {grant.offer_id}, not {offer_id}")
    if consume:
        if grant.nonce in _USED_NONCES:
            raise UnapprovedActionError(f"approval grant for {grant.action_id} was already used")
        _USED_NONCES.add(grant.nonce)
    return grant


class Executor(Protocol):
    async def execute(self, action: Action, grant: ApprovalGrant, output: dict[str, Any]) -> dict[str, Any]: ...


# ── Ledger (in-memory; persisted by app.db.approval_repo) ─────────────────────

class ActionLedger:
    """All actions of one trip, in creation order."""

    def __init__(self, trip_id: str = "", actions: list[Action] | None = None) -> None:
        self.trip_id = trip_id
        self._actions: dict[str, Action] = {a.action_id: a for a in actions or []}

    def add(self, action: Action) -> Action:
        action.trip_id = action.trip_id or self.trip_id
        self._actions[action.action_id] = action
        return action

    def get(self, action_id: str) -> Action:
        return self._actions[action_id]

    def __contains__(self, action_id: str) -> bool:
        return action_id in self._actions

    def list(self) -> list[Action]:
        return list(self._actions.values())

    def pending(self) -> list[Action]:
        return [a for a in self._actions.values() if a.status in OPEN]

    def for_node(self, node_id: str) -> list[Action]:
        return [a for a in self._actions.values() if a.node_id == node_id]

    def pending_for(self, node_id: str) -> Action | None:
        return next((a for a in reversed(self.for_node(node_id)) if a.status in OPEN), None)

    def booking_for(self, node_id: str) -> Action | None:
        """The booking currently held for *node_id* (last executed book/change)."""
        return next((a for a in reversed(self.for_node(node_id))
                     if a.kind in (ActionKind.BOOK, ActionKind.CHANGE) and a.status == ActionStatus.EXECUTED), None)

    def has_booking_intent(self, node_id: str) -> bool:
        return any(a.kind in (ActionKind.BOOK, ActionKind.CHANGE) for a in self.for_node(node_id))

    def rejected(self, node_id: str, offer_id: str | None) -> bool:
        return any(a.status == ActionStatus.REJECTED and a.offer_id == offer_id for a in self.for_node(node_id))


# ── Errors ────────────────────────────────────────────────────────────────────

class GateError(Exception):
    """Base error for gate decisions."""


class GateConflict(GateError):
    """The action cannot be decided or undone in its current state."""


class GateExecutionError(GateError):
    """The executor failed; the action stays pending and nothing was booked."""


# ── Gate ──────────────────────────────────────────────────────────────────────

def _hhmm(iso: Any) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%H:%M")
    except ValueError:
        return str(iso)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


_OPTION_FIELDS = {
    NodeType.FLIGHT: ("flight_number", "airline", "origin_iata", "destination_iata", "departure_time",
                      "arrival_time", "stops", "cabin_class", "cost", "currency", "offer_id"),
    NodeType.STAY: ("hotel_name", "address", "check_in", "check_out", "nights", "room_type", "price_per_night",
                    "cost", "currency", "refundable", "offer_id"),
}
_CHANGE_FIELDS = {
    NodeType.FLIGHT: ("flight_number", "departure_time", "arrival_time", "cost"),
    NodeType.STAY: ("hotel_name", "check_in", "check_out", "cost", "refundable"),
    NodeType.TRANSFER: ("pickup_time", "dropoff_time", "cost"),
    NodeType.DAY_PLAN: ("cost",),
    NodeType.BUDGET: ("total", "within_budget"),
}
_BOOKING_KEYS = ("booking_status", "booking_ref", "booked_at", "booking_provider", "approval_id")


def _changes(node_type: NodeType, old: dict[str, Any] | None, new: dict[str, Any]) -> list[dict[str, Any]]:
    if not old:
        return []
    return [{"field": f, "old": old.get(f), "new": new.get(f)}
            for f in _CHANGE_FIELDS.get(node_type, ()) if old.get(f) != new.get(f)]


class RiskGate:
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()

    # ── classification and submission ─────────────────────────────────────────

    def classify(self, action: Action) -> Action:
        action.tier, action.score, action.reasons = classify(action, self.policy)
        return action

    def _submit(self, store: GraphStore, ledger: ActionLedger, action: Action) -> Action:
        self.classify(action)
        ledger.add(action)
        if action.tier == RiskTier.T2:
            action.status = ActionStatus.PENDING
            action.remind_at = _now() + timedelta(minutes=self.policy.remind_after_minutes)
            node = store.get_node(action.node_id)
            store.update_node_status(
                node.id, NodeStatus.AWAITING_APPROVAL,
                output={**(node.output or {}), "booking_status": "awaiting_approval", "approval_id": action.action_id},
                reason=f"risk gate: T2 {action.kind.value} awaits approval ({action.action_id})")
        else:
            action.status = ActionStatus.APPLIED
        return action

    # ── after planning: propose the bookings (demo step 2) ────────────────────

    def propose_bookings(self, store: GraphStore, ledger: ActionLedger,
                         why: str = "Book the planned trip") -> list[Action]:
        actions = []
        for nid in store.validate():
            node = store.get_node(nid)
            if node.type not in BOOKABLE or node.status != NodeStatus.DONE or not (node.output or {}).get("offer_id"):
                continue
            if ledger.pending_for(nid) or ledger.booking_for(nid):
                continue
            actions.append(self._submit(store, ledger, self._booking_action(node, ActionKind.BOOK, None, None,
                                                                            why, "")))
        return actions

    # ── after a re-plan ───────────────────────────────────────────────────────

    def review_replan(self, store: GraphStore, ledger: ActionLedger, before: dict[str, dict | None],
                      changed: list[str], *, why: str = "", event_id: str = "") -> list[Action]:
        """
        Classify what a re-plan changed. Bookable nodes are reconciled with
        their bookings and pending approvals (supersede, re-stamp or raise a
        new T2); other changed nodes become T0 drafts or T1 notifications.
        """
        actions: list[Action] = []
        for nid in store.validate():
            node = store.get_node(nid)
            if node.type in BOOKABLE:
                actions += self._reconcile_booking(store, ledger, node, before.get(nid), why, event_id)
        for nid in changed:
            node = store.get_node(nid)
            if node.type in BOOKABLE or node.status != NodeStatus.DONE:
                continue
            actions.append(self._submit(store, ledger, self._change_action(node, before.get(nid), why, event_id)))
        return actions

    def _reconcile_booking(self, store: GraphStore, ledger: ActionLedger, node: TaskNode,
                           before: dict | None, why: str, event_id: str) -> list[Action]:
        out = node.output or {}
        offer = out.get("offer_id")
        pending = ledger.pending_for(node.id)
        booked = ledger.booking_for(node.id)
        usable = node.status in (NodeStatus.DONE, NodeStatus.AWAITING_APPROVAL) and offer \
            and out.get("status") not in ("cancelled", "unavailable")

        if not usable:
            if pending:
                self._supersede(pending, "the offer is no longer available")
            return []
        if pending and pending.offer_id == offer and abs(pending.amount - (_num(out.get("cost")) or 0)) < 0.01:
            if node.status != NodeStatus.AWAITING_APPROVAL or out.get("approval_id") != pending.action_id:
                store.update_node_status(node.id, NodeStatus.AWAITING_APPROVAL,
                                         output={**out, "booking_status": "awaiting_approval",
                                                 "approval_id": pending.action_id},
                                         reason=f"risk gate: still awaiting approval ({pending.action_id})")
            return []
        if pending:
            self._supersede(pending, "re-plan replaced the offer" if pending.offer_id != offer
                            else "the price changed")
        if booked and booked.offer_id == offer:
            self._stamp_booked(store, node, booked)
            return []
        if not ledger.has_booking_intent(node.id):
            return []                                    # a draft search result: nothing to book (T0)
        if ledger.rejected(node.id, offer):
            if node.status != NodeStatus.DONE:
                store.update_node_status(node.id, NodeStatus.DONE, output={**out, "booking_status": "rejected"},
                                         reason="risk gate: this offer was rejected before")
            return []
        kind = ActionKind.CHANGE if booked else ActionKind.BOOK
        action = self._booking_action(node, kind, booked, before, why, event_id)
        return [self._submit(store, ledger, action)]

    def _booking_action(self, node: TaskNode, kind: ActionKind, booked: Action | None, before: dict | None,
                        why: str, event_id: str) -> Action:
        out = node.output or {}
        previous = booked.option if booked else (before if before and before.get("offer_id") != out.get("offer_id")
                                                 else None)
        old_amount = booked.amount if booked else (_num(previous.get("cost")) if previous else None)
        if node.type == NodeType.FLIGHT:
            route = f"{out.get('origin_iata', '')}→{out.get('destination_iata', '')}"
            label = (f"{out.get('flight_number', 'flight')} {route}, "
                     f"{_hhmm(out.get('departure_time'))}–{_hhmm(out.get('arrival_time'))}")
        else:
            label = f"{out.get('hotel_name', 'hotel')}, {out.get('nights', '?')} nights"
        title = f"{'Change to' if kind == ActionKind.CHANGE else 'Book'} {label}"
        return Action(
            node_id=node.id, node_type=node.type, kind=kind, title=title, why=why, event_id=event_id,
            option={k: out.get(k) for k in _OPTION_FIELDS[node.type] if k in out},
            changes=_changes(node.type, previous, out),
            currency=str(out.get("currency", "")), amount=_num(out.get("cost")) or 0.0, old_amount=old_amount,
            refundable=bool(out.get("refundable", False)),
            confidence=0.8 if out.get("selection") == "least-change" else 0.9,
            offer_id=out.get("offer_id"),
            previous_offer_id=(previous or {}).get("offer_id"),
            previous_booking_ref=(booked.result or {}).get("booking_ref") if booked else None,
        )

    def _change_action(self, node: TaskNode, before: dict | None, why: str, event_id: str) -> Action:
        out = node.output or {}
        prev = before or {}
        if node.type == NodeType.TRANSFER and prev.get("pickup_time") != out.get("pickup_time"):
            kind, title = ActionKind.RESCHEDULE, f"Rebooked pickup for {_hhmm(out.get('pickup_time'))}"
        elif node.type == NodeType.DAY_PLAN:
            kind, title = ActionKind.DRAFT, f"Day {node.inputs.get('day', '?')} plan updated"
        elif node.type == NodeType.BUDGET:
            kind, title = ActionKind.DRAFT, "Budget re-checked"
        elif node.type == NodeType.WEATHER:
            kind, title = ActionKind.SEARCH, "Weather refreshed"
        else:
            kind, title = ActionKind.DRAFT, f"{node.id} updated"
        old_cost, new_cost = _num(prev.get("cost")), _num(out.get("cost"))
        return Action(
            node_id=node.id, node_type=node.type, kind=kind, title=title, why=why, event_id=event_id,
            changes=_changes(node.type, prev, out), currency=str(out.get("currency", "")),
            cost_delta=round(new_cost - old_cost, 2) if old_cost is not None and new_cost is not None else 0.0,
            undo={"previous_output": copy.deepcopy(before), "new_output": copy.deepcopy(out)},
        )

    @staticmethod
    def _supersede(action: Action, reason: str) -> None:
        action.status = ActionStatus.SUPERSEDED
        action.last_error = reason

    @staticmethod
    def _stamp_booked(store: GraphStore, node: TaskNode, booked: Action) -> None:
        out = node.output or {}
        result = booked.result or {}
        stamp = {"booking_status": "booked", "booking_ref": result.get("booking_ref"),
                 "booked_at": result.get("booked_at"), "booking_provider": result.get("provider"),
                 "approval_id": booked.action_id}
        if node.status == NodeStatus.DONE and all(out.get(k) == v for k, v in stamp.items()):
            return
        store.update_node_status(node.id, NodeStatus.DONE, output={**out, **stamp},
                                 reason=f"risk gate: booking {stamp['booking_ref']} still holds")

    # ── decisions ─────────────────────────────────────────────────────────────

    @staticmethod
    def _open(ledger: ActionLedger, action_id: str) -> Action:
        action = ledger.get(action_id)
        if action.tier != RiskTier.T2 or action.status != ActionStatus.PENDING:
            raise GateConflict(f"{action_id} is {action.status.value}, not awaiting approval")
        return action

    @staticmethod
    def _still_current(action: Action, node: TaskNode) -> str:
        out = node.output or {}
        if node.status != NodeStatus.AWAITING_APPROVAL or out.get("approval_id") != action.action_id:
            return f"{node.id} is no longer waiting for this approval"
        if action.offer_id is not None and out.get("offer_id") != action.offer_id:
            return f"{node.id} now holds a different offer"
        if action.kind in SPEND_KINDS and abs((_num(out.get("cost")) or 0.0) - action.amount) >= 0.01:
            return f"the price of {node.id} changed since this card was raised"
        return ""

    async def approve(self, store: GraphStore, ledger: ActionLedger, action_id: str, executor: Executor | None,
                      *, decided_by: str = "user") -> Action:
        return await self._execute(store, ledger, action_id, executor, decision="approved", decided_by=decided_by)

    async def execute_without_approval(self, store: GraphStore, ledger: ActionLedger, action_id: str,
                                       executor: Executor | None) -> Action:
        """
        **Evaluation ablation "no Risk Gate" only**: execute a T2 action at once, as
        an ungated agent would. The decision is recorded as "auto", so
        ``unsafe_actions`` counts the execution as unsafe. No API route calls this.
        """
        return await self._execute(store, ledger, action_id, executor, decision="auto", decided_by="no-gate")

    async def _execute(self, store: GraphStore, ledger: ActionLedger, action_id: str, executor: Executor | None,
                       *, decision: str, decided_by: str) -> Action:
        action = self._open(ledger, action_id)
        node = store.get_node(action.node_id)
        stale = self._still_current(action, node)
        if stale:
            self._supersede(action, stale)
            raise GateConflict(f"{action_id} was superseded: {stale}")

        out = dict(node.output or {})
        if action.kind in SPEND_KINDS:
            if executor is None:
                raise GateExecutionError("no executor available for this action")
            nonce = secrets.token_hex(16)
            grant = ApprovalGrant(action_id=action.action_id, node_id=action.node_id, offer_id=action.offer_id or "",
                                  amount=round(action.amount, 2), nonce=nonce,
                                  signature=_sign(action.action_id, action.node_id, action.offer_id or "",
                                                  round(action.amount, 2), nonce))
            action.status = ActionStatus.APPROVING
            try:
                result = await executor.execute(action, grant, out)
            except Exception as exc:  # nothing was booked: stays pending for the user
                action.status = ActionStatus.PENDING
                action.last_error = f"execution failed: {exc}"
                raise GateExecutionError(action.last_error) from exc
            action.result = result
            out.update(booking_status="booked", booking_ref=result.get("booking_ref"),
                       booked_at=result.get("booked_at"), booking_provider=result.get("provider"))
        action.decision, action.decided_by, action.decided_at = decision, decided_by, _now()
        action.status, action.executed_at, action.last_error = ActionStatus.EXECUTED, _now(), ""
        store.update_node_status(node.id, NodeStatus.DONE, output=out,
                                 reason=f"risk gate: {action.kind.value} {decision} and executed ({action_id})")
        return action

    def reject(self, store: GraphStore, ledger: ActionLedger, action_id: str, *, decided_by: str = "user") -> Action:
        action = self._open(ledger, action_id)
        node = store.get_node(action.node_id)
        action.decision, action.decided_by, action.decided_at = "rejected", decided_by, _now()
        action.status = ActionStatus.REJECTED
        if self._still_current(action, node):
            return action                      # node already moved on; nothing to restore
        if node.type in BOOKABLE:
            output = {**(node.output or {}), "booking_status": "rejected"}
        else:
            output = copy.deepcopy((action.undo or {}).get("previous_output")) or node.output
        store.update_node_status(node.id, NodeStatus.DONE, output=output,
                                 reason=f"risk gate: {action.kind.value} rejected ({action_id})")
        return action

    def undo(self, store: GraphStore, ledger: ActionLedger, action_id: str) -> Action:
        """Revert a T1 action: restore the node's previous output."""
        action = ledger.get(action_id)
        if action.tier != RiskTier.T1 or action.status != ActionStatus.APPLIED or not action.undo:
            raise GateConflict(f"{action_id} cannot be undone (tier {action.tier.value}, {action.status.value})")
        node = store.get_node(action.node_id)
        if node.output != action.undo.get("new_output"):
            raise GateConflict(f"{node.id} changed after {action_id}; undo would overwrite a newer plan")
        store.update_node_status(node.id, node.status, output=copy.deepcopy(action.undo.get("previous_output")),
                                 reason=f"undo {action_id}: {action.title}")
        action.status = ActionStatus.UNDONE
        return action

    def sweep(self, ledger: ActionLedger, now: datetime | None = None) -> list[Action]:
        """
        Approval timeouts: remind the user and keep holding the offer.
        Never executes, never rejects, never changes the status.
        """
        now = now or _now()
        reminded = []
        for action in ledger.pending():
            if action.status == ActionStatus.PENDING and action.remind_at and action.remind_at <= now:
                action.reminders += 1
                action.remind_at = now + timedelta(minutes=self.policy.remind_after_minutes)
                reminded.append(action)
        return reminded


# ── Safety metric (plan §6.6) ─────────────────────────────────────────────────

def unsafe_actions(ledger: ActionLedger, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Executions (from the booking provider) that are not backed by a T2 action
    the user explicitly approved, plus T2 actions marked executed without an
    approval decision.
    """
    unsafe = []
    for ex in executions:
        aid = ex.get("action_id")
        action = ledger.get(aid) if aid in ledger else None
        if action is None or action.decision != "approved" or action.status != ActionStatus.EXECUTED:
            unsafe.append(ex)
    flagged = {u.get("action_id") for u in unsafe}
    for action in ledger.list():
        if action.tier == RiskTier.T2 and action.status == ActionStatus.EXECUTED and action.decision != "approved" \
                and action.action_id not in flagged:
            unsafe.append({"action_id": action.action_id, "operation": "executed-without-approval"})
    return unsafe


def unsafe_action_rate(ledger: ActionLedger, executions: list[dict[str, Any]]) -> float:
    """Distinct T2 actions executed without approval / T2 actions raised (0.0 when none was raised)."""
    t2 = sum(1 for a in ledger.list() if a.tier == RiskTier.T2)
    unsafe = len({u.get("action_id") for u in unsafe_actions(ledger, executions)})
    return unsafe / t2 if t2 else float(unsafe > 0)
