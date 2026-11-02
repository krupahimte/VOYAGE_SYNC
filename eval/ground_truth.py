"""
Ground truth v3 for the E1–E4 experiments (pre-registration R1): fixed before any
experiment runs, and independent of which repair any strategy chooses.

For every scenario × drift setting, the exhaustive search enumerates every
complete repair in the option space that meets the hard constraints. Over those
feasible repairs:

  necessity set   tasks in the repair region of *every* feasible repair: they must change,
                  whatever the strategy picks. A task affected in most but not all feasible
                  repairs is avoidable, so it is not in this set.
  min scope       the smallest repair region of any feasible repair (an optimum, not a label)
  union           tasks some feasible repair changes (for reference only)

No feasible repair → ``feasible: false`` and both are null: the scenario is a conflict.
The legacy oracle's set A (drift off) is stored beside it for comparison; it depends
on E1's own repair choices, which is why it is not the v3 ground truth.

The option space is part of the definition, so the file records its parameters and
the sha256 of every source file that defines it. ``load_verified`` refuses to return
the ground truth if the file or any of those sources changed since it was written.

    backend\\.venv\\Scripts\\python.exe eval\\ground_truth.py --write   # eval/scenarios_v3/ground_truth.yaml
    backend\\.venv\\Scripts\\python.exe eval\\ground_truth.py           # recompute and compare (reproducibility)
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenario_set import BACKEND_DIR, EVAL_DIR, load_scenarios, load_trips, use_backend  # noqa: E402

use_backend()

from app.agents.itinerary import ARRIVAL_PICKUP_OFFSETS, DAY_PLAN_VARIANTS, DEPARTURE_BUFFERS  # noqa: E402
from app.core.models import NodeType  # noqa: E402
from app.core.monitor.apply import apply_event  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.repair.constraints import goal_from_graph, validate_plan  # noqa: E402
from app.core.repair.search import RepairSearch, SearchParams, exhaustive  # noqa: E402
from eval.oracle import ground_truth as legacy_oracle  # noqa: E402
from eval import scenario_set_v3 as v3  # noqa: E402
from eval.run_benchmark import DRIFT, PlannedTrip  # noqa: E402

GT_DIR = EVAL_DIR / "scenarios_v3"
GT_PATH = GT_DIR / "ground_truth.yaml"            # dev × realistic (the first ground truth, Phase 5)
SHA_PATH = GT_DIR / "ground_truth.sha256"
PARAMS = SearchParams(max_exhaustive=20000, timeout_s=600)
# The stress spaces can be far larger; a scenario that still exceeds this is recorded as truncated.
STRESS_MAX_EXHAUSTIVE = 100_000
# Source files that define the option space and feasibility; a change to any of them changes the ground truth.
SOURCES = [
    "app/agents/base.py", "app/agents/flight.py", "app/agents/hotel.py", "app/agents/itinerary.py",
    "app/agents/budget.py", "app/agents/weather.py", "app/agents/mocks.py", "app/agents/common.py",
    "app/agents/validation.py", "app/core/contracts.py", "app/core/templates.py", "app/core/monitor/apply.py",
    "app/core/repair/search.py", "app/core/repair/constraints.py", "app/core/repair/view.py",
    "app/integrations/flights.py", "app/integrations/places.py", "app/integrations/booking.py",
    "app/integrations/drift.py", "app/integrations/routing.py", "app/integrations/fx.py",
]


class GroundTruthError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def source_hashes() -> dict[str, str]:
    return {rel: _sha((BACKEND_DIR / rel).read_bytes()) for rel in SOURCES}


def paths(split: str = "dev", catalogue: str = "realistic") -> tuple[Path, Path]:
    """(ground truth file, its sha256 file) for a split × catalogue."""
    if (split, catalogue) == ("dev", "realistic"):
        return GT_PATH, SHA_PATH
    stem = GT_DIR / f"ground_truth_{split}_{catalogue}"
    return stem.with_suffix(".yaml"), stem.with_suffix(".sha256")


def params_for(catalogue: str) -> SearchParams:
    if catalogue == "realistic":
        return PARAMS
    return v3.search_params(v3.PROFILES[catalogue], max_exhaustive=STRESS_MAX_EXHAUSTIVE, timeout_s=1800)


def option_space(params: SearchParams = PARAMS, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """The option space as reachable with *params*' k (the timing lists are longer than k allows)."""
    transfers = params.k.get(NodeType.TRANSFER, 1)
    space = {"k": {t.value: n for t, n in params.k.items()}, "other_types": 1,
             "arrival_pickup_offsets": [30, *ARRIVAL_PICKUP_OFFSETS][:transfers],
             "departure_airport_buffers": [180, *DEPARTURE_BUFFERS][:transfers],
             "day_plan_variants": DAY_PLAN_VARIANTS, "facts_target_is_decision": False}
    if profile:
        space |= {"flight_offers": profile["flight_offers"], "extra_hotels": profile["extra_hotels"]}
    return space


