"""
Action execution (plan §6.5 ``execute_action``): carries out a T2 action the
user approved, through our sandbox booking provider. Only ``RiskGate.approve``
calls this, and it passes the single-use approval grant the provider demands.

Flights are booked in the sandbox too: a Duffel order needs passenger
identity data we do not collect (see PROGRESS.md, open questions).
"""

from __future__ import annotations

from typing import Any

from app.core.models import NodeType
from app.core.risk_gate import Action, ActionKind, ApprovalGrant


class ActionExecutor:
    def __init__(self, providers: Any) -> None:
        self.booking = providers.booking

    async def execute(self, action: Action, grant: ApprovalGrant, output: dict[str, Any]) -> dict[str, Any]:
        if action.node_type == NodeType.FLIGHT:
            record = await self.booking.book_flight(output, grant)
        elif action.node_type == NodeType.STAY:
            record = await self.booking.book_hotel(output, grant)
        else:
            raise ValueError(f"nothing to execute for a {action.node_type.value} node")
        # A change books the new offer first, then releases the old booking.
        if action.kind == ActionKind.CHANGE and action.previous_booking_ref:
            cancelled = await self.booking.cancel_booking(action.previous_booking_ref, grant)
            record = {**record, "cancelled_booking_ref": cancelled["booking_ref"]}
        return record
