"""
Our own mock hotel booking provider (plan §7.1: listings API + mock booking).

Quotes are deterministic per hotel, style and dates.

Booking (Phase 6) is our sandbox: hotel stays and flights are "booked" here
and get a confirmation reference. Every booking or cancellation needs a
signed, single-use ``ApprovalGrant``, which only the Risk Gate issues after
the user approves the action; without one the call raises
``UnapprovedActionError`` and nothing is recorded. Every execution is kept in
``executions`` so tests and the evaluation can audit the unsafe-action rate.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.integrations.http import UsageMeter

NIGHTLY_USD = {"budget": 70, "balanced": 130, "comfort": 210, "luxury": 360}


class HotelQuote(BaseModel):
    quote_id: str
    hotel_id: str
    check_in: date
    check_out: date
    nights: int = Field(ge=1)
    rooms: int = Field(ge=1)
    nightly_usd: float = Field(gt=0)
    total_usd: float = Field(gt=0)
    refundable: bool
    source: str = "voyagesync-mock-booking"

    @model_validator(mode="after")
    def consistent(self) -> "HotelQuote":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if abs(self.nightly_usd * self.nights - self.total_usd) > 0.05:
            raise ValueError("total does not match nightly × nights")
        return self


class MockBookingProvider:
    name = "mock_booking"

    def __init__(self, usage: UsageMeter, drift: Any = None) -> None:
        self._usage = usage
        self.executions: list[dict[str, Any]] = []
        self.drift = drift                  # evaluation only (app.integrations.drift)

    async def quote(self, hotel_id: str, check_in: date, check_out: date, rooms: int, style: str) -> HotelQuote:
        self._usage.record(self.name)
        h = int(hashlib.sha256(f"{hotel_id}|{style}".encode()).hexdigest()[:8], 16)
        factor = 0.9 + (h % 21) / 100                       # 0.90 – 1.10
        nightly = NIGHTLY_USD.get(style, 130) * rooms * factor
        if self.drift is not None:
            nightly = self.drift.price(f"{hotel_id}|{check_in.isoformat()}", nightly)
        nightly = round(nightly, 2)
        nights = (check_out - check_in).days
        return HotelQuote(
            quote_id=f"hq_{h:08x}_{check_in.isoformat()}",
            hotel_id=hotel_id, check_in=check_in, check_out=check_out, nights=nights, rooms=rooms,
            nightly_usd=nightly, total_usd=round(nightly * nights, 2), refundable=h % 3 != 0,
        )

    # ── Sandbox booking (Risk Gate approval required) ─────────────────────────

    async def book_flight(self, offer: dict[str, Any], grant: Any) -> dict[str, Any]:
        return self._book("flight", offer, grant)

    async def book_hotel(self, quote: dict[str, Any], grant: Any) -> dict[str, Any]:
        return self._book("hotel", quote, grant)

    async def cancel_booking(self, booking_ref: str, grant: Any) -> dict[str, Any]:
        """Cancel an earlier booking as part of an approved change (same grant, not consumed again)."""
        from app.core.risk_gate import verify_grant

        verify_grant(grant, consume=False)
        self._usage.record(self.name)
        record = {"operation": "cancel", "booking_ref": booking_ref, "action_id": grant.action_id,
                  "node_id": grant.node_id, "at": datetime.now(timezone.utc).isoformat(),
                  "provider": "voyagesync-sandbox"}
        self.executions.append(record)
        return record

    def _book(self, kind: str, item: dict[str, Any], grant: Any) -> dict[str, Any]:
        from app.core.risk_gate import verify_grant

        offer_id = item.get("offer_id")
        verify_grant(grant, offer_id=offer_id)
        self._usage.record(self.name)
        ref = "BK" + hashlib.sha256(f"{grant.action_id}|{offer_id}".encode()).hexdigest()[:8].upper()
        record = {"operation": "book", "kind": kind, "booking_ref": ref, "offer_id": offer_id,
                  "amount": grant.amount, "currency": item.get("currency"), "action_id": grant.action_id,
                  "node_id": grant.node_id, "booked_at": datetime.now(timezone.utc).isoformat(),
                  "provider": "voyagesync-sandbox"}
        self.executions.append(record)
        return record
