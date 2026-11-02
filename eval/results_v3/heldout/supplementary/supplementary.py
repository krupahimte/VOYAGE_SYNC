"""Supplementary held-out analyses, read-only. Uses report_v3's own functions; writes JSON to the scratchpad."""
import json, statistics, sys
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(r"C:\Users\admin1\Desktop\VOYAGE_SYNC")
sys.path.insert(0, str(ROOT))
import yaml
from eval import report_v3 as rp, stats as st

H = ROOT / "eval/results_v3/heldout"
paths = sorted((H / "main").glob("*/runs.csv")) + sorted((H / "e2_prime").glob("*/runs.csv"))
rows = rp.load_rows(paths)
truth = yaml.safe_load((ROOT / "eval/scenarios_v3/ground_truth_heldout_stress.yaml").read_text(encoding="utf-8"))["scenarios"]
large = {sid for sid, e in truth.items() if (e["drift_on"].get("complete_repairs") or 0) > 500}
out = {}

# 1. timeouts: where, and the sensitivity analysis excluding affected scenarios
to = [r for r in rows if r["timed_out"]]
out["timeouts"] = {"runs": len(to), "by": dict(Counter(f"{r['catalogue']}|{r['drift']}|{r['arm']}" for r in to)),
                   "scenarios_on": sorted({r["scenario"] for r in to if r["drift"] == "on"}),
                   "scenarios_off": sorted({r["scenario"] for r in to if r["drift"] == "off"}),
                   "infeasible_timed_out_runs": sum(not r["feasible"] for r in to)}
# did the timed-out runs get a worse fitness than the other seeds of the same arm/scenario?
cmp = []
for r in to:
    others = [o["fitness"] for o in rows if o["scenario"] == r["scenario"] and o["arm"] == r["arm"] and o["drift"] == r["drift"]
              and not o["timed_out"] and o["feasible"]]
    if others and r["feasible"]:
        cmp.append(round(r["fitness"] - statistics.fmean(others), 9))
out["timeouts"]["fitness_vs_other_seeds"] = {"n": len(cmp), "worse": sum(c < 0 for c in cmp), "equal": sum(c == 0 for c in cmp),
                                             "better": sum(c > 0 for c in cmp), "mean_diff": statistics.fmean(cmp) if cmp else None}
bad = {(r["scenario"], r["drift"]) for r in to}
clean = [r for r in rows if (r["scenario"], r["drift"]) not in bad]
res = rp.analyse(clean, 10, 50, large)
out["sensitivity_excluding_timeout_scenarios"] = {
    "excluded_scenarios_on": len({s for s, d in bad if d == "on"}),
    "holds": res["holds"], "ga_justified": res["ga_justified"],
    "tests": {k: {kk: v.get(kk) for kk in ("n", "wins", "losses", "a_better", "b_better", "ties", "rank_biserial", "p", "p_holm",
                                          "silent_a", "silent_b", "mean_diff_ci")} for k, v in res["tests"].items()}}

# 2. drift off: the same tests (secondary)
def analyse_drift(rows, drift):
    table = rp.scenario_table(rows, drift)
    both, stress = {"realistic", "stress"}, {"stress"}
    tests = {"H1": rp.dominance_test(table, "E2@10", "E1g", both), "H2": rp.fitness_test(table, "E3", "RS", stress),
             "H3": rp.fitness_test(table, "E4@10", "E2@50", stress),
             "H4 (E1 vs E2)": rp.silent_test(table, "E1", "E2@10", both), "H4 (E1 vs E3)": rp.silent_test(table, "E1", "E3", both),
             "H4 (E1 vs E4)": rp.silent_test(table, "E1", "E4@10", both)}
    adj = st.holm({k: v["p"] for k, v in tests.items()})
    for k, v in tests.items():
        v["p_holm"] = adj[k]
    return tests
out["drift_off_tests"] = analyse_drift(rows, "off")

# 3. per-scenario descriptive stats for H2/H3 (seed-mean fitness of each arm, feasible pairs)
table = rp.scenario_table(rows, "on")
def pair_desc(a, b, cats):
    va_, vb_ = [], []
    for _, va, vb in rp._pairs(table, a, b, cats):
        if va["feasible"] and vb["feasible"]:
            va_.append(va["fitness"]); vb_.append(vb["fitness"])
    d = [x - y for x, y in zip(va_, vb_)]
    f = lambda xs: {"mean": round(statistics.fmean(xs), 6), "sd": round(statistics.stdev(xs), 6), "median": round(statistics.median(xs), 6)}
    return {"n": len(d), a: f(va_), b: f(vb_), "diff": {**f(d), "min": round(min(d), 6), "max": round(max(d), 6)}}
out["desc"] = {"H2": pair_desc("E3", "RS", {"stress"}), "H3": pair_desc("E4@10", "E2@50", {"stress"}),
               "H1 fitness E2 vs E1g": pair_desc("E2@10", "E1g", {"realistic", "stress"})}
# H1/H4 per catalogue
out["H1_by_catalogue"] = {c: rp.dominance_test(table, "E2@10", "E1g", {c}) for c in ("realistic", "stress")}
out["H4_by_catalogue"] = {c: rp.silent_test(table, "E1", "E2@10", {c}) for c in ("realistic", "stress")}

