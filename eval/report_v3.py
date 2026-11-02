"""
Analysis of E1–E4 runs, exactly as pre-registered (``tests`` and ``statistics`` in
eval/preregistration.yaml): H1–H4 with Holm's adjustment, the GA-justification verdict, and the
secondary tables. Works on dev runs (labelled as dev, for tuning only) and on held-out runs.

    backend\\.venv\\Scripts\\python.exe eval\\report_v3.py --split dev --runs eval/results_v3/dev/full/runs.csv \\
        --runs eval/results_v3/dev/tuning/e2_w20/runs.csv --w-star 10 --w-prime 20 --out eval/results_v3/dev
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import stats as st  # noqa: E402

TERMS = ("disruption", "money", "activity_time_lost", "approvals")
NUMERIC = {"fitness", *TERMS, "plan_evaluations", "partial_evaluations", "option_generations", "api_calls",
           "llm_calls", "latency_ms", "requirement_satisfaction", "necessity_coverage", "excess_scope",
           "scope_optimality", "legacy_precision", "legacy_recall"}
BOOLS = {"feasible", "conflict", "silent_failure", "committed", "timed_out", "truncated"}


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for k in NUMERIC:
                    r[k] = float(r[k]) if r.get(k) not in (None, "", "None") else None
                for k in BOOLS:
                    r[k] = r.get(k) == "True"
                rows.append(r)
    return rows


def arm_key(r: dict[str, Any]) -> str:
    return f"{r['arm']}@{r['beam_width']}" if r["beam_width"] not in ("", None) else r["arm"]


def scenario_table(rows: list[dict[str, Any]], drift: str = "on") -> dict[tuple[str, str], dict[str, Any]]:
    """(scenario, arm key) → seed means; feasible only if every seed was."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["drift"] == drift:
            groups[(r["scenario"], arm_key(r))].append(r)
    table = {}
    for key, rs in groups.items():
        table[key] = {"trip": rs[0]["trip"], "catalogue": rs[0]["catalogue"], "seeds": len(rs),
                      "feasible": all(r["feasible"] for r in rs), "silent_failure": any(r["silent_failure"] for r in rs),
                      "conflict": all(r["conflict"] for r in rs),
                      "fitness": statistics.fmean(r["fitness"] for r in rs),
                      "fitness_sd": statistics.pstdev([r["fitness"] for r in rs]) if len(rs) > 1 else 0.0,
                      **{t: statistics.fmean(r[t] for r in rs) for t in TERMS},
                      "plan_evaluations": statistics.fmean(r["plan_evaluations"] for r in rs)
                      if all(r["plan_evaluations"] is not None for r in rs) else None}
    return table


def _pairs(table, a: str, b: str, catalogues: set[str], scenarios: set[str] | None = None):
    for (sid, arm), va in table.items():
        if arm != a or va["catalogue"] not in catalogues or (scenarios is not None and sid not in scenarios):
            continue
        vb = table.get((sid, b))
        if vb is not None:
            yield sid, va, vb


def dominance_test(table, a: str, b: str, catalogues: set[str]) -> dict[str, Any]:
    counts = defaultdict(int)
    for _, va, vb in _pairs(table, a, b, catalogues):
        if va["feasible"] and vb["feasible"]:
            counts[st.pareto(va, vb, TERMS)] += 1
    test = st.sign_test(counts["dominates"], counts["dominated"])
    return {"compare": f"{a} vs {b}", "counts": dict(counts), **test}


def fitness_test(table, a: str, b: str, catalogues: set[str], scenarios: set[str] | None = None) -> dict[str, Any]:
    diffs, by_trip = [], defaultdict(list)
    for _, va, vb in _pairs(table, a, b, catalogues, scenarios):
        if va["feasible"] and vb["feasible"]:
            # Fitness is rounded to 6 decimals; seed means of equal values can differ by ~1e-17.
            # Rounding the difference makes those exact ties (zeros) instead of random-sign noise.
            d = round(va["fitness"] - vb["fitness"], 9)
            diffs.append(d)
            by_trip[va["trip"]].append(d)
    if not diffs:
        return {"compare": f"{a} vs {b}", "n": 0, "p": 1.0}
    w = st.wilcoxon(diffs)
    return {"compare": f"{a} vs {b}", "n": len(diffs), "median_diff": round(statistics.median(diffs), 6),
            "mean_diff_ci": st.cluster_ci(dict(by_trip)), "rank_biserial": st.rank_biserial(diffs),
            "a_better": sum(d > 0 for d in diffs), "b_better": sum(d < 0 for d in diffs),
            "ties": sum(d == 0 for d in diffs), **{k: w[k] for k in ("p", "method", "zeros")}}


