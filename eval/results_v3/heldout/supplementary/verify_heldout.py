"""Read-only completeness and integrity checks of the held-out run. Writes nothing into the repo."""
import csv, json, sys, hashlib
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\admin1\Desktop\VOYAGE_SYNC")
sys.path.insert(0, str(ROOT))
import yaml
from eval import experiments as ex, scenario_set_v3 as v3, ground_truth as gt

H = ROOT / "eval/results_v3/heldout"
prereg = yaml.safe_load((ROOT / "eval/preregistration.yaml").read_text(encoding="utf-8"))
out = {"prereg_verify": None}
try:
    ex.verify_frozen(); out["prereg_verify"] = "ok"
except Exception as e:
    out["prereg_verify"] = f"FAILED: {e}"
out["prereg_file_sha256_raw"] = hashlib.sha256((ROOT / "eval/preregistration.yaml").read_bytes()).hexdigest()

# ground truth verification
gts = {}
for cat in ("realistic", "stress"):
    d = gt.load_verified("heldout", cat)
    gts[cat] = d
    p = ROOT / f"eval/scenarios_v3/ground_truth_heldout_{cat}.yaml"
    out[f"gt_{cat}_sha256"] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    out[f"gt_{cat}_frozen"] = prereg["frozen_values"]["ground_truth_sha256"][f"heldout_{cat}"]

groups = [g.split(",") for g in (H / "RUN_COMMANDS.sh").read_text().split("TRIP_GROUPS=(")[1].split(")")[0].split('"')[1::2]]
assert sorted(sum(groups, [])) == sorted(prereg["split"]["heldout_trips"]), "groups != prereg held-out trips"
out["groups_match_prereg_trips"] = True
dev = set(prereg["split"]["dev_trips"])

problems = []
totals = {}
all_rows = []
for cat in ("realistic", "stress"):
    scen = {s["id"]: s for s in v3.load("heldout", cat)}
    assert not (set(s["trip"] for s in scen.values()) & dev)
    for drift in ("on", "off"):
        seen_scen = Counter()
        for gi, trips in enumerate(groups):
            d = H / f"main/{cat}_{drift}_g{gi}"
            man = json.loads((d / "manifest.json").read_text())
            if man["preregistration_digest"] != prereg["frozen_sha256"] or man["preregistration_status"] != "frozen":
                problems.append(f"{d}: manifest digest/status")
            cfg = man["configs"][0]
            if (cfg["seeds"], cfg["beam_width"], cfg["budget"], cfg["base_seed"]) != (20, 10, 500, 0):
                problems.append(f"{d}: config {cfg}")
            rows = list(csv.DictReader(open(d / "runs.csv", encoding="utf-8")))
            all_rows += rows
            expect = {sid for sid, s in scen.items() if s["trip"] in trips}
            got = defaultdict(list)
            for r in rows:
                got[r["scenario"]].append(r)
                if r["drift"] != drift or r["catalogue"] != cat or r["split"] != "heldout":
                    problems.append(f"{d}: row label {r['drift']} {r['catalogue']} {r['split']}")
            if set(got) != expect:
                problems.append(f"{d}: scenarios missing {len(expect - set(got))}, extra {len(set(got) - expect)}")
            for sid, rs in got.items():
                seen_scen[sid] += 1
                c = Counter(r["arm"] for r in rs)
                want = {a: (20 if a in ex.STOCHASTIC else 1) for a in ex.ARMS}
                if dict(c) != want:
                    problems.append(f"{sid}: arm counts {dict(c)}")
                for a in ex.STOCHASTIC:
                    runs = sorted(int(r["run"]) for r in rs if r["arm"] == a)
                    if runs != list(range(20)):
                        problems.append(f"{sid} {a}: runs {runs}")
                    if len({r["seed"] for r in rs if r["arm"] == a}) != 20:
                        problems.append(f"{sid} {a}: seeds not distinct")
                s = scen[sid]
                for r in rs:
                    if r["trip"] != s["trip"] or r["kind"] != s.get("kind", r["kind"]):
                        problems.append(f"{sid}: trip/kind mismatch"); break
                    if r["beam_width"] not in ("", "10") or (r["arm"] in ("E2", "E4") and r["beam_width"] != "10"):
                        problems.append(f"{sid}: beam width {r['beam_width']}")
            totals[f"{cat}_{drift}"] = totals.get(f"{cat}_{drift}", 0) + len(rows)
            totals[f"{cat}_{drift}_g{gi}"] = len(rows)
        dup = [k for k, v in seen_scen.items() if v > 1]
        missing = set(scen) - set(seen_scen)
        out[f"scenarios_{cat}_{drift}"] = {"expected": len(scen), "found": len(seen_scen), "duplicates_across_groups": len(dup), "missing": len(missing)}
        # E2'
        d = H / f"e2_prime/{cat}_{drift}"
        man = json.loads((d / "manifest.json").read_text())
        cfg = man["configs"][0]
        rows = list(csv.DictReader(open(d / "runs.csv", encoding="utf-8")))
        ids = Counter(r["scenario"] for r in rows)
        out[f"e2prime_{cat}_{drift}"] = {"rows": len(rows), "scenarios": len(ids), "max_rows_per_scenario": max(ids.values()),
                                         "missing": len(set(scen) - set(ids)), "arms": sorted({r['arm'] for r in rows}),
                                         "beam_widths": sorted({r['beam_width'] for r in rows}),
                                         "digest_ok": man["preregistration_digest"] == prereg["frozen_sha256"],
                                         "seeds_cfg": cfg["seeds"]}

out["rows"] = totals
out["rows_main_total"] = len(all_rows)
flags = Counter()
for r in all_rows:
    for k in ("timed_out", "truncated"):
        if r[k] == "True":
            flags[(k, r["catalogue"], r["drift"], r["arm"])] += 1
    if r["status"] in ("error", "failed", "") :
        flags[("status_" + r["status"], r["catalogue"], r["drift"], r["arm"])] += 1
    if r["fitness"] in ("", "None"):
        flags[("no_fitness", r["arm"])] += 1
out["flags"] = {"|".join(k): v for k, v in sorted(flags.items())}
out["status_counts"] = dict(Counter(r["status"] for r in all_rows))
out["stopped_counts"] = dict(Counter(r["arm"] + "|" + r["stopped"] for r in all_rows if r["stopped"]).most_common())
lat = [float(r["latency_ms"]) for r in all_rows if r["latency_ms"] not in ("", "None")]
out["latency_ms_max"] = max(lat)
out["latency_over_2s"] = dict(Counter(r["arm"] for r in all_rows if r["latency_ms"] not in ("", "None") and float(r["latency_ms"]) > 2000))

# deterministic arms identical across groups? (each scenario only in one group; check E1 vs ground-truth targets instead)
# oracle: necessity coverage defined iff ground truth has a necessity set
mism = 0
for r in all_rows:
    e = gts[r["catalogue"]]["scenarios"].get(r["scenario"])
    if e is None:
        mism += 1
out["rows_without_ground_truth"] = mism
out["problems"] = problems[:50]
out["problem_count"] = len(problems)
print(json.dumps(out, indent=1, default=str))
