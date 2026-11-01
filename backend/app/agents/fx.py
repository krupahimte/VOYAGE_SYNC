"""
Fixed mock exchange rates (USD base) used by mock agents.

Phase 4 replaces these with live Frankfurter rates behind the same helper.
"""

from __future__ import annotations

MOCK_USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "INR": 83.0,
    "JPY": 150.0,
    "AUD": 1.52,
    "CAD": 1.36,
}


def convert_from_usd(amount_usd: float, currency: str) -> float:
    """Convert a USD amount to *currency* (unknown currencies stay in USD)."""
    rate = MOCK_USD_RATES.get(currency.upper(), 1.0)
    return round(amount_usd * rate, 2)
