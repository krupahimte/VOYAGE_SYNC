"""
Analysis of the weight sensitivity runs: the pre-registered H1–H4 tests (``report_v3.analyse``, unchanged) on each
weight setting, next to the equal-weight held-out result. Each setting's fitness is the one its arms optimised.

    backend\\.venv\\Scripts\\python.exe eval\\results_v3\\heldout\\sensitivity\\analyse_sensitivity.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from eval import report_v3 as rp  # noqa: E402
from run_sensitivity import SETTINGS, TRIPS  # noqa: E402

HELDOUT = ROOT / "eval" / "results_v3" / "heldout"
VOLATILE = {"latency_ms"}


def raw(paths):
    import csv
    return [r for p in paths for r in csv.DictReader(open(p, encoding="utf-8"))]


def parity() -> dict:
    """The default-weight re-run of one trip must equal the main held-out rows (latency aside)."""
    new = raw([HERE / "parity_default" / "realistic_paris_couple_weekend" / "runs.csv"])
    old = [r for r in raw(sorted((HELDOUT / "main").glob("realistic_on_g*/runs.csv"))) if r["trip"] == "paris_couple_weekend"]
    key = lambda r: (r["scenario"], r["arm"], r["run"])  # noqa: E731
    strip = lambda r: {k: v for k, v in r.items() if k not in VOLATILE}  # noqa: E731
    o = {key(r): strip(r) for r in old}
    n = {key(r): strip(r) for r in new}
    diff = [k for k in o if o[k] != n.get(k)]
    return {"rows_old": len(o), "rows_new": len(n), "identical": not diff and set(o) == set(n), "differing": diff[:10]}


def complete(name: str) -> dict:
    missing = [f"{cat}_{t}" for cat in ("realistic", "stress") for t in TRIPS
               if not (HERE / name / "main" / f"{cat}_{t}" / "runs.csv").exists()]
    missing += [f"e2_prime/{c}" for c in ("realistic", "stress") if not (HERE / name / "e2_prime" / c / "runs.csv").exists()]
    return {"missing": missing}


def summary(result: dict) -> dict:
    t = result["tests"]
    keep = ("wins", "losses", "counts", "n", "a_better", "b_better", "ties", "rank_biserial", "mean_diff_ci",
            "silent_a", "silent_b", "p", "p_holm")
    return {"holds": result["holds"], "ga_justified": result["ga_justified"],
            "tests": {k: {kk: v[kk] for kk in keep if kk in v} for k, v in t.items()},
            "secondary": {k: {kk: v[kk] for kk in keep if kk in v} for k, v in result["secondary"].items()},
            "stress_fitness": {k.split("|")[1]: v["fitness"]["mean"] for k, v in result["arms"].items()
                               if k.startswith("stress|")},
            "plan_evaluations_stress": {k.split("|")[1]: v["plan_evaluations"] for k, v in result["arms"].items()
                                        if k.startswith("stress|")}}


def changed_choices(base_rows, rows) -> dict:
    """Scenarios where an arm's chosen repair differs from the equal-weight run (its terms differ)."""
    def plans(rs):
        out = defaultdict(set)
        for r in rs:
            if r["drift"] == "on" and r["feasible"]:
                out[(r["scenario"], rp.arm_key(r))].add(tuple(round(r[t], 6) for t in rp.TERMS))
        return out
    a, b = plans(base_rows), plans(rows)
    by_arm = defaultdict(lambda: [0, 0])
    for k in a.keys() & b.keys():
        by_arm[k[1]][0] += 1
        by_arm[k[1]][1] += a[k] != b[k]
    return {arm: {"scenarios": n, "changed": c} for arm, (n, c) in sorted(by_arm.items())}


def main() -> None:
    truth = yaml.safe_load((ROOT / "eval/scenarios_v3/ground_truth_heldout_stress.yaml").read_text(encoding="utf-8"))
    large = {sid for sid, e in truth["scenarios"].items() if (e["drift_on"].get("complete_repairs") or 0) > 500}
    base_rows = rp.load_rows(sorted((HELDOUT / "main").glob("*_on_g*/runs.csv"))
                             + sorted((HELDOUT / "e2_prime").glob("*_on/runs.csv")))
    out = {"parity": parity(), "settings": {}}
    out["settings"]["equal (main run)"] = summary(rp.analyse(base_rows, 10, 50, large))
    for name in SETTINGS:
        status = complete(name)
        if status["missing"]:
            out["settings"][name] = status
            continue
        rows = rp.load_rows(sorted((HERE / name / "main").glob("*/runs.csv"))
                            + sorted((HERE / name / "e2_prime").glob("*/runs.csv")))
        res = summary(rp.analyse(rows, 10, 50, large))
        res["rows"] = len(rows)
        res["timed_out_runs"] = sum(r["timed_out"] for r in rows)
        res["changed_choices"] = changed_choices(base_rows, rows)
        out["settings"][name] = res
    (HERE / "sensitivity_heldout.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"wrote {HERE / 'sensitivity_heldout.json'}")


if __name__ == "__main__":
    main()