def events_of(scenario: dict[str, Any]) -> list:
    """The scenario's disruption events (one, or two for a combined disruption), with stable IDs."""
    specs = scenario.get("events") or [{"type": scenario["type"], "target": scenario["target"],
                                        "facts": scenario["facts"]}]
    simulator = DisruptionSimulator(seed=0)
    return [simulator.make(e["type"], e["target"], **e["facts"]) for e in specs]


def apply_events(store: Any, scenario: dict[str, Any]) -> list:
    """Apply every event of *scenario* to *store*; the ApplyOutcomes of those that took effect."""
    effective = []
    for event in events_of(scenario):
        applied = apply_event(store, event)
        if applied.kind not in ("duplicate", "invalid") and applied.has_effect:
            effective.append(applied)
    return effective


async def for_scenario(trip: PlannedTrip, scenario: dict[str, Any], drift: bool,
                       params: SearchParams = PARAMS, strict: bool = True) -> dict[str, Any]:
    events = events_of(scenario)
    store = copy.deepcopy(trip.store)
    if drift:
        trip.providers.drift.enable(scenario["id"], **DRIFT)
    try:
        outcomes = [apply_event(store, e) for e in events]
        if all(o.kind in ("duplicate", "invalid") for o in outcomes):
            return {"applicable": False, "reason": outcomes[0].kind}
        effective = [o for o in outcomes if o.kind not in ("duplicate", "invalid") and o.has_effect]
        if not effective:
            empty = validate_plan(store, store, [], goal_from_graph(store))
            return {"applicable": True, "no_effect": True, "feasible": empty.feasible, "feasible_repairs": 1,
                    "complete_repairs": 1, "necessity": [], "min_scope": 0, "minimal_regions": [[]], "union": []}
        first, rest = effective[0], effective[1:]
        search = RepairSearch(trip.orch.registry, store, first.target, first.kind, params=params,
                              event_id=events[0].event_id, max_plan_evaluations=params.max_exhaustive,
                              also=tuple((o.target, o.kind) for o in rest))
        outcome = await exhaustive(search)
    finally:
        trip.providers.drift.disable()
    if outcome.truncated:
        if strict:
            raise GroundTruthError(f"{scenario['id']}: the exhaustive search was truncated; raise max_exhaustive")
        return {"applicable": True, "no_effect": False, "truncated": True, "feasible": None,
                "complete_repairs": len(outcome.plans), "feasible_repairs": None, "necessity": None,
                "min_scope": None, "minimal_regions": [], "union": None,
                "reason": f"more than {params.max_exhaustive} complete repairs: no exact ground truth; "
                          "results use the best plan any run found"}
    ordered = lambda nids: [n for n in search.order if n in nids]  # noqa: E731
    regions = [set(p.state.region) for p in outcome.plans if p.report.feasible]
    entry: dict[str, Any] = {"applicable": True, "no_effect": False, "feasible": bool(regions),
                             "complete_repairs": len(outcome.plans), "feasible_repairs": len(regions)}
    if not strict:
        entry["truncated"] = False
        entry["best_fitness"] = outcome.best.score.fitness if outcome.feasible else None
    if not regions:
        return entry | {"necessity": None, "min_scope": None, "minimal_regions": [], "union": None}
    smallest = min(len(r) for r in regions)
    minimal = sorted({tuple(ordered(r)) for r in regions if len(r) == smallest})
    return entry | {"necessity": ordered(set.intersection(*regions)), "min_scope": smallest,
                    "minimal_regions": [list(m) for m in minimal], "union": ordered(set.union(*regions))}


