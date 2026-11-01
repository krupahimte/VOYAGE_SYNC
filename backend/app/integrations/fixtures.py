"""
Recorded API responses used by mock providers and tests.

Files live in ``app/integrations/fixtures/``. Real recordings come from
``scripts/record_fixtures.py``; files whose top level has ``"_synthetic": true``
were written by hand in the provider's documented format because no API key
was available when they were made.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json"


def has_fixture(name: str) -> bool:
    return fixture_path(name).is_file()


@lru_cache(maxsize=128)
def _load_text(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")


def load_fixture(name: str) -> Any:
    """Return a fresh copy of a fixture's JSON (callers may mutate it)."""
    return json.loads(_load_text(name))


def save_fixture(name: str, data: Any) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = fixture_path(name)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _load_text.cache_clear()
    return path
