"""
Phase 6 — Risk Gate core: tiers, approvals, T1 undo, timeouts, and the
unsafe-action rate (= 0) under every path: timeouts, retries, re-plans with
each strategy (ours, B1, B2), stale and double approvals, rejection, and
direct calls to the booking provider.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.models import NodeStatus, NodeType, RiskTier
from app.core.monitor.simulator import DisruptionSimulator
from app.core.orchestrator import Orchestrator, check_graph_contracts
from app.core.risk_gate import (
    Action,
    ActionKind,
    ActionLedger,
    ActionStatus,
    ApprovalGrant,
    GateConflict,
    GateExecutionError,
    RiskGate,
    RiskPolicy,
    UnapprovedActionError,
    classify,
    risk_score,
    unsafe_action_rate,
    unsafe_actions,
)
from app.integrations.http import ProviderError
from app.schemas.trip import TripGoal

GOAL = TripGoal(destination="Santorini", departure_city="Mumbai", departure_date=date(2027, 6, 14),
                return_date=date(2027, 6, 19), travelers=1, budget=4000, currency="USD", travel_style="luxury",
                interests=["relaxation", "food"])
BOOKABLE = ("flight_outbound", "flight_return", "hotel_stay")
STRATEGIES = ("contract", "full", "naive")


class Trip:
    def __init__(self, orch: Orchestrator, store, ledger: ActionLedger) -> None:
        self.orch, self.store, self.ledger = orch, store, ledger
        self.sim = DisruptionSimulator(0)

    @property
    def executions(self) -> list[dict]:
        return self.orch.registry.providers.booking.executions

    def pending(self, node_id: str) -> Action:
        action = self.ledger.pending_for(node_id)
        assert action is not None, f"no pending action for {node_id}"
        return action

    async def approve_all(self) -> None:
        for action in list(self.ledger.pending()):
            await self.orch.approve_action(self.store, self.ledger, action.action_id)

    async def disrupt(self, scenario: str, strategy: str = "contract"):
        return await self.orch.handle_disruption(self.store, self.sim.preset(scenario), strategy=strategy,
                                                 ledger=self.ledger)

    def assert_safe(self) -> None:
        assert unsafe_actions(self.ledger, self.executions) == []
        assert unsafe_action_rate(self.ledger, self.executions) == 0.0


@pytest.fixture
async def trip() -> Trip:
    orch = Orchestrator()
    ledger = ActionLedger()
    result = await orch.plan_trip(GOAL, ledger=ledger)
    return Trip(orch, result.graph_store, ledger)


def act(kind: ActionKind, node_type: NodeType = NodeType.TRANSFER, **kw) -> Action:
    return Action(node_id="n", node_type=node_type, kind=kind, **kw)


# ── Classification ────────────────────────────────────────────────────────────

class TestClassify:
    policy = RiskPolicy()

    @pytest.mark.parametrize("kind", [ActionKind.BOOK, ActionKind.CHANGE, ActionKind.CANCEL, ActionKind.PAY])
    def test_consequential_kinds_always_t2(self, kind):
        tier, _, reasons = classify(act(kind, NodeType.STAY, refundable=True), self.policy)
        assert tier == RiskTier.T2 and reasons

    def test_cancel_and_pay_t2_even_when_free(self):
        for kind in (ActionKind.CANCEL, ActionKind.PAY):
            assert classify(act(kind, NodeType.WEATHER, amount=0), self.policy)[0] == RiskTier.T2

    def test_hard_rules_override_a_zero_score(self):
        zero = RiskPolicy(w_irreversibility=0, w_cost=0, w_visibility=0, w_uncertainty=0)
        assert risk_score(act(ActionKind.BOOK, amount=10), zero) == 0
        assert classify(act(ActionKind.BOOK, amount=10), zero)[0] == RiskTier.T2

    def test_money_is_never_spent_in_t0_or_t1(self):
        tier, _, reasons = classify(act(ActionKind.RESCHEDULE, amount=5.0, currency="USD"), self.policy)
        assert tier == RiskTier.T2 and any("never spent" in r for r in reasons)

    def test_spend_above_threshold_and_non_refundable_named(self):
        _, _, reasons = classify(act(ActionKind.BOOK, NodeType.FLIGHT, amount=900, refundable=False), self.policy)
        assert any("threshold" in r for r in reasons) and "non-refundable" in reasons

    def test_reschedule_pending_transfer_is_t1(self):
        action = act(ActionKind.RESCHEDULE)
        assert risk_score(action, self.policy) == pytest.approx(0.21)
        assert classify(action, self.policy)[0] == RiskTier.T1

    @pytest.mark.parametrize("node_type,kind,delta", [
        (NodeType.DAY_PLAN, ActionKind.DRAFT, -40.0),
        (NodeType.BUDGET, ActionKind.DRAFT, 0.0),
        (NodeType.WEATHER, ActionKind.SEARCH, 0.0),
    ])
    def test_drafts_and_searches_are_t0(self, node_type, kind, delta):
        assert classify(act(kind, node_type, cost_delta=delta), self.policy)[0] == RiskTier.T0

    def test_score_threshold_can_raise_a_non_spend_action(self):
        strict = RiskPolicy(t2_score=0.2)
        assert classify(act(ActionKind.RESCHEDULE), strict)[0] == RiskTier.T2


# ── Planning proposes the bookings (demo step 2) ──────────────────────────────

class TestBookingProposals:
    async def test_bookings_wait_for_approval(self, trip):
        pending = trip.ledger.pending()
        assert {a.node_id for a in pending} == set(BOOKABLE)
        for action in pending:
            assert action.tier == RiskTier.T2 and action.kind == ActionKind.BOOK
            assert action.amount > 0 and action.currency == "USD" and action.title and action.why
            node = trip.store.get_node(action.node_id)
            assert node.status == NodeStatus.AWAITING_APPROVAL
            assert node.output["booking_status"] == "awaiting_approval"
            assert node.output["approval_id"] == action.action_id
        assert trip.executions == []
        assert all(c.holds for c in check_graph_contracts(trip.store))

    async def test_without_a_ledger_nothing_is_proposed(self):
        result = await Orchestrator().plan_trip(GOAL)
        assert result.actions == []
        assert all(n.status == NodeStatus.DONE for n in result.graph_store.list_nodes())

    async def test_approve_books_once(self, trip):
        action = trip.pending("flight_outbound")
        await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        node = trip.store.get_node("flight_outbound")
        assert node.status == NodeStatus.DONE
        assert node.output["booking_status"] == "booked" and node.output["booking_ref"].startswith("BK")
        assert action.status == ActionStatus.EXECUTED and action.decision == "approved"
        assert [e["action_id"] for e in trip.executions] == [action.action_id]
        trip.assert_safe()

    async def test_reject_never_executes(self, trip):
        action = trip.pending("hotel_stay")
        trip.orch.reject_action(trip.store, trip.ledger, action.action_id)
        node = trip.store.get_node("hotel_stay")
        assert node.status == NodeStatus.DONE and node.output["booking_status"] == "rejected"
        with pytest.raises(GateConflict):
            await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        assert trip.executions == []
        trip.assert_safe()


# ── Unsafe-action rate = 0 ────────────────────────────────────────────────────

class TestNoUnapprovedExecution:
    async def test_timeout_reminds_and_holds_never_executes(self, trip):
        gate = trip.orch.gate
        later = datetime.now(timezone.utc)
        for _ in range(5):
            later += timedelta(days=2)
            reminded = gate.sweep(trip.ledger, now=later)
            assert {a.node_id for a in reminded} == set(BOOKABLE)
        for action in trip.ledger.pending():
            assert action.status == ActionStatus.PENDING and action.reminders == 5
            assert trip.store.get_node(action.node_id).status == NodeStatus.AWAITING_APPROVAL
        assert trip.executions == []
        trip.assert_safe()

    async def test_double_approval_executes_once(self, trip):
        action = trip.pending("flight_return")
        await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        with pytest.raises(GateConflict):
            await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        assert len(trip.executions) == 1
        trip.assert_safe()

    async def test_concurrent_approvals_execute_once(self, trip):
        action = trip.pending("flight_outbound")
        results = await asyncio.gather(
            *(trip.orch.approve_action(trip.store, trip.ledger, action.action_id) for _ in range(3)),
            return_exceptions=True)
        assert sum(isinstance(r, GateConflict) for r in results) == 2
        assert len(trip.executions) == 1
        trip.assert_safe()

    async def test_failed_execution_stays_pending_then_retry_needs_approval(self, trip, monitor_booking_failure):
        action = trip.pending("hotel_stay")
        monitor_booking_failure(trip, times=1)
        with pytest.raises(GateExecutionError):
            await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        assert action.status == ActionStatus.PENDING and "execution failed" in action.last_error
        assert action.decision is None
        assert trip.store.get_node("hotel_stay").status == NodeStatus.AWAITING_APPROVAL
        assert trip.executions == []
        # Nothing retries on its own; the user approves again.
        await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        assert len(trip.executions) == 1
        trip.assert_safe()

    async def test_no_executor_means_no_execution(self, trip):
        action = trip.pending("flight_outbound")
        with pytest.raises(GateExecutionError):
            await trip.orch.gate.approve(trip.store, trip.ledger, action.action_id, None)
        assert action.status == ActionStatus.PENDING and trip.executions == []

    async def test_price_change_before_approval_supersedes(self, trip):
        action = trip.pending("hotel_stay")
        node = trip.store.get_node("hotel_stay")
        trip.store.update_node_status("hotel_stay", node.status, output={**node.output, "cost": node.output["cost"] + 50})
        with pytest.raises(GateConflict):
            await trip.orch.approve_action(trip.store, trip.ledger, action.action_id)
        assert action.status == ActionStatus.SUPERSEDED and trip.executions == []

    async def test_price_spike_raises_a_new_card_at_the_new_price(self, trip):
        old = trip.pending("hotel_stay")
        out = await trip.disrupt("price_spike_hotel")
        assert old.status == ActionStatus.SUPERSEDED
        new = trip.pending("hotel_stay")
        assert new.amount == pytest.approx(trip.store.get_node("hotel_stay").output["cost"])
        assert new.amount > old.amount and new.action_id in {a.action_id for a in out.actions}
        with pytest.raises(GateConflict):
            await trip.orch.approve_action(trip.store, trip.ledger, old.action_id)
        assert trip.executions == []

    @pytest.mark.parametrize("strategy", STRATEGIES)
    async def test_cancellation_before_approval(self, trip, strategy):
        old = trip.pending("flight_outbound")
        out = await trip.disrupt("cancel_outbound", strategy)
        assert out.status == "replanned"
        assert old.status == ActionStatus.SUPERSEDED
        new = trip.pending("flight_outbound")
        assert new.kind == ActionKind.BOOK and new.offer_id != old.offer_id
        assert trip.store.get_node("flight_outbound").status == NodeStatus.AWAITING_APPROVAL
        with pytest.raises(GateConflict):
            await trip.orch.approve_action(trip.store, trip.ledger, old.action_id)
        assert trip.executions == []
        trip.assert_safe()

    @pytest.mark.parametrize("strategy", STRATEGIES)
    async def test_rebooking_after_cancellation_needs_approval(self, trip, strategy):
        await trip.approve_all()
        booked_ref = trip.store.get_node("flight_outbound").output["booking_ref"]
        assert len(trip.executions) == 3

        out = await trip.disrupt("cancel_outbound", strategy)
        change = trip.pending("flight_outbound")
        assert change.tier == RiskTier.T2 and change.kind == ActionKind.CHANGE
        assert change.previous_booking_ref == booked_ref
        assert change.old_amount is not None and change.price_difference == pytest.approx(
            change.amount - change.old_amount)
        assert {c["field"] for c in change.changes} >= {"flight_number"}
        assert change.why and change.event_id == out.event.event_id
        assert trip.store.get_node("flight_outbound").status == NodeStatus.AWAITING_APPROVAL
        assert len(trip.executions) == 3                     # nothing re-booked yet
        trip.assert_safe()

        await trip.orch.approve_action(trip.store, trip.ledger, change.action_id)
        ops = [(e["operation"], e["action_id"]) for e in trip.executions[3:]]
        assert ops == [("book", change.action_id), ("cancel", change.action_id)]
        assert trip.store.get_node("flight_outbound").output["booking_ref"] != booked_ref
        trip.assert_safe()

    async def test_full_replan_keeps_bookings_without_new_approvals(self, trip):
        await trip.approve_all()
        refs = {nid: trip.store.get_node(nid).output["booking_ref"] for nid in BOOKABLE}
        out = await trip.disrupt("delay_5h", "full")
        assert set(out.replan.regenerated) >= {"flight_return", "hotel_stay"}
        assert trip.ledger.pending() == []
        assert {nid: trip.store.get_node(nid).output["booking_ref"] for nid in BOOKABLE} == refs
        assert all(trip.store.get_node(nid).status == NodeStatus.DONE for nid in BOOKABLE)
        assert len(trip.executions) == 3
        trip.assert_safe()

    async def test_provider_refuses_without_a_valid_grant(self, trip):
        booking = trip.orch.registry.providers.booking
        out = trip.store.get_node("flight_outbound").output
        with pytest.raises(UnapprovedActionError):
            await booking.book_flight(out, None)
        forged = ApprovalGrant(action_id="act_x", node_id="flight_outbound", offer_id=out["offer_id"],
                               amount=out["cost"], nonce="n", signature="0" * 64)
        with pytest.raises(UnapprovedActionError):
            await booking.book_flight(out, forged)
        with pytest.raises(UnapprovedActionError):
            await booking.cancel_booking("BK123", forged)
        assert booking.executions == []

    async def test_grant_is_single_use_and_offer_bound(self, trip):
        grants = []
        executor = trip.orch.executor()
        real = executor.execute

        async def capture(action, grant, output):
            grants.append(grant)
            return await real(action, grant, output)

        executor.execute = capture
        action = trip.pending("flight_outbound")
        await trip.orch.gate.approve(trip.store, trip.ledger, action.action_id, executor)
        booking = trip.orch.registry.providers.booking
        with pytest.raises(UnapprovedActionError, match="already used"):
            await booking.book_flight(trip.store.get_node("flight_outbound").output, grants[0])
        with pytest.raises(UnapprovedActionError, match="covers offer"):
            await booking.book_flight(trip.store.get_node("flight_return").output, grants[0])
        assert len(booking.executions) == 1
        trip.assert_safe()

    def test_metric_flags_an_unapproved_execution(self):
        ledger = ActionLedger("t", [act(ActionKind.BOOK, NodeType.FLIGHT, tier=RiskTier.T2,
                                        status=ActionStatus.PENDING, action_id="act_1")])
        rogue = [{"operation": "book", "action_id": "act_1"}]
        assert len(unsafe_actions(ledger, rogue)) == 1 and unsafe_action_rate(ledger, rogue) == 1.0
        assert unsafe_action_rate(ledger, []) == 0.0


# ── Re-plans: T1 notifications with undo, T0 drafts ───────────────────────────

class TestReplanActions:
    async def test_delay_5h_tiers(self, trip):
        await trip.approve_all()
        hotel_version = trip.store.get_node("hotel_stay").version
        out = await trip.disrupt("delay_5h")
        assert out.replan.regenerated == ["airport_transfer_in", "day_1_plan", "budget_check"]
        tiers = {a.node_id: (a.tier, a.kind, a.title) for a in out.actions}
        assert tiers["airport_transfer_in"] == (RiskTier.T1, ActionKind.RESCHEDULE, "Rebooked pickup for 19:30")
        assert tiers["day_1_plan"][0] == RiskTier.T0 and tiers["budget_check"][0] == RiskTier.T0
        assert not any(a.tier == RiskTier.T2 for a in out.actions)
        assert trip.store.get_node("hotel_stay").version == hotel_version
        assert len(trip.executions) == 3
        trip.assert_safe()

    async def test_disruption_on_a_node_awaiting_approval(self, trip):
        out = await trip.disrupt("delay_5h")
        assert out.status == "replanned"
        assert out.replan.regenerated == ["airport_transfer_in", "day_1_plan", "budget_check"]
        assert trip.store.get_node("flight_outbound").status == NodeStatus.AWAITING_APPROVAL
        assert trip.store.get_node("flight_outbound").output["arrival_time"] == "2027-06-14T19:00:00"
        assert trip.executions == []

    async def test_small_delay_has_no_actions(self, trip):
        out = await trip.disrupt("delay_10m")
        assert out.status == "no_effect" and out.actions == []
        assert len(trip.ledger.pending()) == 3

    async def test_undo_restores_previous_transfer(self, trip):
        out = await trip.disrupt("delay_5h")
        t1 = next(a for a in out.actions if a.tier == RiskTier.T1)
        trip.orch.gate.undo(trip.store, trip.ledger, t1.action_id)
        assert t1.status == ActionStatus.UNDONE
        assert trip.store.get_node("airport_transfer_in").output["pickup_time"] == "2027-06-14T14:30:00"
        broken = [(c.source_id, c.destination_id) for c in check_graph_contracts(trip.store) if not c.holds]
        assert ("flight_outbound", "airport_transfer_in") in broken
        with pytest.raises(GateConflict):
            trip.orch.gate.undo(trip.store, trip.ledger, t1.action_id)

    async def test_t0_and_t2_cannot_be_undone(self, trip):
        out = await trip.disrupt("delay_5h")
        t0 = next(a for a in out.actions if a.tier == RiskTier.T0)
        with pytest.raises(GateConflict):
            trip.orch.gate.undo(trip.store, trip.ledger, t0.action_id)
        with pytest.raises(GateConflict):
            trip.orch.gate.undo(trip.store, trip.ledger, trip.pending("hotel_stay").action_id)

    async def test_undo_refused_after_a_newer_change(self, trip):
        out = await trip.disrupt("delay_5h")
        t1 = next(a for a in out.actions if a.tier == RiskTier.T1)
        await trip.disrupt("delay_2h")
        with pytest.raises(GateConflict):
            trip.orch.gate.undo(trip.store, trip.ledger, t1.action_id)

    async def test_hotel_overbooked_after_booking_needs_approval(self, trip):
        await trip.approve_all()
        out = await trip.disrupt("hotel_overbooked")
        change = trip.pending("hotel_stay")
        assert change.kind == ActionKind.CHANGE and change.tier == RiskTier.T2
        assert {"hotel_name"} <= {c["field"] for c in change.changes}
        assert any(a.node_id == "airport_transfer_in" for a in out.actions)
        assert len(trip.executions) == 3
        trip.assert_safe()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def monitor_booking_failure(monkeypatch):
    def install(trip: Trip, times: int) -> None:
        booking = trip.orch.registry.providers.booking
        real = booking.book_hotel
        state = {"left": times}

        async def flaky(quote, grant):
            if state["left"] > 0:
                state["left"] -= 1
                raise ProviderError("sandbox booking unavailable")
            return await real(quote, grant)

        monkeypatch.setattr(booking, "book_hotel", flaky)

    return install