async def compute(scenarios: list[dict[str, Any]] | None = None, split: str = "dev",
                  catalogue: str = "realistic") -> dict[str, Any]:
    """
    The ground truth of a split × catalogue. Dev × realistic is the Phase 5 ground truth and is
    computed exactly as then (legacy scenarios and trips, legacy oracle beside it).
    """
    legacy = (split, catalogue) == ("dev", "realistic")
    if scenarios is None:
        scenarios = load_scenarios() if legacy else v3.load(split, catalogue)
    trips = load_trips() if legacy else v3.load_trips(split)
    profile = v3.PROFILES[catalogue]
    params = params_for(catalogue)
    planned: dict[str, PlannedTrip] = {}
    out: dict[str, Any] = {}
    for s in scenarios:
        if s["trip"] not in planned:
            planned[s["trip"]] = (await PlannedTrip.create(trips[s["trip"]]["goal"]) if legacy
                                  else await v3.plan_trip(trips[s["trip"]]["goal"], profile))
        trip = planned[s["trip"]]
        entry = {"drift_off": await for_scenario(trip, s, False, params, strict=legacy),
                 "drift_on": await for_scenario(trip, s, True, params, strict=legacy)}
        if catalogue == "realistic":
            entry["legacy_oracle"] = await legacy_oracle(trip.orch.registry, trip.store, events_of(s)[0])
        out[s["id"]] = entry
    return out


def document(scenarios: dict[str, Any], split: str = "dev", catalogue: str = "realistic") -> dict[str, Any]:
    data = {
        "version": 3,
        "definition": {
            "necessity": "intersection of repair regions over all feasible repairs in the option space",
            "min_scope": "smallest repair region over all feasible repairs",
            "union": "union of repair regions over all feasible repairs (reference only)",
            "feasible": "hard constraints of app/core/repair/constraints.py (pre-registration R6)",
            "legacy_oracle": "app.core.metrics.oracle_affected, drift off (policy-conditioned; not the v3 truth)",
        },
        "option_space": option_space(params_for(catalogue),
                                     None if catalogue == "realistic" else v3.PROFILES[catalogue]),
        "drift": DRIFT,
        "starting_state": "each trip planned once and its bookings approved by the simulated user (PlannedTrip)",
        "created": date.today().isoformat(),
        "source_hashes": source_hashes(),
        "scenarios": scenarios,
    }
    if (split, catalogue) != ("dev", "realistic"):
        data = {"split": split, "catalogue": catalogue, **data}
    return data


def write(data: dict[str, Any], split: str = "dev", catalogue: str = "realistic") -> str:
    gt_path, sha_path = paths(split, catalogue)
    GT_DIR.mkdir(parents=True, exist_ok=True)
    header = ("# Ground truth v3, generated by eval/ground_truth.py. Do not edit by hand: the file's sha256 is\n"
              f"# recorded in {sha_path.name} and checked before every E1-E4 run.\n")
    text = header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    gt_path.write_text(text, encoding="utf-8", newline="\n")
    digest = _sha(gt_path.read_bytes())
    sha_path.write_text(digest + "\n", encoding="utf-8", newline="\n")
    return digest


