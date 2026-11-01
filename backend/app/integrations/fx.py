"""
Currency conversion.

Live: Frankfurter (ECB reference rates, no key).
Mock: the recorded Frankfurter response ``frankfurter_latest_USD``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from app.integrations.fixtures import load_fixture
from app.integrations.http import HttpJson, ProviderError, UsageMeter

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"


class FxRates(BaseModel):
    base: str
    date: str
    rates: dict[str, float]
    source: str = ""

    @field_validator("rates")
    @classmethod
    def positive(cls, v: dict[str, float]) -> dict[str, float]:
        bad = [k for k, r in v.items() if not r or r <= 0]
        if bad:
            raise ValueError(f"non-positive rates for {bad}")
        return v

    def rate(self, currency: str) -> float:
        currency = currency.upper()
        if currency == self.base:
            return 1.0
        if currency not in self.rates:
            raise ProviderError(f"No exchange rate for {currency}.")
        return self.rates[currency]

    def convert(self, amount: float, from_ccy: str, to_ccy: str) -> float:
        if from_ccy.upper() == to_ccy.upper():
            return round(amount, 2)
        return round(amount / self.rate(from_ccy) * self.rate(to_ccy), 2)


def parse_rates(raw: Any, *, source: str) -> FxRates:
    try:
        return FxRates(base=raw["base"], date=raw["date"], rates=raw["rates"], source=source)
    except (KeyError, TypeError, ValidationError) as exc:
        raise ProviderError(f"Invalid Frankfurter response: {exc}") from exc


class _FxBase:
    name = "fx"

    async def rates(self) -> FxRates:  # pragma: no cover - interface
        raise NotImplementedError

    async def convert(self, amount: float, from_ccy: str, to_ccy: str) -> float:
        return (await self.rates()).convert(amount, from_ccy, to_ccy)


class FrankfurterFX(_FxBase):
    name = "frankfurter"

    def __init__(self, http: HttpJson, usage: UsageMeter) -> None:
        self._http = http
        self._usage = usage
        self._cached: FxRates | None = None
        self._lock = asyncio.Lock()

    async def rates(self) -> FxRates:
        async with self._lock:            # concurrent agents share a single fetch
            if self._cached is None:
                self._usage.record(self.name)
                raw = await self._http.get(FRANKFURTER_URL, params={"base": "USD"})
                self._cached = parse_rates(raw, source="frankfurter")
        return self._cached


class RecordedFX(_FxBase):
    name = "mock_fx"

    def __init__(self, usage: UsageMeter) -> None:
        self._usage = usage
        self._cached = parse_rates(load_fixture("frankfurter_latest_USD"), source="recorded:frankfurter")
        self._fetched = False

    async def rates(self) -> FxRates:
        if not self._fetched:            # counted like the live client: one fetch, then cached
            self._usage.record(self.name)
            self._fetched = True
        return self._cached
