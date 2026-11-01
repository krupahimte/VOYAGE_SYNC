"""
Evaluation metrics v2: the seven-metric framework (executability, constraint
satisfaction, time & cost efficiency, replanning latency, task preservation,
replanning scope, recovery success) computed per benchmark run, aggregated
per strategy, checked for parity, and written up as a report.

Used by ``run_benchmark.py --metrics v2``. Results go to eval/results_v2/ only;
eval/results/ and eval/RESULTS.md are never touched. All metric functions live
in app.core.metrics and are pure: they make no LLM or API calls (tracked per run
as ``metric_llm_calls`` and asserted to be 0).
"""

from __future__ import annotations

import contextlib
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from app.core import metrics as M

REQ = M.REQUIREMENTS

V2_FIELDS = [
    "facts", "task_count", "replanning_scope",
    "tpr_unaffected", "tpr_preserved", "tpr", "tpr_visible_unaffected", "tpr_visible_preserved", "tpr_visible",
    "e1_completion", "e2_output_valid", "e3_dependencies_evaluable", "e4_contracts_hold", "plan_valid",
    "execution_ready", "awaiting_approval", "invalid_outputs", "unevaluable_contracts", "broken_contracts",
    "task_plan_valid_rate", "task_ready_rate",
    *[f"req_{k}_before" for k in REQ], *[f"req_{k}" for k in REQ],
    "req_met", "req_checkable", "req_satisfaction", "req_baseline_satisfaction", "req_regressions",
    "cost_total_before", "cost_total_after", "budget_limit", "cost_change_of_budget", "within_budget_after",
    "retention_days", "activity_minutes_before", "activity_minutes_after", "activity_time_retention",
    "contract_recovered", "strict_recovered",
    "metric_llm_calls", "oracle_mock_llm_calls", "llm_provider",
]

# The strategies the comparison report puts side by side (ablations go to an appendix).
MAIN = [("ours", "Ours"), ("b2_naive", "B2 naive"), ("b1_full", "B1 full")]


# ── Per-run metrics ───────────────────────────────────────────────────────────

def run_metrics(*, goal: Any, before: Any, after: Any, affected: list[str], target: str, selected: list[str],
                requirements_before: dict[str, bool | None], contracts_holding: int, contracts_total: int,
                failed: int, facts: dict[str, Any]) -> dict[str, Any]:
    """All v2 columns of one run (pure: graph snapshots in, numbers out)."""
    tpr = M.task_preservation(before, after, affected, target)
    exe = M.executability(after)
    req_after = M.user_requirements(goal, after)
    sat = M.requirement_satisfaction(req_after)
    regressions = M.requirement_regressions(requirements_before, req_after)
    cost = M.travel_cost_change(before, after)
    keep = M.activity_time_retention(before, after, affected)
    tasks = len(before.list_nodes())
    return {
        "facts": json.dumps(facts, sort_keys=True), "task_count": tasks,
        "replanning_scope": M.ratio(len(selected), tasks),
        "tpr_unaffected": tpr["unaffected"], "tpr_preserved": tpr["preserved"], "tpr": tpr["rate"],
        "tpr_visible_unaffected": tpr["visible_unaffected"], "tpr_visible_preserved": tpr["visible_preserved"],
        "tpr_visible": tpr["visible_rate"],
        **{k: exe[k] for k in ("e1_completion", "e2_output_valid", "e3_dependencies_evaluable", "e4_contracts_hold",
                               "plan_valid", "execution_ready", "awaiting_approval", "invalid_outputs",
                               "unevaluable_contracts", "broken_contracts", "task_plan_valid_rate",
                               "task_ready_rate")},
        **{f"req_{k}_before": requirements_before.get(k) for k in REQ},
        **{f"req_{k}": req_after.get(k) for k in REQ},
        "req_met": sat["met"], "req_checkable": sat["checkable"], "req_satisfaction": sat["rate"],
        "req_baseline_satisfaction": M.requirement_satisfaction(requirements_before)["rate"],
        "req_regressions": ";".join(regressions),
        "cost_total_before": cost["total_before"], "cost_total_after": cost["total_after"],
        "budget_limit": goal.budget,
        "cost_change_of_budget": cost["change_of_budget"], "within_budget_after": cost["within_budget_after"],
        "retention_days": keep["days"], "activity_minutes_before": keep["minutes_before"],
        "activity_minutes_after": keep["minutes_after"], "activity_time_retention": keep["rate"],
        "contract_recovered": M.contract_recovered(contracts_holding, contracts_total, failed),
        "strict_recovered": M.strict_recovered(exe["plan_valid"], regressions),
    }


