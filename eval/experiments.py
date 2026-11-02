"""
E1–E4 experiment runner (pre-registration ``arms``, ``budget``, ``seeds``, ``statistics``).

For every scenario of a split × catalogue, every drift setting and every arm, the trip (planned
once in the catalogue's option space and booked by the simulated user) is copied, the scenario's
events are applied, and the arm repairs it:

  E1 / B1 / B2   the existing Replanner (contract / full / naive), once per disrupted task
  E1g E2 E3 E4   greedy / beam / GA / hybrid repair search, all events at once
  RS / EXH       random search (the GA's control) / exhaustive (the optimality reference)

Each run is scored the same way, on the final graph against the graph after the events: the
hard-constraint validation, conflict and silent failure, the four objective terms and fitness, the
scope metrics against the ground truth, the user's requirements (which no arm optimises), the
legacy precision/recall (E1, B1, B2 on the realistic catalogue), and the cost (plan evaluations,
partial evaluations, option generations, provider and LLM calls, wall-clock time). No Risk Gate
runs here: the gate never changes what a plan contains, and the legacy benchmark covers it.

Stochastic arms (E3, E4, RS) run once per seed, seed = sha256(base | scenario | run).
``--weights disruption=0.5`` runs the pre-registered weight sensitivity: the search arms optimise, and every
arm is scored by, the changed weights (the others stay 1). Without it the weights are the frozen equal ones.
The held-out split runs only when the pre-registration is frozen and its hash verifies.

    backend\\.venv\\Scripts\\python.exe eval\\experiments.py --split dev --catalogue stress --seeds 5
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import hashlib
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval.ground_truth as gt  # noqa: E402  (also forces mock mode)
from eval import scenario_set_v3 as v3  # noqa: E402
from eval.run_benchmark import DRIFT  # noqa: E402
from eval.scenario_set import EVAL_DIR  # noqa: E402

from app.core.metrics import precision_recall_f1, requirement_satisfaction, user_requirements  # noqa: E402
from app.core.repair.commit import repair  # noqa: E402
from app.core.repair.constraints import goal_from_graph, validate_plan  # noqa: E402
from app.core.repair.objective import TERMS, ObjectiveWeights, score  # noqa: E402
from app.core.repair.policies import GAParams, PolicyParams  # noqa: E402
from app.core.replanner import Replanner  # noqa: E402

RESULTS_DIR = EVAL_DIR / "results_v3"
PREREG = EVAL_DIR / "preregistration.yaml"
ARMS = ["E1", "B1", "B2", "E1g", "E2", "E3", "E4", "RS", "EXH"]
LEGACY = {"E1": "contract", "B1": "full", "B2": "naive"}
POLICY = {"E1g": "greedy", "E2": "beam", "E3": "ga", "E4": "hybrid", "RS": "random", "EXH": "exhaustive"}
STOCHASTIC = {"E3", "E4", "RS"}
FIELDS = [
    "split", "catalogue", "scenario", "trip", "kind", "severity", "drift", "arm", "beam_width", "run", "seed",
    "status", "committed", "feasible", "conflict", "silent_failure", "violations", "regenerated_count",
    "regenerated", "fitness", *TERMS, "necessity_coverage", "excess_scope", "scope_optimality",
    "requirement_satisfaction", "legacy_precision", "legacy_recall", "plan_evaluations", "partial_evaluations",
    "option_generations", "option_cache_hits", "api_calls", "llm_calls", "latency_ms", "timed_out",
    "truncated", "stopped",
]


class PreregistrationError(Exception):
    pass


def prereg_digest(text: str) -> str:
    """sha256 of the pre-registration with its own ``frozen_sha256`` value blanked (so it can hold it)."""
    lines = [("frozen_sha256: null" if line.startswith("frozen_sha256:") else line) for line in text.splitlines()]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def verify_frozen() -> dict[str, Any]:
    text = PREREG.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data.get("status") != "frozen":
        raise PreregistrationError("the pre-registration is not frozen: the held-out split cannot run")
    if data.get("frozen_sha256") != prereg_digest(text):
        raise PreregistrationError("the pre-registration changed after it was frozen")
    return data


def run_seed(base: int, scenario_id: str, run: int) -> int:
    return int(hashlib.sha256(f"{base}|{scenario_id}|{run}".encode()).hexdigest()[:8], 16)


@dataclass
class Config:
    split: str
    catalogue: str
    arms: list[str]
    drift_modes: list[bool]
    seeds: int = 5
    base_seed: int = 0
    beam_width: int = 5
    budget: int = 500
    ga: GAParams = field(default_factory=GAParams)
    weights: dict[str, float] = field(default_factory=dict)   # overrides of the equal weights (sensitivity only)

    def objective_weights(self) -> ObjectiveWeights:
        return ObjectiveWeights(**self.weights)


def parse_weights(text: str) -> dict[str, float]:
    """'disruption=0.5,money=1.5' → {'disruption': 0.5, 'money': 1.5}; only the four objective terms."""
    weights = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, _, value = part.partition("=")
        if name not in TERMS:
            raise SystemExit(f"--weights: unknown term {name!r} (terms: {', '.join(TERMS)})")
        weights[name] = float(value)
    return weights


class Meter:
    def __init__(self, providers: Any) -> None:
        self.providers = providers
        self.t = time.perf_counter()
        self.api = providers.usage.total
        self.llm = providers.llm.calls

    def read(self) -> tuple[float, int, int]:
        return (round((time.perf_counter() - self.t) * 1000, 2), self.providers.usage.total - self.api,
                self.providers.llm.calls - self.llm)


async def run_arm(arm: str, trip: Any, scenario: dict[str, Any], drift: bool, cfg: Config, *,
                  seed: int = 0) -> tuple[Any, Any, dict[str, Any]]:
    """(final graph, graph after the events, run info) for one arm on one scenario."""
    store = copy.deepcopy(trip.store)
    providers = trip.orch.registry.providers
    if drift:
        providers.drift.enable(scenario["id"], **DRIFT)
    meter = Meter(providers)
    info: dict[str, Any] = {"region": [], "committed": False, "conflict": False, "budget": {}, "stopped": "",
                            "truncated": False}
    try:
        effective = gt.apply_events(store, scenario)
        reference = copy.deepcopy(store)
        if not effective:
            info["status"] = "no_effect"
        elif arm in LEGACY:
            replanner = Replanner(trip.orch.registry)
            for applied in effective:
                result = await replanner.replan(store, applied.target, applied.kind, strategy=LEGACY[arm])
                info["region"] += [n for n in result.stale if n not in info["region"]]
            info["status"], info["committed"] = "replanned", True
        else:
            params = v3.search_params(v3.PROFILES[cfg.catalogue], max_plan_evaluations=cfg.budget,
                                      max_exhaustive=gt.params_for(cfg.catalogue).max_exhaustive,
                                      timeout_s=gt.params_for(cfg.catalogue).timeout_s if arm == "EXH" else 60,
                                      weights=cfg.objective_weights())
            first, rest = effective[0], effective[1:]
            outcome, _, _ = await repair(trip.orch.registry, store, first.target, first.kind, policy=POLICY[arm],
                                         params=params,
                                         policy_params=PolicyParams(beam_width=cfg.beam_width, seed=seed, ga=cfg.ga),
                                         also=tuple((o.target, o.kind) for o in rest))
            committed = outcome.feasible and outcome.conflict is None
            info.update(status="replanned" if committed else "conflict", committed=committed,
                        conflict=not committed, region=list(outcome.best.state.region) if committed else [],
                        budget=outcome.budget.to_dict(), truncated=outcome.truncated,
                        stopped=(outcome.dead_ends[-1] if outcome.dead_ends else ""))
    finally:
        providers.drift.disable()
    info["latency_ms"], info["api_calls"], info["llm_calls"] = meter.read()
    info["targets"] = sorted({o.target for o in effective})
    return store, reference, info


def score_run(trip: Any, scenario: dict[str, Any], drift: bool, arm: str, final: Any, reference: Any,
              info: dict[str, Any], truth: dict[str, Any], cfg: Config, run: int, seed: int) -> dict[str, Any]:
    region = info["region"]
    report = validate_plan(final, reference, region, goal_from_graph(reference))
    s = score(reference, final, info["targets"] or [scenario.get("target", "")], cfg.objective_weights())
    entry = truth["scenarios"][scenario["id"]]["drift_on" if drift else "drift_off"]
    exact = entry.get("applicable") and not entry.get("truncated") and entry.get("feasible")
    requirements = requirement_satisfaction(user_requirements(trip.goal, final))
    legacy_p = legacy_r = None
    if arm in LEGACY and cfg.catalogue == "realistic":
        legacy_p, legacy_r, _ = precision_recall_f1(region, truth["scenarios"][scenario["id"]]["legacy_oracle"])
    budget = info["budget"]
    return {
        "split": cfg.split, "catalogue": cfg.catalogue, "scenario": scenario["id"], "trip": scenario["trip"],
        "kind": scenario["kind"], "severity": scenario["severity"], "drift": "on" if drift else "off", "arm": arm,
        "beam_width": cfg.beam_width if arm in ("E2", "E4") else "", "run": run, "seed": seed if arm in STOCHASTIC else "",
        "status": info["status"], "committed": info["committed"], "feasible": report.feasible,
        "conflict": info["conflict"],
        "silent_failure": bool(region) and not report.feasible and not info["conflict"],
        "violations": report.violation_count, "regenerated_count": len(region), "regenerated": ";".join(region),
        "fitness": s.fitness, **s.terms,
        "necessity_coverage": gt.necessity_coverage(region, entry["necessity"]) if exact else None,
        "excess_scope": gt.excess_scope(region, entry["min_scope"]) if exact else None,
        "scope_optimality": gt.scope_optimality(region, entry["min_scope"]) if exact else None,
        "requirement_satisfaction": requirements["rate"], "legacy_precision": legacy_p, "legacy_recall": legacy_r,
        "plan_evaluations": budget.get("plan_evaluations"), "partial_evaluations": budget.get("partial_evaluations"),
        "option_generations": budget.get("option_generations"), "option_cache_hits": budget.get("option_cache_hits"),
        "api_calls": info["api_calls"], "llm_calls": info["llm_calls"], "latency_ms": info["latency_ms"],
        "timed_out": budget.get("timed_out"), "truncated": info["truncated"], "stopped": info["stopped"],
    }


async def run(cfg: Config, scenarios: list[dict[str, Any]] | None = None, *, progress: bool = True
              ) -> list[dict[str, Any]]:
    if cfg.split == "heldout":
        verify_frozen()
    truth = gt.load_verified(cfg.split, cfg.catalogue)
    scenarios = scenarios if scenarios is not None else v3.load(cfg.split, cfg.catalogue)
    trips = v3.load_trips(cfg.split)
    planned: dict[str, Any] = {}
    rows = []
    started = time.perf_counter()
    for i, s in enumerate(scenarios, 1):
        if s["trip"] not in planned:
            planned[s["trip"]] = await v3.plan_trip(trips[s["trip"]]["goal"], v3.PROFILES[cfg.catalogue])
        trip = planned[s["trip"]]
        for drift in cfg.drift_modes:
            for arm in cfg.arms:
                runs = range(cfg.seeds) if arm in STOCHASTIC else range(1)
                for r in runs:
                    seed = run_seed(cfg.base_seed, s["id"], r) if arm in STOCHASTIC else 0
                    final, reference, info = await run_arm(arm, trip, s, drift, cfg, seed=seed)
                    rows.append(score_run(trip, s, drift, arm, final, reference, info, truth, cfg, r, seed))
        if progress and (i % 20 == 0 or i == len(scenarios)):
            print(f"  {cfg.split}/{cfg.catalogue}: {i}/{len(scenarios)} scenarios "
                  f"({time.perf_counter() - started:.0f} s)", flush=True)
    return rows


# ── Aggregation ───────────────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None and x != ""]
    return round(statistics.fmean(xs), 6) if xs else None


def per_scenario(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """(catalogue, drift, scenario, arm, beam_width) → the run means over seeds, plus the seed spread of fitness."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["catalogue"], r["drift"], r["scenario"], r["arm"], r["beam_width"]), []).append(r)
    out = {}
    for key, rs in groups.items():
        fitness = [r["fitness"] for r in rs]
        out[key] = {"trip": rs[0]["trip"], "runs": len(rs), "feasible": _mean([float(r["feasible"]) for r in rs]),
                    "conflict": _mean([float(r["conflict"]) for r in rs]),
                    "fitness": _mean(fitness), "fitness_sd": round(statistics.pstdev(fitness), 6) if len(rs) > 1 else 0.0,
                    **{t: _mean([r[t] for r in rs]) for t in TERMS},
                    "plan_evaluations": _mean([r["plan_evaluations"] for r in rs])}
    return out


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per catalogue × drift × arm (× beam width): rates and means over scenarios (seed means first)."""
    scen = per_scenario(rows)
    out: dict[str, Any] = {}
    for (cat, drift, _sid, arm, width), v in scen.items():
        key = f"{cat}|drift_{drift}|{arm}{width if width != '' else ''}"
        out.setdefault(key, []).append(v)
    summary = {}
    for key, vs in sorted(out.items()):
        feasible = [v for v in vs if v["feasible"] == 1.0]
        summary[key] = {"scenarios": len(vs), "feasible_rate": _mean([v["feasible"] for v in vs]),
                        "conflict_rate": _mean([v["conflict"] for v in vs]),
                        "fitness_feasible_mean": _mean([v["fitness"] for v in feasible]),
                        "fitness_seed_sd_mean": _mean([v["fitness_sd"] for v in vs]),
                        **{f"{t}_mean": _mean([v[t] for v in feasible]) for t in TERMS},
                        "plan_evaluations_mean": _mean([v["plan_evaluations"] for v in vs])}
    rates = {}
    for r in rows:
        rates.setdefault(f"{r['catalogue']}|drift_{r['drift']}|{r['arm']}{r['beam_width']}", []).append(r)
    for key, rs in rates.items():
        summary[key] |= {"silent_failure_rate": _mean([float(r["silent_failure"]) for r in rs]),
                         "requirement_satisfaction_mean": _mean([r["requirement_satisfaction"] for r in rs]),
                         "partial_evaluations_mean": _mean([r["partial_evaluations"] for r in rs]),
                         "option_generations_mean": _mean([r["option_generations"] for r in rs]),
                         "api_calls_mean": _mean([r["api_calls"] for r in rs]),
                         "llm_calls_mean": _mean([r["llm_calls"] for r in rs]),
                         "latency_ms_median": round(statistics.median([r["latency_ms"] for r in rs]), 2),
                         "timed_out_runs": sum(1 for r in rs if r["timed_out"]),
                         "necessity_coverage_mean": _mean([r["necessity_coverage"] for r in rs]),
                         "excess_scope_mean": _mean([r["excess_scope"] for r in rs]),
                         "legacy_precision_mean": _mean([r["legacy_precision"] for r in rs]),
                         "legacy_recall_mean": _mean([r["legacy_recall"] for r in rs])}
    return summary


def write_outputs(out: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (out / "summary.json").write_text(json.dumps(summarise(rows), indent=2), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def manifest(cfgs: list[Config]) -> dict[str, Any]:
    text = PREREG.read_text(encoding="utf-8")
    return {"preregistration_digest": prereg_digest(text), "preregistration_status": yaml.safe_load(text)["status"],
            "configs": [cfg.__dict__ | {"ga": cfg.ga.__dict__} for cfg in cfgs],
            "ground_truth": {f"{c.split}_{c.catalogue}": gt.paths(c.split, c.catalogue)[1].read_text().strip()
                             for c in cfgs},
            "source_hashes": gt.source_hashes(), "drift": DRIFT}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=v3.SPLITS, default="dev")
    parser.add_argument("--catalogue", choices=[*v3.CATALOGUES, "both"], default="both")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--drift", choices=["on", "off", "both"], default="both")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=5)
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--limit", type=int, default=0, help="first N scenarios per catalogue (smoke runs)")
    parser.add_argument("--trips", default="", help="only these trip IDs (comma-separated), to split one run "
                        "over parallel processes; seeds are keyed by scenario, so the rows are the same")
    parser.add_argument("--weights", default="", help="sensitivity only: changed objective weights, e.g. "
                        "'disruption=0.5' (the other terms stay 1)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    weights = parse_weights(args.weights)
    catalogues = list(v3.CATALOGUES) if args.catalogue == "both" else [args.catalogue]
    drift = {"on": [True], "off": [False], "both": [True, False]}[args.drift]
    out = args.out or RESULTS_DIR / args.split
    if out.resolve() in {(EVAL_DIR / "results").resolve(), (EVAL_DIR / "results_v2").resolve()}:
        raise SystemExit("experiments never write into eval/results or eval/results_v2")
    cfgs, rows = [], []
    for cat in catalogues:
        cfg = Config(args.split, cat, args.arms.split(","), drift, args.seeds, beam_width=args.beam_width,
                     budget=args.budget, weights=weights)
        cfgs.append(cfg)
        scenarios = v3.load(args.split, cat)
        if args.trips:
            scenarios = [s for s in scenarios if s["trip"] in set(args.trips.split(","))]
        scenarios = scenarios[: args.limit or None]
        rows += await run(cfg, scenarios)
    write_outputs(out, rows, manifest(cfgs))
    print(f"wrote {out} ({len(rows)} runs)")


if __name__ == "__main__":
    asyncio.run(main())
