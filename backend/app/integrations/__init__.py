"""
External data providers behind thin, swappable interfaces (Phase 4).

Each provider module has:
  - request builders + a ``parse_*`` function that validates the raw API
    response into Pydantic models (invalid items are rejected, not passed on);
  - a live client (real HTTP) and a mock client (recorded fixtures or
    deterministic synthetic data in the same raw format, run through the
    same parser).

Agents only see the normalised models.
"""