# ── LLM guard ─────────────────────────────────────────────────────────────────

class RealLLMCalled(RuntimeError):
    """A real (network) LLM provider was invoked during the benchmark."""


@contextlib.contextmanager
def no_real_llm() -> Iterator[dict[str, int]]:
    """
    Fail loudly if any real LLM provider is called while the benchmark runs; the
    yielded dict records the number of attempts (it can only be 0 on success).
    """
    from app.llm.client import OpenAICompatibleLLM

    attempts = {"real_llm_calls": 0}
    original = OpenAICompatibleLLM._complete

    async def blocked(self, messages, *, json_mode):  # noqa: ARG001
        attempts["real_llm_calls"] += 1
        raise RealLLMCalled("a real LLM provider was called during the mock-mode benchmark")

    OpenAICompatibleLLM._complete = blocked
    try:
        yield attempts
    finally:
        OpenAICompatibleLLM._complete = original


def assert_mock_llm(orchestrator: Any) -> str:
    """The orchestrator and every agent must share the deterministic MockLLM."""
    from app.config import get_settings
    from app.llm.client import MockLLM

    if not get_settings().is_mock:
        raise RealLLMCalled("benchmark must run with VOYAGESYNC_MODE=mock")
    providers = getattr(orchestrator.registry, "providers", None)
    if not isinstance(orchestrator.llm, MockLLM) or (providers is not None and providers.llm is not orchestrator.llm):
        raise RealLLMCalled("benchmark LLM client is not the shared MockLLM")
    return orchestrator.llm.provider


# ── Parity ────────────────────────────────────────────────────────────────────