def silent_test(table, a: str, b: str, catalogues: set[str]) -> dict[str, Any]:
    only_a = only_b = 0
    total_a = total_b = 0
    for _, va, vb in _pairs(table, a, b, catalogues):
        total_a += va["silent_failure"]
        total_b += vb["silent_failure"]
        only_a += va["silent_failure"] and not vb["silent_failure"]
        only_b += vb["silent_failure"] and not va["silent_failure"]
    return {"compare": f"{a} vs {b}", "silent_a": total_a, "silent_b": total_b, **st.sign_test(only_a, only_b)}


def analyse(rows: list[dict[str, Any]], w_star: int, w_prime: int, large: set[str]) -> dict[str, Any]:
    table = scenario_table(rows, "on")
    both, stress = {"realistic", "stress"}, {"stress"}
    e2, e4, e2p = f"E2@{w_star}", f"E4@{w_star}", f"E2@{w_prime}"
    tests = {
        "H1": dominance_test(table, e2, "E1g", both),
        "H2": fitness_test(table, "E3", "RS", stress),
        "H3": fitness_test(table, e4, e2p, stress),
        "H4 (E1 vs E2)": silent_test(table, "E1", e2, both),
        "H4 (E1 vs E3)": silent_test(table, "E1", "E3", both),
        "H4 (E1 vs E4)": silent_test(table, "E1", e4, both),
    }
    adjusted = st.holm({k: v["p"] for k, v in tests.items()})
    for k, v in tests.items():
        v["p_holm"] = adjusted[k]
    h1, h2, h3 = tests["H1"], tests["H2"], tests["H3"]
    holds = {
        "H1": h1.get("wins", 0) > h1.get("losses", 0) and h1["p_holm"] < 0.05,
        "H2": (h2.get("rank_biserial") or 0) > 0 and h2["p_holm"] < 0.05,
        "H3": (h3.get("rank_biserial") or 0) > 0 and h3["p_holm"] < 0.05,
        "H4": all(tests[k]["silent_b"] == 0 and tests[k]["p_holm"] < 0.05 for k in tests if k.startswith("H4")),
    }
    secondary = {"H2 (large spaces)": fitness_test(table, "E3", "RS", stress, large),
                 "H3 (large spaces)": fitness_test(table, e4, e2p, stress, large)}
    return {"tests": tests, "holds": holds, "ga_justified": holds["H2"] and holds["H3"], "secondary": secondary,
            "arms": arm_summary(table), "drift_off_arms": arm_summary(scenario_table(rows, "off"))}


def arm_summary(table) -> dict[str, Any]:
    by_arm: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for (sid, arm), v in table.items():
        by_arm[(v["catalogue"], arm)].append(v)
    out = {}
    for (cat, arm), vs in sorted(by_arm.items()):
        feasible = [v for v in vs if v["feasible"]]
        by_trip = defaultdict(list)
        for v in feasible:
            by_trip[v["trip"]].append(v["fitness"])
        out[f"{cat}|{arm}"] = {
            "scenarios": len(vs), "feasible": len(feasible), "conflicts": sum(v["conflict"] for v in vs),
            "silent_failures": sum(v["silent_failure"] for v in vs),
            "fitness": st.cluster_ci(dict(by_trip), resamples=2000),
            "fitness_seed_sd_mean": round(statistics.fmean(v["fitness_sd"] for v in vs), 6),
            **{t: round(statistics.fmean(v[t] for v in feasible), 6) if feasible else None for t in TERMS},
            "plan_evaluations": round(statistics.fmean(v["plan_evaluations"] for v in vs
                                                       if v["plan_evaluations"] is not None), 2)
            if any(v["plan_evaluations"] is not None for v in vs) else None}
    return out


