"""
Shared test setup: force mock mode so the suite never needs keys or network,
even when a developer's .env selects live mode.
"""

import os

os.environ["VOYAGESYNC_MODE"] = "mock"
os.environ["VOYAGESYNC_LLM_API_KEY"] = ""

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
os.environ["DUFFEL_ACCESS_TOKEN"] = ""
os.environ["GEOAPIFY_API_KEY"] = ""
get_settings.cache_clear()


# ── No network in tests ───────────────────────────────────────────────────────
# Any real HTTP request fails loudly; tests use httpx.MockTransport instead.
import httpx  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    async def blocked(self, request):
        raise RuntimeError(f"Network access in tests is not allowed: {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, request: (_ for _ in ()).throw(
                            RuntimeError(f"Network access in tests is not allowed: {request.url}")))