def scenario_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Every strategy (and drift mode) of a scenario must see the same event and oracle set A."""
    seen: dict[str, set] = defaultdict(set)
    strategies: dict[str, set] = defaultdict(set)
    for r in rows:
        seen[r["scenario"]].add((r["event_type"], r["target"], r["facts"], r["affected"]))
        strategies[r["scenario"]].add((r["drift"], r["strategy"]))
    expected = {k for v in strategies.values() for k in v}
    return {
        "scenarios": len(seen),
        "event_or_oracle_mismatches": sorted(s for s, v in seen.items() if len(v) != 1),
        "missing_strategy_runs": sorted(s for s, v in strategies.items() if v != expected),
    }


def legacy_parity(legacy_csv: Path, rows: list[dict[str, Any]], fields: list[str],
                  ignore: tuple[str, ...] = ("latency_ms",)) -> dict[str, Any]:
    """Compare the legacy columns of *rows* with an existing results CSV, ignoring timing columns."""
    if not legacy_csv.exists():
        return {"file": str(legacy_csv), "available": False}
    with open(legacy_csv, encoding="utf-8") as fh:
        old = {(r["scenario"], r["strategy"]): r for r in csv.DictReader(fh)}
    compared, mismatches, missing = 0, [], []
    for r in rows:
        key = (r["scenario"], r["strategy"])
        if key not in old:
            missing.append(key)
            continue
        compared += 1
        for f in fields:
            if f in ignore:
                continue
            new_value = "" if r[f] is None else str(r[f])
            if new_value != old[key][f]:
                mismatches.append({"scenario": key[0], "strategy": key[1], "field": f,
                                   "legacy": old[key][f], "new": new_value})
    return {"file": str(legacy_csv), "available": True, "legacy_rows": len(old), "compared": compared,
            "missing_in_legacy": len(missing), "not_rerun": len(old) - compared,
            "mismatch_count": len(mismatches), "mismatches": mismatches[:25], "ignored_fields": list(ignore)}


# ── Aggregation ───────────────────────────────────────────────────────────────

def _stat(values: list[Any]) -> dict[str, Any]:
    """Mean over defined values; booleans count as 1/0. n = defined, na = undefined (never imputed)."""
    defined = [float(v) for v in values if v is not None]
    return {"value": round(statistics.fmean(defined), 4) if defined else None, "n": len(defined),
            "na": len(values) - len(defined)}


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def aggregate_v2(rows: list[dict[str, Any]], strategies: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cfg in strategies:
        runs = [r for r in rows if r["strategy"] == cfg.name and r["status"] in ("replanned", "no_effect")]
        col = lambda k: [r[k] for r in runs]  # noqa: E731
        latencies = [r["latency_ms"] / 1000 for r in runs]
        out[cfg.name] = {
            "label": cfg.label, "runs": len(runs),
            # 1. executability
            "plan_valid": _stat(col("plan_valid")), "execution_ready": _stat(col("execution_ready")),
            "e1_completion": _stat(col("e1_completion")), "e2_output_valid": _stat(col("e2_output_valid")),
            "e3_dependencies_evaluable": _stat(col("e3_dependencies_evaluable")),
            "e4_contracts_hold": _stat(col("e4_contracts_hold")),
            "task_plan_valid_rate": _stat(col("task_plan_valid_rate")),
            "task_ready_rate": _stat(col("task_ready_rate")),
            "runs_with_awaiting_approval": sum(1 for r in runs if r["awaiting_approval"]),
            # 2. constraint satisfaction (two separate metrics)
            "contract_satisfaction": _stat(col("constraint_satisfaction")),
            "req_satisfaction": _stat(col("req_satisfaction")),
            "req_baseline_satisfaction": _stat(col("req_baseline_satisfaction")),
            **{f"req_{k}": _stat(col(f"req_{k}")) for k in REQ},
            **{f"req_{k}_before": _stat(col(f"req_{k}_before")) for k in REQ},
            "req_regression_rate": _stat([bool(r["req_regressions"]) for r in runs]),
            "req_regressions_by_requirement": {k: sum(1 for r in runs if k in r["req_regressions"].split(";"))
                                               for k in REQ},
            # 3. time & cost efficiency
            "resource_mock_llm_calls": _stat(col("llm_calls")), "resource_api_calls": _stat(col("api_calls")),
            "metric_llm_calls_total": sum(r["metric_llm_calls"] for r in runs),
            "travel_cost_change_of_budget": _stat(col("cost_change_of_budget")),
            "within_budget_after": _stat(col("within_budget_after")),
            "activity_time_retention": _stat(col("activity_time_retention")),
            # 4–7
            "latency_median_s": round(statistics.median(latencies), 6) if latencies else None,
            "latency_p95_s": round(_quantile(latencies, 0.95), 6) if latencies else None,
            "tpr": _stat(col("tpr")), "tpr_visible": _stat(col("tpr_visible")),
            "replanning_scope": _stat(col("replanning_scope")),
            "contract_recovery": _stat(col("contract_recovered")),
            "strict_recovery": _stat(col("strict_recovered")),
            # secondary (appendix only)
            "precision": _stat(col("precision")), "recall": _stat(col("recall")), "f1": _stat(col("f1")),
            "legacy_stability": _stat(col("stability")),
        }
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)   # None → empty cell: missing values stay missing


# ── Report ────────────────────────────────────────────────────────────────────

def _pct(stat: dict[str, Any] | None, digits: int = 1) -> str:
    if not stat or stat["value"] is None:
        return "N/A"
    return f"{stat['value'] * 100:.{digits}f} %"


def _num(stat: dict[str, Any] | None, digits: int = 2) -> str:
    return "N/A" if not stat or stat["value"] is None else f"{stat['value']:.{digits}f}"


def _n(stat: dict[str, Any]) -> str:
    return f"n={stat['n']}" + (f", N/A={stat['na']}" if stat["na"] else "")


def _seconds(v: float | None) -> str:
    return "N/A" if v is None else f"{v:.4f} s"


def _main_table(agg: dict[str, Any]) -> list[str]:
    rows = [
        ("1 Executability: plan-valid runs", "plan_valid", _pct),
        ("1 Executability: execution-ready runs", "execution_ready", _pct),
        ("1 Executability: task-level plan validity", "task_plan_valid_rate", _pct),
        ("1 Executability: task-level execution readiness", "task_ready_rate", _pct),
        ("2a Contract satisfaction", "contract_satisfaction", _pct),
        ("2b User-requirement satisfaction (after replan)", "req_satisfaction", _pct),
        ("2c Requirement regression rate", "req_regression_rate", _pct),
        ("3a Resource usage: MockLLM invocations / run", "resource_mock_llm_calls", _num),
        ("3a Resource usage: provider API calls / run", "resource_api_calls", _num),
        ("3b Travel cost change (share of budget)", "travel_cost_change_of_budget", lambda s: _pct(s, 2)),
        ("3b Within budget after replan", "within_budget_after", _pct),
        ("3c Activity-time retention (affected day plans)", "activity_time_retention", _pct),
        ("5 Task Preservation Rate (unaffected tasks)", "tpr", lambda s: _pct(s, 2)),
        ("5 Task Preservation Rate (unaffected, user-visible)", "tpr_visible", lambda s: _pct(s, 2)),
        ("6 Replanning scope", "replanning_scope", _pct),
        ("7a Contract recovery rate", "contract_recovery", _pct),
        ("7b Strict recovery rate", "strict_recovery", _pct),
    ]
    head = "| Metric | " + " | ".join(label for _, label in MAIN) + " |"
    lines = [head, "|---|" + "---|" * len(MAIN)]
    for title, key, fmt in rows:
        cells = [f"{fmt(agg[name][key])} ({_n(agg[name][key])})" for name, _ in MAIN]
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    lat = [f"{_seconds(agg[name]['latency_median_s'])} / {_seconds(agg[name]['latency_p95_s'])} "
           f"(n={agg[name]['runs']})" for name, _ in MAIN]
    # after header, separator and the twelve metric rows of 1–3 (latency is metric 4)
    lines.insert(2 + 12, "| 4 Replanning latency, median / p95 (mock mode) | " + " | ".join(lat) + " |")
    return lines


STATUS_TABLE = """\
| # | Metric | Status | Why |
|---|---|---|---|
| 1 | Executability | **Measured** (operational definition) | Task completion, output validators, contract evaluability and contract validity are all observable. **Not** TDAG's definition, which has not been verified. |
| 2a | Contract satisfaction | **Measured** | Unchanged existing metric: share of edge contracts holding. |
| 2b | User-requirement satisfaction | **Partially measured** | 4 requirements checkable (dates, party size, budget, interests). Accommodation type, pace, flexible dates and notes are not in the outputs. |
| 3a | Resource usage (LLM and API calls) | **Measured** | Counts of calls, not money. LLM calls are deterministic MockLLM invocations (no model, no network). |
| 3b | Travel cost change | **Measured** | From the budget task's totals. |
| 3c | Activity-time retention | **Measured where applicable** | Defined only when the oracle marks a day plan affected; otherwise N/A. |
| 3 | A single TDAG-style time & cost efficiency score | **Unavailable** | TDAG's formula has not been verified; no combined score is invented. |
| 4 | Replanning latency | **Measured** (mock mode) | Wall-clock time event → valid plan without network; not comparable to live APIs. |
| 5 | Task Preservation Rate | **Measured** | Over unaffected tasks only (U = all − A − target). |
| 6 | Replanning scope | **Measured** | Tasks marked for regeneration ÷ all tasks. |
| 7a | Contract recovery rate | **Measured** | Unchanged existing definition. |
| 7b | Strict recovery rate | **Measured** | Plan-valid and no requirement regression. |
"""

DEFINITIONS = """\
Notation, per run: **V** all tasks of the trip; **t** the disrupted target task; **A** the oracle's affected
set (computed once per scenario, no drift); **R** the tasks the strategy marked for regeneration; *before* the
booked plan before the event, *after* the plan after the replan and the simulated user's approvals.
*Unchanged* = same traveller-visible identity for flights (number, times, status), stay (hotel, dates, room),
transfers (hotel, pickup, drop-off) and day plans (activity names and start times); same output, ignoring
booking bookkeeping, for weather and budget tasks. **N/A** = a zero denominator or nothing checkable: excluded
from means, counted separately, never imputed.

