"""
Centralized application configuration loaded from environment variables.
All settings have safe defaults so the app starts without any .env file.

Mode switch
-----------
``VOYAGESYNC_MODE=mock`` (default) uses deterministic mocks for the LLM and
every external API, so the test suite and the demo run with no keys and no
network.  ``VOYAGESYNC_MODE=live`` enables real providers; any provider whose
key is missing logs a warning and falls back to its mock.
"""

import logging
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "VoyageSync"
    app_version: str = "0.3.0"
    debug: bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./voyagesync.db"

    # ── Mode (mock | live) ────────────────────────────────────────────────────
    voyagesync_mode: Literal["mock", "live"] = "mock"

    # ── LLM (OpenAI-compatible chat completions API) ──────────────────────────
    voyagesync_llm_api_key: str = ""
    voyagesync_llm_model: str = "gpt-4o-mini"
    voyagesync_llm_base_url: str = "https://api.openai.com/v1"
    voyagesync_llm_timeout_s: float = 60.0
    # Decomposition attempts before falling back to the template graph
    voyagesync_llm_max_attempts: int = 3

    # ── External APIs (Phase 4) ───────────────────────────────────────────────
    # Keyless in live mode: Open-Meteo (weather + geocoding), Frankfurter (FX), OSRM (routing).
    duffel_access_token: str = ""      # Duffel test-mode token (duffel_test_…)
    geoapify_api_key: str = ""         # Geoapify Places
    voyagesync_http_timeout_s: float = 20.0

    # ── Risk Gate (Phase 6) ───────────────────────────────────────────────────
    # Spend above this (trip currency) is named in the T2 reasons and saturates the
    # cost term of the risk score. Any spend at all is T2 regardless.
    voyagesync_risk_spend_threshold: float = 250.0
    # An unanswered approval is held and the user is reminded after this many minutes.
    voyagesync_approval_remind_minutes: int = 30

    # ── CORS origins (comma-separated) ────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    model_config = SettingsConfigDict(
        # Repo-root .env first, then backend/.env (later files win)
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_mock(self) -> bool:
        return self.voyagesync_mode == "mock"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton pattern)."""
    return Settings()
