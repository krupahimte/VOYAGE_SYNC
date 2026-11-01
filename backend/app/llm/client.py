"""
Provider-agnostic LLM client.

  - ``LLMClient``           — the interface the Orchestrator talks to.
  - ``MockLLM``             — deterministic, offline; used by default and in tests.
  - ``OpenAICompatibleLLM`` — one live provider: any OpenAI-compatible
                              chat-completions endpoint (OpenAI, Gemini's
                              OpenAI endpoint, Groq, …), chosen via env.
  - ``get_llm_client()``    — picks one from settings. Live mode without a key
                              logs a warning and falls back to MockLLM.

The LLM only ever returns text.  Callers parse and validate it; nothing the
LLM produces is executed.
"""

from __future__ import annotations

import abc
import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

ChatMessage = dict[str, str]   # {"role": "system"|"user"|"assistant", "content": str}


# ── Errors and results ────────────────────────────────────────────────────────

class LLMError(Exception):
    """Raised when the LLM call itself fails (transport, HTTP, empty reply)."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


# ── Interface ─────────────────────────────────────────────────────────────────

class LLMClient(abc.ABC):
    """Every provider implements ``complete``; usage counters feed cost metrics."""

    provider: str = "base"
    model: str = ""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def complete(self, messages: Sequence[ChatMessage], *, json_mode: bool = True) -> LLMResponse:
        self.calls += 1
        response = await self._complete(list(messages), json_mode=json_mode)
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens
        return response

    @abc.abstractmethod
    async def _complete(self, messages: list[ChatMessage], *, json_mode: bool) -> LLMResponse:
        """Provider-specific call."""


# ── Mock provider ─────────────────────────────────────────────────────────────

_TRIP_GOAL_TAG = re.compile(r"<trip_goal>\s*(\{.*?\})\s*</trip_goal>", re.DOTALL)
_DAY_PLAN_TAG = re.compile(r"<day_plan_request>\s*(\{.*?\})\s*</day_plan_request>", re.DOTALL)


def _default_mock_handler(messages: list[ChatMessage]) -> str:
    """
    Answer deterministically:
      - a decomposition request (tagged trip goal) → the template task proposal;
      - a day-plan description request → one templated sentence per activity.
    """
    from app.core.templates import propose_template_tasks
    from app.schemas.trip import TripGoal

    for msg in reversed(messages):
        day_match = _DAY_PLAN_TAG.search(msg.get("content", ""))
        if day_match:
            request = json.loads(day_match.group(1))
            city = request.get("city", "the city")
            return json.dumps({"descriptions": {
                a["name"]: (f"{'Outdoor' if a.get('outdoor') else 'Indoor'} {a.get('slot', 'stop')} "
                            f"at {a['time']}: {a['name']}, close to your hotel in {city}.")
                for a in request.get("activities", [])
            }})
        match = _TRIP_GOAL_TAG.search(msg.get("content", ""))
        if match:
            goal = TripGoal.model_validate_json(match.group(1))
            return propose_template_tasks(goal).model_dump_json()
    return "{}"


class MockLLM(LLMClient):
    """
    Deterministic offline LLM.

    Parameters:
        responses: scripted replies returned in order (an Exception instance is
                   raised instead of returned). Once exhausted, ``handler`` answers.
        handler:   function(messages) -> str; defaults to the template planner.
    """

    provider = "mock"
    model = "mock-llm"

    def __init__(
        self,
        responses: Sequence[str | Exception] | None = None,
        handler: Callable[[list[ChatMessage]], str] | None = None,
    ) -> None:
        super().__init__()
        self._script = list(responses or [])
        self._handler = handler or _default_mock_handler
        self.received: list[list[ChatMessage]] = []

    async def _complete(self, messages: list[ChatMessage], *, json_mode: bool) -> LLMResponse:
        self.received.append([dict(m) for m in messages])
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            text = item
        else:
            text = self._handler(messages)
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            prompt_tokens=prompt_chars // 4,
            completion_tokens=len(text) // 4,
        )


# ── Live provider: OpenAI-compatible chat completions ────────────────────────

class OpenAICompatibleLLM(LLMClient):
    provider = "openai_compatible"

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout_s: float = 60.0) -> None:
        super().__init__()
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def _complete(self, messages: list[ChatMessage], *, json_mode: bool) -> LLMResponse:
        import httpx

        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"LLM API returned {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.RequestError as exc:
            raise LLMError(f"LLM API request failed: {exc}") from exc

        try:
            body = resp.json()
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected LLM API response shape: {exc}") from exc
        if not text.strip():
            raise LLMError("LLM returned an empty reply.")
        usage = body.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_json_object(text: str) -> str:
    """
    Pull the JSON object out of an LLM reply that may be wrapped in markdown
    fences or prose. Raises ValueError if no object is present.
    """
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Reply does not contain a JSON object.")
    candidate = stripped[start:end + 1]
    json.loads(candidate)  # raises ValueError (JSONDecodeError) if malformed
    return candidate


def get_llm_client(settings=None) -> LLMClient:
    """Return the configured LLM client (mock unless live mode has a key)."""
    from app.config import get_settings

    settings = settings or get_settings()
    if settings.is_mock:
        return MockLLM()
    if not settings.voyagesync_llm_api_key:
        logger.warning("VOYAGESYNC_MODE=live but VOYAGESYNC_LLM_API_KEY is empty — using MockLLM.")
        return MockLLM()
    return OpenAICompatibleLLM(
        api_key=settings.voyagesync_llm_api_key,
        model=settings.voyagesync_llm_model,
        base_url=settings.voyagesync_llm_base_url,
        timeout_s=settings.voyagesync_llm_timeout_s,
    )
