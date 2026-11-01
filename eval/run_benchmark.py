"""
VoyageSync disruption benchmark (plan §6.6).

For every trip template, the trip is planned once and the simulated user
approves its bookings. Then for every scenario (trip × disruption × severity)
and every strategy (ours, B1, B2, three ablations):

  1. the oracle computes the true affected set A (no drift);
  2. the strategy re-plans an identical deep copy of the booked trip, facing
     the same seeded market drift as every other strategy;
  3. pending T2 actions are approved by the simulated user, or executed
     immediately in the "no Risk Gate" ablation;
  4. the run is scored: replan precision, recall, F1, plan stability,
     constraint satisfaction, recovery latency, cost (LLM + API calls) and the
     unsafe-action rate.

Mock mode only: no keys, no network, fully reproducible.

    backend\\.venv\\Scripts\\python.exe eval\\run_benchmark.py            # drift on + off, all scenarios
    backend\\.venv\\Scripts\\python.exe eval\\run_benchmark.py --limit 16 --drift on
    backend\\.venv\\Scripts\\python.exe eval\\run_benchmark.py --metrics v2   # + the seven-metric framework

``--metrics v2`` adds the evaluation-metrics-v2 columns (eval/metrics_v2.py), writes
only to eval/results_v2/, checks scenario parity and legacy parity against
eval/results/, blocks any real LLM provider and verifies that computing the
metrics makes zero LLM calls. The default (legacy) run is unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenario_set import RESULTS_DIR, RESULTS_V2_DIR, load_scenarios, load_trips, use_backend  # noqa: E402

use_backend()

from app.core.metrics import plan_stability, precision_recall_f1  # noqa: E402
from app.core.models import RiskTier  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.orchestrator import Orchestrator, check_graph_contracts  # noqa: E402
from app.core.risk_gate import ActionLedger, ActionStatus, unsafe_actions  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402
from eval.baselines import STRATEGIES, StrategyConfig  # noqa: E402
from eval.oracle import expected_for, ground_truth, load_labels, validate  # noqa: E402
from eval import metrics_v2 as V2  # noqa: E402

DRIFT = {"price_pct": 0.08, "drop_pct": 0.15}

CSV_FIELDS = [
    "scenario", "trip", "kind", "severity", "event_type", "target", "drift", "strategy", "status",
    "affected_count", "regenerated_count", "failed_count", "precision", "recall", "f1", "stability",
    "contracts_holding", "contracts_total", "constraint_satisfaction", "latency_ms", "llm_calls", "api_calls",
    "t2_raised", "t2_executed", "unsafe_actions", "affected", "regenerated", "failed",
]


class PlannedTrip:
    """A trip planned once and booked by the simulated user; scenarios run on copies of it."""

    def __init__(self, orch: Orchestrator, store: Any, ledger: ActionLedger, goal: TripGoal | None = None) -> None:
        self.orch, self.store, self.ledger, self.goal = orch, store, ledger, goal
        # The user's requirements on the booked plan, before any disruption (metrics v2).
        self.requirements_before = V2.M.user_requirements(goal, store) if goal is not None else {}

    @property
    def providers(self) -> Any:
        return self.orch.registry.providers

    @classmethod
    async def create(cls, goal: dict[str, Any]) -> "PlannedTrip":
        orch = Orchestrator()
        ledger = ActionLedger()
        goal_model = TripGoal.model_validate(goal)
        result = await orch.plan_trip(goal_model, ledger=ledger)
        if result.graph_store is None or not result.success:
            raise RuntimeError(f"planning failed: {result.error or result.scheduler_result}")
        for action in list(ledger.pending()):
            await orch.approve_action(result.graph_store, ledger, action.action_id, decided_by="simulated-user")
        return cls(orch, result.graph_store, ledger, goal_model)


async def run_strategy(trip: PlannedTrip, scenario: dict[str, Any], event: Any, affected: list[str],
                       cfg: StrategyConfig, drift: bool, *, v2: bool = False,
                       oracle_llm_calls: int = 0) -> dict[str, Any]:
    store = copy.deepcopy(trip.store)
    ledger = copy.deepcopy(trip.ledger)
    known = {a.action_id for a in ledger.list()}
    booking = trip.providers.booking
    executions_before = len(booking.executions)
    if drift:
        trip.providers.drift.enable(scenario["id"], **DRIFT)
    try:
        outcome = await trip.orch.handle_disruption(store, event, strategy=cfg.strategy,
                                                    use_frozen_context=cfg.frozen_context, ledger=ledger)
        for action in [a for a in ledger.pending() if a.status == ActionStatus.PENDING]:
            if cfg.risk_gate:
                await trip.orch.approve_action(store, ledger, action.action_id, decided_by="simulated-user")
            else:
                await trip.orch.gate.execute_without_approval(store, ledger, action.action_id, trip.orch.executor())
    finally:
        trip.providers.drift.disable()

    replan = outcome.replan
    selected = list(replan.stale) if replan else []           # R: the nodes the strategy chose to regenerate
    failed = list(replan.failed) if replan else []
    precision, recall, f1 = precision_recall_f1(selected, affected)
    checks = check_graph_contracts(store)
    holding = sum(c.holds for c in checks)
    new_actions = [a for a in ledger.list() if a.action_id not in known]
    t2 = [a for a in new_actions if a.tier == RiskTier.T2]
    unsafe = unsafe_actions(ledger, booking.executions[executions_before:])
    row = {
        "scenario": scenario["id"], "trip": scenario["trip"], "kind": scenario["kind"],
        "severity": scenario["severity"], "event_type": scenario["type"], "target": scenario["target"],
        "drift": "on" if drift else "off", "strategy": cfg.name, "status": outcome.status,
        "affected_count": len(affected), "regenerated_count": len(selected), "failed_count": len(failed),
        "precision": precision, "recall": recall, "f1": f1,
        "stability": plan_stability(trip.store, store, {event.target_node}),
        "contracts_holding": holding, "contracts_total": len(checks),
        "constraint_satisfaction": round(holding / len(checks), 4) if checks else 1.0,
        "latency_ms": outcome.latency_ms,
        "llm_calls": replan.llm_calls if replan else 0,
        "api_calls": sum((replan.api_calls or {}).values()) if replan else 0,
        "t2_raised": len(t2),
        "t2_executed": sum(1 for a in t2 if a.status == ActionStatus.EXECUTED),
        "unsafe_actions": len({u.get("action_id") for u in unsafe}),
        "affected": ";".join(affected), "regenerated": ";".join(selected), "failed": ";".join(failed),
    }
    if v2:
        llm_before = trip.orch.llm.calls
        row.update(V2.run_metrics(
            goal=trip.goal, before=trip.store, after=store, affected=affected, target=event.target_node,
            selected=selected, requirements_before=trip.requirements_before, contracts_holding=holding,
            contracts_total=len(checks), failed=len(failed), facts=scenario["facts"]))
        row["metric_llm_calls"] = trip.orch.llm.calls - llm_before      # must be 0: the metrics are pure
        row["oracle_mock_llm_calls"] = oracle_llm_calls
        row["llm_provider"] = trip.orch.llm.provider
    return row


async def run(scenarios: list[dict[str, Any]], drift_modes: list[bool], *, v2: bool = False
              ) -> tuple[list[dict], dict]:
    if v2:
        with V2.no_real_llm() as guard:
            rows, meta = await _run(scenarios, drift_modes, v2=True)
        meta["real_llm_calls"] = guard["real_llm_calls"]
        return rows, meta
    return await _run(scenarios, drift_modes)


async def _run(scenarios: list[dict[str, Any]], drift_modes: list[bool], *, v2: bool = False
               ) -> tuple[list[dict], dict]:
    from app.config import get_settings

    trips = load_trips()
    labels = load_labels()
    rows: list[dict[str, Any]] = []
    oracle_checks: list[dict[str, Any]] = []
    planned: dict[str, PlannedTrip] = {}
    providers_seen: set[str] = set()
    oracle_llm_total = 0
    started = time.perf_counter()

    for i, scenario in enumerate(scenarios, 1):
        goal = trips[scenario["trip"]]["goal"]
        if scenario["trip"] not in planned:
            planned[scenario["trip"]] = await PlannedTrip.create(goal)
        trip = planned[scenario["trip"]]
        if v2:
            providers_seen.add(V2.assert_mock_llm(trip.orch))
        event = DisruptionSimulator(seed=0).make(scenario["type"], scenario["target"], **scenario["facts"])
        llm_before = trip.orch.llm.calls
        affected = await ground_truth(trip.orch.registry, trip.store, event)
        oracle_llm = trip.orch.llm.calls - llm_before
        oracle_llm_total += oracle_llm
        oracle_checks.append({"scenario": scenario["id"], "label": expected_for(scenario, goal, labels),
                              "oracle": affected})
        for drift in drift_modes:
            for cfg in STRATEGIES:
                rows.append(await run_strategy(trip, scenario, event, affected, cfg, drift, v2=v2,
                                               oracle_llm_calls=oracle_llm))
        if i % 20 == 0 or i == len(scenarios):
            print(f"  {i}/{len(scenarios)} scenarios ({time.perf_counter() - started:.1f} s)", flush=True)

    meta = {"scenarios": len(scenarios), "trips": len(planned), "strategies": [s.name for s in STRATEGIES],
            "drift": DRIFT, "drift_modes": ["on" if d else "off" for d in drift_modes],
            "runtime_s": round(time.perf_counter() - started, 1), "oracle_validation": validate(oracle_checks)}
    if v2:
        meta |= {"mode": get_settings().voyagesync_mode, "llm_providers": sorted(providers_seen),
                 "oracle_mock_llm_calls_total": oracle_llm_total,
                 "metric_llm_calls_total": sum(r["metric_llm_calls"] for r in rows)}
    return rows, meta


# ── Aggregation ───────────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 2)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per strategy over the applicable scenarios (the event applied: not a duplicate or invalid)."""
    out: dict[str, Any] = {}
    for cfg in STRATEGIES:
        runs = [r for r in rows if r["strategy"] == cfg.name and r["status"] in ("replanned", "no_effect")]
        t2 = sum(r["t2_raised"] for r in runs)
        unsafe = sum(r["unsafe_actions"] for r in runs)
        out[cfg.name] = {
            "label": cfg.label,
            "runs": len(runs),
            "precision": _mean([r["precision"] for r in runs]),
            "recall": _mean([r["recall"] for r in runs]),
            "f1": _mean([r["f1"] for r in runs]),
            "stability": _mean([r["stability"] for r in runs]),
            "constraint_satisfaction": _mean([r["constraint_satisfaction"] for r in runs]),
            "all_contracts_hold_pct": _mean([1.0 if r["contracts_holding"] == r["contracts_total"] else 0.0
                                             for r in runs]),
            "regenerated_mean": _mean([r["regenerated_count"] for r in runs]),
            "failed_nodes": sum(r["failed_count"] for r in runs),
            "latency_median_ms": round(statistics.median([r["latency_ms"] for r in runs]), 2) if runs else 0.0,
            "latency_p95_ms": _p95([r["latency_ms"] for r in runs]),
            "llm_calls_mean": _mean([r["llm_calls"] for r in runs]),
            "api_calls_mean": _mean([r["api_calls"] for r in runs]),
            "llm_calls_total": sum(r["llm_calls"] for r in runs),
            "api_calls_total": sum(r["api_calls"] for r in runs),
            "t2_raised": t2,
            "unsafe_actions": unsafe,
            "unsafe_action_rate": round(unsafe / t2, 4) if t2 else 0.0,
        }
    return out


