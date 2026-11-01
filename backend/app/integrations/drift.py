"""
Seeded market drift for the mock providers (evaluation only).

Real APIs drift between searches: fares and room rates move, offers sell out,
listings come and go. Deterministic mocks never do, which makes a full
replan (B1) look unrealistically stable (PROGRESS.md, open question 10).

When enabled, the mock flights, places and booking providers apply:
  - a price drift of up to ±``price_pct`` per offer / quote, and
  - unavailability of each offer / place with probability ``drop_pct``
    (at least one candidate always remains).

Every draw is a hash of (seed, item key), so the same item drifts the same way
within one re-plan and across strategies run with the same seed: every
strategy faces exactly the same market. Drift is off by default, never used by
the app or the API, and does not apply to live providers (they drift on their
own).
"""

from __future__ import annotations

import hashlib
from typing import Sequence, TypeVar

T = TypeVar("T")


class Drift:
    def __init__(self) -> None:
        self.seed: str | None = None
        self.price_pct = 0.0
        self.drop_pct = 0.0

    @property
    def active(self) -> bool:
        return self.seed is not None

    def enable(self, seed: str, *, price_pct: float = 0.08, drop_pct: float = 0.15) -> None:
        self.seed, self.price_pct, self.drop_pct = str(seed), price_pct, drop_pct

    def disable(self) -> None:
        self.seed = None

    def _unit(self, *parts: object) -> float:
        digest = hashlib.sha256("|".join(map(str, (self.seed, *parts))).encode()).hexdigest()
        return int(digest[:12], 16) / float(16 ** 12)

    def price(self, key: str, amount: float) -> float:
        if not self.active or not self.price_pct:
            return amount
        return amount * (1 + (2 * self._unit("price", key) - 1) * self.price_pct)

    def available(self, key: str) -> bool:
        return not self.active or self._unit("drop", key) >= self.drop_pct

    def filter(self, items: Sequence[T], key_of) -> list[T]:
        """Drop unavailable items, keeping at least the first one."""
        if not self.active or not items:
            return list(items)
        kept = [item for item in items if self.available(key_of(item))]
        return kept or [items[0]]