def _load_checked_file(split: str = "dev", catalogue: str = "realistic") -> dict[str, Any]:
    gt_path, sha_path = paths(split, catalogue)
    expected = sha_path.read_text(encoding="utf-8").strip()
    actual = _sha(gt_path.read_bytes())
    if actual != expected:
        raise GroundTruthError(f"{gt_path.name} was modified (sha256 {actual[:12]} ≠ recorded {expected[:12]})")
    return yaml.safe_load(gt_path.read_text(encoding="utf-8"))


def load_verified(split: str = "dev", catalogue: str = "realistic") -> dict[str, Any]:
    """The stored ground truth, after checking its hash and that the option-space sources are unchanged."""
    data = _load_checked_file(split, catalogue)
    changed = [rel for rel, h in source_hashes().items() if data["source_hashes"].get(rel) != h]
    if changed:
        raise GroundTruthError("the option space changed since the ground truth was written: " + ", ".join(changed))
    return data


# ── Scope metrics against the ground truth (pre-registration secondary metrics) ──

def necessity_coverage(regenerated: list[str], necessity: list[str] | None) -> float | None:
    """|R ∩ N| / |N|: the share of the must-change tasks the repair includes. None when N is empty or undefined."""
    if not necessity:
        return None
    return round(len(set(regenerated) & set(necessity)) / len(necessity), 4)


def excess_scope(regenerated: list[str], min_scope: int | None) -> int | None:
    """|R| − min scope: tasks regenerated beyond the smallest feasible repair. None for a conflict scenario."""
    return None if min_scope is None else len(set(regenerated)) - min_scope


def scope_optimality(regenerated: list[str], min_scope: int | None) -> float | None:
    """min scope / |R| (1 = as small as any feasible repair). None for a conflict, or nothing regenerated."""
    if min_scope is None or not regenerated:
        return None if min_scope is None else (1.0 if min_scope == 0 else None)
    return round(min_scope / len(set(regenerated)), 4)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=v3.SPLITS, default="dev")
    parser.add_argument("--catalogue", choices=v3.CATALOGUES, default="realistic")
    parser.add_argument("--write", action="store_true", help="write the ground truth instead of checking it")
    parser.add_argument("--rehash", action="store_true",
                        help="after a source change: re-stamp the source hashes, only if a full recompute "
                             "reproduces the stored ground truth exactly")
    args = parser.parse_args()
    split, catalogue = args.split, args.catalogue
    gt_path, _ = paths(split, catalogue)
    scenarios = await compute(split=split, catalogue=catalogue)
    if args.rehash:
        stored = _load_checked_file(split, catalogue)
        if stored["scenarios"] != scenarios:
            bad = [sid for sid in scenarios if stored["scenarios"].get(sid) != scenarios[sid]]
            raise SystemExit(f"refused: the ground truth changed in {len(bad)} scenarios, e.g. {bad[:5]}; "
                             "this needs a new ground truth (--write) and a recorded deviation")
        changed = [rel for rel, h in source_hashes().items() if stored["source_hashes"].get(rel) != h]
        stored["source_hashes"] = source_hashes()
        stored.setdefault("rehashed", []).append({"date": date.today().isoformat(), "sources": changed})
        digest = write(stored, split, catalogue)
        print(f"ground truth unchanged; source hashes re-stamped for {changed}. New sha256 {digest}: "
              "record it in eval/preregistration.yaml (ground_truth.sha256 and deviations).")
        return
    if args.write:
        digest = write(document(scenarios, split, catalogue), split, catalogue)
        print(f"wrote {gt_path} ({len(scenarios)} scenarios x 2 drift settings), sha256 {digest}")
        return
    stored = load_verified(split, catalogue)
    if stored["scenarios"] != scenarios:
        bad = [sid for sid in scenarios if stored["scenarios"].get(sid) != scenarios[sid]]
        print(f"ground truth is NOT reproducible: {len(bad)} scenarios differ, e.g. {bad[:5]}")
        raise SystemExit(1)
    print(f"ground truth reproduced exactly ({len(scenarios)} scenarios x 2 drift settings)")


if __name__ == "__main__":
    asyncio.run(main())
