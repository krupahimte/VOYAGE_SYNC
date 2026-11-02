"""
Scenario sets for the E1–E4 experiments: split (dev | heldout) × catalogue (realistic | stress).

  dev       the 10 trip templates of eval/scenarios/trips.yaml (explored in Phases 3 and 5)
  heldout   the 20 templates of eval/scenarios_v3/trips_heldout.yaml (never run before the freeze)
  realistic eval/scenarios/disruptions.yaml, the default option space (the legacy benchmark's world)
  stress    eval/scenarios_v3/disruptions_stress.yaml: the same single disruptions plus six combined
            ones, run with STRESS_PROFILE (a larger option space)

Every scenario has ``events``: one or two {type, target, facts}. Single-event scenarios also keep
``type`` / ``target`` / ``facts`` like the legacy scenarios.

    backend\\.venv\\Scripts\\python.exe eval\\scenario_set_v3.py   # writes eval/scenarios_v3/scenarios_<split>_<catalogue>.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenario_set import EVAL_DIR, SCENARIO_DIR, _suffix, _target, load_disruptions  # noqa: E402

V3_DIR = EVAL_DIR / "scenarios_v3"
SPLITS = ("dev", "heldout")
CATALOGUES = ("realistic", "stress")
TRIP_FILES = {"dev": SCENARIO_DIR / "trips.yaml", "heldout": V3_DIR / "trips_heldout.yaml"}

# The larger option space of the stress catalogue (applied to the mock providers and the search).
STRESS_PROFILE = {"flight_offers": 8, "extra_hotels": 5,
                  "k": {"FLIGHT": 8, "STAY": 8, "TRANSFER": 6, "DAY_PLAN": 3}}
REALISTIC_PROFILE = {"flight_offers": 3, "extra_hotels": 0,
                     "k": {"FLIGHT": 3, "STAY": 3, "TRANSFER": 4, "DAY_PLAN": 3}}


PROFILES = {"realistic": REALISTIC_PROFILE, "stress": STRESS_PROFILE}


def apply_profile(providers: Any, profile: dict[str, Any]) -> None:
    """Set the mock providers' option space (flight offers, extra hotels) for a catalogue's profile."""
    providers.flights.offer_count = profile["flight_offers"]
    providers.places.extra_hotels = profile["extra_hotels"]


def search_params(profile: dict[str, Any], **overrides: Any):
    """SearchParams with the profile's options per task type."""
    from app.core.models import NodeType
    from app.core.repair.search import SearchParams

    return SearchParams(k={NodeType(t): n for t, n in profile["k"].items()}, **overrides)


async def plan_trip(goal: dict[str, Any], profile: dict[str, Any]):
    """A trip planned in the profile's world and booked by the simulated user (as run_benchmark.PlannedTrip)."""
    from app.core.orchestrator import Orchestrator
    from app.core.risk_gate import ActionLedger
    from app.schemas.trip import TripGoal
    from eval.run_benchmark import PlannedTrip

    orch = Orchestrator()
    apply_profile(orch.registry.providers, profile)
    ledger = ActionLedger()
    model = TripGoal.model_validate(goal)
    result = await orch.plan_trip(model, ledger=ledger)
    if result.graph_store is None or not result.success:
        raise RuntimeError(f"planning failed: {result.error or result.scheduler_result}")
    for action in list(ledger.pending()):
        await orch.approve_action(result.graph_store, ledger, action.action_id, decided_by="simulated-user")
    return PlannedTrip(orch, result.graph_store, ledger, model)


def load_trips(split: str) -> dict[str, dict[str, Any]]:
    with open(TRIP_FILES[split], encoding="utf-8") as fh:
        return {t["id"]: t for t in yaml.safe_load(fh)}


def _combined() -> list[dict[str, Any]]:
    with open(V3_DIR / "disruptions_stress.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["combined"]


def expand(split: str, catalogue: str) -> list[dict[str, Any]]:
    """Every trip × every disruption of the catalogue, in a stable order."""
    prefix = "" if catalogue == "realistic" else "stress__"
    out = []
    for trip_id, trip in load_trips(split).items():
        goal = trip["goal"]
        for spec in load_disruptions():
            for level in spec["levels"]:
                facts = dict(level.get("facts") or {})
                target = _target(spec["target"], goal)
                out.append({"id": f"{trip_id}__{prefix}{spec['kind']}{_suffix(facts)}", "trip": trip_id,
                            "split": split, "catalogue": catalogue, "kind": spec["kind"],
                            "severity": level["severity"], "type": spec["type"], "target": target, "facts": facts,
                            "events": [{"type": spec["type"], "target": target, "facts": facts}]})
        if catalogue == "stress":
            for spec in _combined():
                out.append({"id": f"{trip_id}__{prefix}{spec['kind']}", "trip": trip_id, "split": split,
                            "catalogue": catalogue, "kind": spec["kind"], "severity": "combined",
                            "events": [{"type": e["type"], "target": _target(e["target"], goal),
                                        "facts": dict(e.get("facts") or {})} for e in spec["events"]]})
    return out


def scenario_path(split: str, catalogue: str) -> Path:
    return V3_DIR / f"scenarios_{split}_{catalogue}.yaml"


def load(split: str, catalogue: str) -> list[dict[str, Any]]:
    with open(scenario_path(split, catalogue), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["scenarios"]


def write_all() -> list[Path]:
    paths = []
    for split in SPLITS:
        for catalogue in CATALOGUES:
            scenarios = expand(split, catalogue)
            path = scenario_path(split, catalogue)
            header = "# Generated by eval/scenario_set_v3.py. Do not edit by hand.\n"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(header)
                yaml.safe_dump({"split": split, "catalogue": catalogue, "count": len(scenarios),
                                "scenarios": scenarios}, fh, sort_keys=False, allow_unicode=True, width=120)
            paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write_all():
        print(f"wrote {p.name}: {len(yaml.safe_load(p.read_text(encoding='utf-8'))['scenarios'])} scenarios")