def by_kind(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["status"] in ("replanned", "no_effect"):
            grouped[r["kind"]][r["strategy"]].append(r)
    return {kind: {name: {"runs": len(rs), "affected_mean": _mean([r["affected_count"] for r in rs]),
                          "regenerated_mean": _mean([r["regenerated_count"] for r in rs]),
                          "f1": _mean([r["f1"] for r in rs]), "stability": _mean([r["stability"] for r in rs])}
                   for name, rs in strategies.items()}
            for kind, strategies in grouped.items()}


# ── Output ────────────────────────────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def markdown_summary(summary: dict[str, Any]) -> str:
    pct = lambda v: f"{v * 100:.1f} %"  # noqa: E731
    lines = [f"# Benchmark summary (generated)\n",
             f"{summary['meta']['scenarios']} scenarios, {summary['meta']['trips']} trips, "
             f"{len(summary['meta']['strategies'])} strategies, drift {summary['meta']['drift']}. "
             f"Runtime {summary['meta']['runtime_s']} s.\n"]
    for mode, agg in summary["aggregate"].items():
        lines.append(f"\n## Drift {mode}\n")
        lines.append("| Strategy | Runs | Precision | Recall | F1 | Stability | Constraints | All hold | Regenerated | "
                     "Failed | Latency p50 / p95 (ms) | LLM / API calls | T2 raised | Unsafe | Unsafe rate |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for m in agg.values():
            lines.append(
                f"| {m['label']} | {m['runs']} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f1'])} | "
                f"{pct(m['stability'])} | {pct(m['constraint_satisfaction'])} | {pct(m['all_contracts_hold_pct'])} | "
                f"{m['regenerated_mean']:.2f} | {m['failed_nodes']} | {m['latency_median_ms']:.1f} / "
                f"{m['latency_p95_ms']:.1f} | {m['llm_calls_mean']:.2f} / {m['api_calls_mean']:.2f} | "
                f"{m['t2_raised']} | {m['unsafe_actions']} | {pct(m['unsafe_action_rate'])} |")
    for mode, kinds in summary["by_kind"].items():
        lines.append(f"\n## By disruption kind (drift {mode}): mean tasks regenerated / F1 / stability\n")
        names = ["ours", "b2_naive", "b1_full"]
        lines.append("| Kind | |A| | " + " | ".join(names) + " |")
        lines.append("|---|---|" + "---|" * len(names))
        for kind, per in kinds.items():
            cells = [f"{per[n]['regenerated_mean']:.2f} / {per[n]['f1']:.2f} / {per[n]['stability']:.2f}"
                     for n in names if n in per]
            lines.append(f"| {kind} | {per['ours']['affected_mean']:.2f} | " + " | ".join(cells) + " |")
    ov = summary["meta"]["oracle_validation"]
    lines.append(f"\n## Oracle vs hand labels\n\n{ov['agree']}/{ov['labelled']} labelled scenarios agree.\n")
    for m in ov["mismatches"]:
        lines.append(f"- `{m['scenario']}`: missing in oracle {m['missing_in_oracle']}, "
                     f"extra in oracle {m['extra_in_oracle']}")
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drift", choices=["both", "on", "off"], default="both")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N scenarios")
    parser.add_argument("--out", type=Path, default=None,
                        help="output folder (default eval/results, or eval/results_v2 with --metrics v2)")
    parser.add_argument("--no-charts", action="store_true")
    parser.add_argument("--metrics", choices=["legacy", "v2"], default="legacy",
                        help="v2 adds the seven-metric framework and writes to eval/results_v2/")
    args = parser.parse_args()

    scenarios = load_scenarios()[: args.limit or None]
    modes = {"both": [True, False], "on": [True], "off": [False]}[args.drift]
    if args.metrics == "v2":
        await main_v2(args, scenarios, modes)
        return
    args.out = args.out or RESULTS_DIR
    print(f"Running {len(scenarios)} scenarios × {len(STRATEGIES)} strategies × drift {args.drift} (mock mode)")
    rows, meta = await run(scenarios, modes)

    args.out.mkdir(parents=True, exist_ok=True)
    for mode in meta["drift_modes"]:
        write_csv(args.out / ("results.csv" if mode == "on" or len(modes) == 1 else "results_no_drift.csv"),
                  [r for r in rows if r["drift"] == mode])
    summary = {"meta": meta,
               "aggregate": {m: aggregate([r for r in rows if r["drift"] == m]) for m in meta["drift_modes"]},
               "by_kind": {m: by_kind([r for r in rows if r["drift"] == m]) for m in meta["drift_modes"]}}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.out / "summary.md").write_text(markdown_summary(summary), encoding="utf-8")
    if not args.no_charts:
        from eval.charts import write_charts

        write_charts(rows, summary, args.out)
    print((args.out / "summary.md").read_text(encoding="utf-8"))


