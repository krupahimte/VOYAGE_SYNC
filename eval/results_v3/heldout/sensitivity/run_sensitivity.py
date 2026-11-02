"""
Launcher of the pre-registered weight sensitivity (``objective.sensitivity``: "each weight x0.5 and x1.5, reported
without re-tuning") on the held-out split, drift on (the primary setting).

8 settings (one weight at 0.5 or 1.5, the others 1) x 2 catalogues x 20 trips, every arm, 20 seeds, W* = 10, plus
E2' (width 50) per setting and catalogue. Nothing is re-tuned. One job per trip and catalogue, 16 at a time.
A first job re-runs one trip with the default weights to check that the runner change left the default unchanged.

    backend\\.venv\\Scripts\\python.exe eval\\results_v3\\heldout\\sensitivity\\run_sensitivity.py
"""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
TERMS = ("disruption", "money", "activity_time_lost", "approvals")
SETTINGS = {f"{t}_x{f}": f"{t}={f}" for t in TERMS for f in ("0.5", "1.5")}
TRIPS = yaml.safe_load((ROOT / "eval" / "preregistration.yaml").read_text(encoding="utf-8"))["split"]["heldout_trips"]
PARALLEL = 16


def jobs() -> list[tuple[str, list[str]]]:
    base = [str(PY), "eval/experiments.py", "--split", "heldout", "--drift", "on"]
    out = [("parity_default/realistic_paris_couple_weekend",
            base + ["--catalogue", "realistic", "--seeds", "20", "--beam-width", "10", "--trips", "paris_couple_weekend"])]
    for cat in ("stress", "realistic"):           # the longer stress jobs first
        for name, weights in SETTINGS.items():
            for trip in TRIPS:
                out.append((f"{name}/main/{cat}_{trip}", base + ["--catalogue", cat, "--seeds", "20", "--beam-width",
                                                                 "10", "--trips", trip, "--weights", weights]))
    for name, weights in SETTINGS.items():
        for cat in ("stress", "realistic"):
            out.append((f"{name}/e2_prime/{cat}", base + ["--catalogue", cat, "--arms", "E2", "--seeds", "1",
                                                          "--beam-width", "50", "--weights", weights]))
    return out


def run(job: tuple[str, list[str]]) -> tuple[str, int]:
    rel, cmd = job
    target = OUT / rel
    if (target / "runs.csv").exists():
        return rel, 0                              # finished in an earlier launch
    log = OUT / "logs" / (rel.replace("/", "__") + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as fh:
        code = subprocess.call(cmd + ["--out", str(target)], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    return rel, code


def main() -> None:
    todo = jobs()
    started, failed = time.time(), []
    with ThreadPoolExecutor(PARALLEL) as pool:
        for i, (rel, code) in enumerate(pool.map(run, todo), 1):
            if code:
                failed.append(rel)
            print(f"[{i}/{len(todo)}] {rel}: {'ok' if code == 0 else f'exit {code}'} ({time.time() - started:.0f} s)",
                  flush=True)
    print("failed: " + ", ".join(failed) if failed else f"all {len(todo)} jobs completed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
