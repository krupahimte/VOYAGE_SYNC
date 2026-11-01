"""
Shared HTTP helper, provider error type and API-usage meter.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """A provider call failed (network, HTTP status, unusable payload)."""


class UsageMeter:
    """
    Counts provider calls (live and mock alike) — the "API calls" part of the
    cost-of-replan metric (plan §6.6).
    """

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def record(self, provider: str) -> None:
        self._counts[provider] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    @staticmethod
    def diff(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
        return {k: v - before.get(k, 0) for k, v in after.items() if v - before.get(k, 0)}


class HttpJson:
    """
    Minimal JSON-over-HTTP client. ``client`` may be injected (tests pass an
    httpx.AsyncClient with a MockTransport so no network is used).
    """

    def __init__(self, *, timeout_s: float = 20.0, client: httpx.AsyncClient | None = None) -> None:
        self._timeout_s = timeout_s
        self._client = client

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            if self._client is not None:
                resp = await self._client.request(method, url, params=params, json=json, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    resp = await client.request(method, url, params=params, json=json, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{url} returned {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"{url} request failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"{url} returned invalid JSON: {exc}") from exc

    async def get(self, url: str, **kwargs: Any) -> Any:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        return await self.request("POST", url, **kwargs)


def reason(exc: Exception) -> str:
    """Short human-readable reason for a rejected item (Pydantic-aware)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'item'}: {e['msg']}" for e in errors()[:3])[:200]
        except Exception:  # noqa: BLE001
            pass
    return str(exc).splitlines()[0][:200]