1. **Executability** (operational; not TDAG's definition). Run-level checks:
   E1 every task DONE or AWAITING_APPROVAL with an output; E2 every output passes the agents' validators;
   E3 every edge contract can be evaluated (no "Invalid input" / "cannot be checked"); E4 every contract holds.
   *Plan-valid run* = E1 ∧ E2 ∧ E3 ∧ E4. *Execution-ready run* = plan-valid ∧ every task DONE (a task awaiting
   approval is a valid plan but not executable). *Task-level plan validity* = tasks with a plan-valid status and a
   valid output ÷ |V|; *task-level execution readiness* = DONE tasks with a valid output ÷ |V|.
2. **Constraint satisfaction**, two separate metrics.
   2a *Contract satisfaction* = contracts holding ÷ contracts (existing). 2b *User-requirement satisfaction* =
   requirements met ÷ checkable requirements, over {dates, party size (rooms × 2 ≥ travellers), budget
   (total ≤ budget), interests (every day after arrival themed on a requested interest)}. 2c *Requirement
   regression rate* = runs where a requirement that held before the event fails after ÷ runs.
3. **Time & cost efficiency**, never mixed. 3a *Resource usage*: MockLLM invocations and provider API calls per
   replan (counts, not money). 3b *Travel cost change* = (total after − total before) ÷ budget limit; and the share
   of runs within budget after. 3c *Activity-time retention* = Σ activity minutes after ÷ Σ before over the day
   plans in A (N/A when A has no day plan).
4. **Replanning latency** = wall-clock time from the event to a valid plan (median, p95), in seconds.
5. **Task Preservation Rate** = |unchanged tasks in U| ÷ |U| with U = V − A − {t}; also for the user-visible part
   of U. Empty U → N/A. (The existing *plan stability* metric is kept unchanged in the appendix; it uses all
   user-visible tasks except t as its denominator, including affected ones.)
6. **Replanning scope** = |R| ÷ |V|.
7. **Recovery success**, two separate metrics. 7a *Contract recovery* (existing) = every contract holds and no task
   failed. 7b *Strict recovery* = plan-valid (E1–E4) and no requirement regression.
"""


def markdown_report(summary: dict[str, Any], strategies: list[Any]) -> str:
    meta = summary["meta"]
    lines = [
        "# VoyageSync evaluation metrics v2: comparison report (generated)",
        "",
        f"Generated by `eval/run_benchmark.py --metrics v2`. {meta['scenarios']} scenarios × "
        f"{len(meta['strategies'])} strategies × drift {', '.join(meta['drift_modes'])}; runtime {meta['runtime_s']} s. "
        "Mock mode only. The original results (`eval/results/`, `eval/RESULTS.md`) are unchanged.",
        "",
        "## LLM usage check",
        "",
        f"- Mode: `{meta['mode']}`; LLM provider used by every orchestrator and agent: `{', '.join(meta['llm_providers'])}`.",
        f"- **Real LLM calls: {meta['real_llm_calls']}** (any call to the network LLM client fails the run).",
        f"- **LLM calls made while computing metrics: {meta['metric_llm_calls_total']}** (all runs).",
        f"- MockLLM invocations by the agents during replanning are reported as resource usage (3a); MockLLM is "
        f"deterministic template code, not a model. The oracle made {meta['oracle_mock_llm_calls_total']} MockLLM "
        "invocations across all scenarios (its agents regenerate tasks to find A).",
        "",
        "## Metric status",
        "",
        STATUS_TABLE,
    ]
    for mode in meta["drift_modes"]:
        agg = summary["aggregate_v2"][mode]
        lines += ["", f"## Results: B1 vs B2 vs ours (drift {mode})", "",
                  "Values are means over the strategy's runs; n = runs where the metric is defined, "
                  "N/A = runs where it is not.", ""]
        lines += _main_table(agg)
        lines += ["", f"### User requirements (drift {mode}): share of runs meeting each requirement", "",
                  "| Requirement | Before the event (all strategies) | " +
                  " | ".join(f"After: {label}" for _, label in MAIN) + " | Regressions (ours / B2 / B1) |",
                  "|---|---|" + "---|" * len(MAIN) + "---|"]
        for k in REQ:
            reg = " / ".join(str(agg[name]["req_regressions_by_requirement"][k]) for name, _ in MAIN)
            lines.append(f"| {k} | {_pct(agg['ours'][f'req_{k}_before'])} ({_n(agg['ours'][f'req_{k}_before'])}) | "
                         + " | ".join(f"{_pct(agg[name][f'req_{k}'])} ({_n(agg[name][f'req_{k}'])})"
                                      for name, _ in MAIN) + f" | {reg} |")
        lines += ["", f"### Executability detail (drift {mode})", "",
                  "| Check | " + " | ".join(label for _, label in MAIN) + " |", "|---|" + "---|" * len(MAIN)]
        for key, title in (("e1_completion", "E1 completion"), ("e2_output_valid", "E2 outputs valid"),
                           ("e3_dependencies_evaluable", "E3 dependencies evaluable"),
                           ("e4_contracts_hold", "E4 contracts hold")):
            lines.append(f"| {title} | " + " | ".join(_pct(agg[name][key]) for name, _ in MAIN) + " |")
        lines.append("| Runs with a task awaiting approval | "
                     + " | ".join(str(agg[name]["runs_with_awaiting_approval"]) for name, _ in MAIN) + " |")

    lines += ["", "## Definitions and formulas", "", DEFINITIONS]
    lines += ["", "## Scenario parity", ""]
    for mode, par in summary["parity"]["within_run"].items():
        lines.append(f"- Drift {mode}: {par['scenarios']} scenarios; scenarios whose event or oracle set A differs "
                     f"between strategies: **{len(par['event_or_oracle_mismatches'])}**; scenarios missing a "
                     f"strategy run: **{len(par['missing_strategy_runs'])}**.")
    for mode, par in summary["parity"]["legacy"].items():
        if not par.get("available"):
            lines.append(f"- Drift {mode}: legacy file `{par['file']}` not found; no comparison.")
            continue
        lines.append(f"- Drift {mode}: compared {par['compared']} runs with `{Path(par['file']).name}` on every "
                     f"original column except {', '.join(par['ignored_fields'])}: **{par['mismatch_count']} "
                     f"mismatches**; runs not re-run: {par['not_rerun']}; runs missing from the legacy file: "
                     f"{par['missing_in_legacy']}.")
    lines += ["", "## Unrecovered and non-strict runs (ours, drift on)", ""]
    unrec = summary["details"]["ours_unrecovered"]
    lines.append(f"- Contract recovery failed in {len(unrec)} run(s): "
                 + (", ".join(f"`{s}`" for s in unrec) if unrec else "none") + ".")
    strict = summary["details"]["ours_not_strict"]
    lines.append(f"- Strict recovery failed in {len(strict)} run(s); reasons: "
                 + (", ".join(f"{k}: {v}" for k, v in summary["details"]["ours_not_strict_reasons"].items())
                    or "none") + ".")
    over = summary["details"]["trips_over_budget_before"]
    lines.append(f"- Trips already over the user's budget **before** any disruption: {len(over)} of "
                 f"{summary['details']['trips']} ({', '.join(over) or 'none'}). Their budget requirement is false "
                 "before and after for every strategy, so it lowers 2b equally and is not a regression.")

    lines += ["", "## Where a baseline scores better than ours (generated from the runs)", "",
              "Runs where ours misses the budget requirement after the replan but B2 or B1 meets it, with the trip "
              "total after the replan:", "",
              "| Scenario | Drift | Budget | Total before | Ours | B2 naive | B1 full |", "|---|---|---|---|---|---|---|"]
    for w in summary["details"]["ours_budget_worse"]:
        lines.append(f"| `{w['scenario']}` | {w['drift']} | {w['budget']:g} | {w['before']:.2f} | {w['ours']:.2f} | "
                     f"{w['b2_naive']:.2f} | {w['b1_full']:.2f} |")
    if not summary["details"]["ours_budget_worse"]:
        lines.append("| none | | | | | | |")
    lines += ["", "Mean travel cost change (share of budget) by disruption kind:", "",
              "| Kind | Drift | Ours | B2 naive | B1 full |", "|---|---|---|---|---|"]
    for row in summary["details"]["cost_change_by_kind"]:
        lines.append(f"| {row['kind']} | {row['drift']} | " + " | ".join(
            "N/A" if row[n] is None else f"{row[n] * 100:+.2f} %" for n, _ in MAIN) + " |")
    lines += ["", "Ours keeps existing bookings and, with frozen context, prefers the replacement that changes the "
              "plan least (e.g. the flight closest in time) over the cheapest one. B1 and B2 re-search without "
              "frozen context and take the cheapest valid option, and under market drift a re-search can also "
              "come back cheaper. The rows above are where that difference decides the budget requirement.",
              ""]

    lines += ["", "## Appendix A: all six strategies (drift on)", "",
              "| Strategy | Plan-valid | Contract sat. | Req. sat. | Regression | TPR | Scope | Strict recovery | MockLLM / API calls |",
              "|---|---|---|---|---|---|---|---|---|"]
    agg_on = summary["aggregate_v2"][meta["drift_modes"][0]]
    for cfg in strategies:
        a = agg_on[cfg.name]
        lines.append(f"| {cfg.label} | {_pct(a['plan_valid'])} | {_pct(a['contract_satisfaction'])} | "
                     f"{_pct(a['req_satisfaction'])} | {_pct(a['req_regression_rate'])} | {_pct(a['tpr'], 2)} | "
                     f"{_pct(a['replanning_scope'])} | {_pct(a['strict_recovery'])} | "
                     f"{_num(a['resource_mock_llm_calls'])} / {_num(a['resource_api_calls'])} |")
    lines += ["", "## Appendix B: secondary metrics (not primary criteria)", "",
              "Replan precision / recall / F1 against the oracle, and the original plan-stability metric, kept "
              "for continuity with `eval/RESULTS.md`.", "",
              "| Strategy | Precision | Recall | F1 | Plan stability (original definition) |", "|---|---|---|---|---|"]
    for name, label in MAIN:
        a = agg_on[name]
        lines.append(f"| {label} | {_pct(a['precision'])} | {_pct(a['recall'])} | {_pct(a['f1'])} | "
                     f"{_pct(a['legacy_stability'])} |")
    lines += ["", "## Limitations", "",
              "- **Not TDAG metrics.** Executability, constraint satisfaction and time & cost efficiency here are "
              "operational definitions over our own outputs. TDAG's (ItineraryBench) scoring rules were not verified "
              "and no 60/20/20 combined score is computed.",
              "- **Synthetic data.** Mock fares do not depend on distance, which is why some trips are over budget "
              "before any disruption; the drift model (±8 % price, 15 % unavailability) is an assumption.",
              "- **Contract-based ground truth.** A, which defines \"unaffected\" for TPR and the affected day plans for "
              "retention, comes from the contract-based oracle; it cannot see dependencies no contract encodes.",
              "- **Requirement coverage.** Only 4 user requirements are checkable; interests are checked through "
              "day themes, and party size assumes 2 guests per room.",
              "- **Approvals are simulated.** Every pending T2 action is approved by a simulated user (or executed "
              "without approval in the no-gate ablation), so execution readiness after the run reflects that policy.",
              "- **Latency is mock-mode wall-clock time** and varies between machines; it is excluded from parity.",
              "- **Single-run benchmark.** The pipeline is deterministic apart from latency, so there is no "
              "run-to-run variance to report; there are no confidence intervals.",
              ""]
    return "\n".join(lines)


def _budget_worse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs where ours fails the budget requirement after the replan but B2 or B1 meets it."""
    by = {(r["scenario"], r["drift"], r["strategy"]): r for r in rows}
    out = []
    for (scenario, drift, strategy), r in sorted(by.items()):
        if strategy != "ours" or r["req_budget"] is not False:
            continue
        others = {n: by.get((scenario, drift, n)) for n in ("b2_naive", "b1_full")}
        if any(o is not None and o["req_budget"] is True for o in others.values()):
            out.append({"scenario": scenario, "drift": drift, "budget": float(r.get("budget_limit") or 0),
                        "before": r["cost_total_before"], "ours": r["cost_total_after"],
                        **{n: (o["cost_total_after"] if o else None) for n, o in others.items()}})
    return out


def _cost_by_kind(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["cost_change_of_budget"] is not None:
            grouped[(r["kind"], r["drift"])][r["strategy"]].append(r["cost_change_of_budget"])
    table = []
    for (kind, drift), per in sorted(grouped.items()):
        table.append({"kind": kind, "drift": drift,
                      **{n: (round(statistics.fmean(per[n]), 4) if per.get(n) else None) for n, _ in MAIN}})
    return table


def details(rows: list[dict[str, Any]], mode: str = "on") -> dict[str, Any]:
    ours = [r for r in rows if r["strategy"] == "ours" and r["drift"] == mode]
    reasons: dict[str, int] = defaultdict(int)
    for r in ours:
        if not r["strict_recovered"]:
            if not r["plan_valid"]:
                reasons["plan not valid"] += 1
            for k in filter(None, r["req_regressions"].split(";")):
                reasons[f"regression: {k}"] += 1
    trips = {r["trip"]: r["req_budget_before"] for r in ours}
    return {
        "ours_unrecovered": sorted(r["scenario"] for r in ours if not r["contract_recovered"]),
        "ours_not_strict": sorted(r["scenario"] for r in ours if not r["strict_recovered"]),
        "ours_not_strict_reasons": dict(reasons),
        "trips": len(trips),
        "trips_over_budget_before": sorted(t for t, ok in trips.items() if ok is False),
        "ours_budget_worse": _budget_worse(rows),
        "cost_change_by_kind": _cost_by_kind(rows),
    }