def markdown(result: dict[str, Any], split: str, w_star: int, w_prime: int) -> str:
    label = "DEV (tuning data; not evidence for any claim)" if split == "dev" else "HELD-OUT"
    lines = [f"# E1–E4 analysis: {label}", "",
             f"Generated by `eval/report_v3.py` as pre-registered. Drift on. W* = {w_star}, E2' = width {w_prime}.", "",
             "## Pre-registered tests (Holm-adjusted)", "",
             "| Test | Comparison | Result | p | p (Holm) |", "|---|---|---|---|---|"]
    for name, t in result["tests"].items():
        if "counts" in t:
            res = f"{t['wins']} dominate vs {t['losses']} dominated ({t['counts']})"
        elif "silent_a" in t:
            res = f"silent failures {t['silent_a']} vs {t['silent_b']}"
        else:
            ci = t.get("mean_diff_ci") or {}
            res = (f"n={t['n']}, median Δ={t.get('median_diff')}, mean Δ={ci.get('mean')} "
                   f"[{ci.get('low')}, {ci.get('high')}], better/worse/tie {t.get('a_better')}/{t.get('b_better')}/"
                   f"{t.get('ties')}, r={t.get('rank_biserial')}")
        lines.append(f"| {name} | {t['compare']} | {res} | {t['p']:.4g} | {t['p_holm']:.4g} |")
    lines += ["", "**Hypotheses:** " + ", ".join(f"{k} {'holds' if v else 'does not hold'}"
                                                   for k, v in result["holds"].items()),
              "", f"**GA justified (H2 and H3):** {'yes' if result['ga_justified'] else 'no'}", "",
              "## Secondary: large option spaces (more complete repairs than the budget)", "",
              "| Test | Comparison | n | median Δ | p (unadjusted) |", "|---|---|---|---|---|"]
    for name, t in result["secondary"].items():
        lines.append(f"| {name} | {t['compare']} | {t['n']} | {t.get('median_diff')} | {t['p']:.4g} |")
    for title, key in (("Per arm (drift on)", "arms"), ("Per arm (drift off, secondary)", "drift_off_arms")):
        lines += ["", f"## {title}", "", "| Catalogue | Arm | Scenarios | Feasible | Conflicts | Silent failures | "
                  "Fitness mean [95 % trip-bootstrap CI] | Seed SD | Disruption | Money | Activity lost | Approvals | "
                  "Plan evaluations |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, v in result[key].items():
            cat, arm = k.split("|")
            f = v["fitness"]
            lines.append(f"| {cat} | {arm} | {v['scenarios']} | {v['feasible']} | {v['conflicts']} | "
                         f"{v['silent_failures']} | {f['mean']} [{f['low']}, {f['high']}] | {v['fitness_seed_sd_mean']} | "
                         + " | ".join(str(v[t]) for t in TERMS) + f" | {v['plan_evaluations']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["dev", "heldout"], required=True)
    parser.add_argument("--runs", type=Path, action="append", required=True)
    parser.add_argument("--w-star", type=int, required=True)
    parser.add_argument("--w-prime", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import yaml

    gt_path = Path(__file__).resolve().parent / "scenarios_v3" / f"ground_truth_{args.split}_stress.yaml"
    truth = yaml.safe_load(gt_path.read_text(encoding="utf-8"))["scenarios"]
    large = {sid for sid, e in truth.items() if (e["drift_on"].get("complete_repairs") or 0) > 500}
    result = analyse(load_rows(args.runs), args.w_star, args.w_prime, large)
    result["large_space_scenarios"] = len(large)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"analysis_{args.split}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report = args.out / f"ANALYSIS_{args.split.upper()}.md"
    report.write_text(markdown(result, args.split, args.w_star, args.w_prime), encoding="utf-8")
    print(f"wrote {report}")      # the report itself has characters a Windows console code page cannot print


if __name__ == "__main__":
    main()