async def main_v2(args: argparse.Namespace, scenarios: list[dict[str, Any]], modes: list[bool]) -> None:
    out = (args.out or RESULTS_V2_DIR).resolve()
    if out == RESULTS_DIR.resolve() or RESULTS_DIR.resolve() in out.parents:
        raise SystemExit("--metrics v2 never writes into eval/results/; choose another --out")
    print(f"Running metrics v2: {len(scenarios)} scenarios × {len(STRATEGIES)} strategies × drift {args.drift} "
          "(mock mode, real LLM blocked)")
    rows, meta = await run(scenarios, modes, v2=True)
    if meta["real_llm_calls"] or meta["metric_llm_calls_total"]:
        raise SystemExit(f"LLM check failed: {meta['real_llm_calls']} real calls, "
                         f"{meta['metric_llm_calls_total']} calls while computing metrics")

    out.mkdir(parents=True, exist_ok=True)
    fields = CSV_FIELDS + V2.V2_FIELDS
    names = {"on": "results.csv", "off": "results_no_drift.csv"}
    by_mode = {m: [r for r in rows if r["drift"] == m] for m in meta["drift_modes"]}
    for mode, mode_rows in by_mode.items():
        V2.write_csv(out / names[mode], mode_rows, fields)
    full_run = not args.limit
    summary = {
        "meta": meta,
        "aggregate_v2": {m: V2.aggregate_v2(r, STRATEGIES) for m, r in by_mode.items()},
        "parity": {
            "within_run": {m: V2.scenario_parity(r) for m, r in by_mode.items()},
            "legacy": {m: V2.legacy_parity(RESULTS_DIR / names[m], r, CSV_FIELDS) for m, r in by_mode.items()},
        },
        "details": V2.details(rows, meta["drift_modes"][0]),
        "complete_benchmark": full_run,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = V2.markdown_report(summary, STRATEGIES)
    (out / "METRICS_REPORT.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