# 4. primary pre-registered metric: Pareto dominance vs E1, each arm
ARMS = ["B1", "B2", "E1g", "E2@10", "E2@50", "E3", "E4@10", "RS", "EXH"]
out["dominance_vs_E1"] = {}
for cats, label in (({"realistic"}, "realistic"), ({"stress"}, "stress"), ({"realistic", "stress"}, "pooled")):
    out["dominance_vs_E1"][label] = {a: rp.dominance_test(table, a, "E1", cats) for a in ARMS}

# 5. optimality gap to EXH (feasible, EXH not truncated)
exh_trunc = {(r["scenario"]) for r in rows if r["arm"] == "EXH" and r["truncated"] and r["drift"] == "on"}
gap = {}
for cat in ("realistic", "stress"):
    for a in ["E1", "B1", "B2", "E1g", "E2@10", "E2@50", "E3", "E4@10", "RS"]:
        ds = []
        for sid, va, vb in rp._pairs(table, a, "EXH", {cat}):
            if va["feasible"] and vb["feasible"] and sid not in exh_trunc:
                ds.append(round(vb["fitness"] - va["fitness"], 9))
        gap[f"{cat}|{a}"] = {"n": len(ds), "mean_gap": round(statistics.fmean(ds), 6), "max_gap": round(max(ds), 6),
                             "optimal_share": round(sum(d <= 0 for d in ds) / len(ds), 4),
                             "beats_exh": sum(d < 0 for d in ds)}
out["optimality_gap_vs_exh"] = gap
out["exh_truncated_scenarios_on"] = len(exh_trunc)

# 6. full per-arm metric table (drift on and off), run level and scenario level
def arm_metrics(rows, drift):
    g = defaultdict(list)
    for r in rows:
        if r["drift"] == drift:
            g[(r["catalogue"], rp.arm_key(r))].append(r)
    res = {}
    for (cat, arm), rs in sorted(g.items()):
        eff = [r for r in rs if r["status"] != "no_effect"]
        feas = [r for r in rs if r["feasible"]]
        mean = lambda k, xs: (round(statistics.fmean([x[k] for x in xs if x[k] is not None]), 4)
                              if any(x[k] is not None for x in xs) else None)
        lat = sorted(r["latency_ms"] for r in rs if r["latency_ms"] is not None)
        res[f"{cat}|{arm}"] = {
            "runs": len(rs), "scenarios": len({r["scenario"] for r in rs}),
            "feasible_rate": round(len(feas) / len(rs), 4),
            "committed_rate": round(sum(r["committed"] for r in rs) / len(rs), 4),
            "conflict_rate": round(sum(r["conflict"] for r in rs) / len(rs), 4),
            "silent_failure_rate": round(sum(r["silent_failure"] for r in rs) / len(rs), 4),
            "mean_violations": round(statistics.fmean(int(r["violations"] or 0) for r in rs), 4),
            "regenerated_mean_effective": round(statistics.fmean(int(r["regenerated_count"] or 0) for r in eff), 4) if eff else None,
            "fitness_feasible": mean("fitness", feas),
            **{t: mean(t, feas) for t in rp.TERMS},
            "activity_retention": (round(1 - statistics.fmean(r["activity_time_lost"] for r in feas), 4) if feas else None),
            "necessity_coverage": mean("necessity_coverage", rs), "excess_scope": mean("excess_scope", rs),
            "scope_optimality": mean("scope_optimality", rs), "requirement_satisfaction": mean("requirement_satisfaction", rs),
            "legacy_precision": mean("legacy_precision", rs), "legacy_recall": mean("legacy_recall", rs),
            "plan_evaluations": mean("plan_evaluations", rs), "partial_evaluations": mean("partial_evaluations", rs),
            "option_generations": mean("option_generations", rs),
            "api_calls": mean("api_calls", rs), "llm_calls": mean("llm_calls", rs),
            "latency_ms_median": round(statistics.median(lat), 2), "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 2),
            "timed_out_runs": sum(r["timed_out"] for r in rs)}
    return res
out["arm_metrics_on"] = arm_metrics(rows, "on")
out["arm_metrics_off"] = arm_metrics(rows, "off")
# pooled across catalogues, drift on
pooled = defaultdict(list)
for r in rows:
    if r["drift"] == "on":
        r2 = dict(r); r2["catalogue"] = "pooled"; pooled[0].append(r2)
out["arm_metrics_on_pooled"] = arm_metrics(pooled[0], "on")
# per-scenario conflicts by kind (all repair arms identical?)
conf = Counter(r["scenario"].split("__")[1] for r in rows if r["arm"] == "E2" and r["beam_width"] == "10" and r["conflict"] and r["drift"] == "on")
out["conflict_scenarios_by_kind_E2"] = dict(conf)
sil = Counter(r["scenario"].split("__")[1] for r in rows if r["arm"] == "E1" and r["silent_failure"] and r["drift"] == "on")
out["silent_scenarios_by_kind_E1"] = dict(sil)
json.dump(out, open(sys.argv[1], "w"), indent=1, default=str)
print("ok")
