"""
Golden snapshot of E1 (contract PROPAGATE, the "ours" benchmark strategy) for the
hybrid work: every scenario × drift on/off, recorded before any agent refactor.

For each planned trip it records every node's status and a hash of its output;
for each scenario × drift mode it records the oracle set A, the replan's
stale / regenerated / changed / failed lists, the Risk Gate actions raised, and
every node's status and output hash after the simulated user approved. Phase 1
(candidate generation) must leave all of it byte-identical.

Outputs are hashed as canonical JSON. Only fields that differ between two runs of
the same code are left out (VOLATILE_KEYS: booking references, timestamps and
action IDs are random or wall-clock).

    backend\\.venv\\Scripts\\python.exe eval\\golden.py --write    # record eval/golden/e1_snapshot.json
    backend\\.venv\\Scripts\\python.exe eval\\golden.py            # compare with the recorded snapshot
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenario_set import EVAL_DIR, load_scenarios, load_trips, use_backend  # noqa: E402

use_backend()

from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.risk_gate import ActionStatus  # noqa: E402
from eval.baselines import BY_NAME  # noqa: E402
from eval.oracle import ground_truth  # noqa: E402
from eval.run_benchmark import DRIFT, PlannedTrip  # noqa: E402

SNAPSHOT_PATH = EVAL_DIR / "golden" / "e1_snapshot.json"
STRATEGY = BY_NAME["ours"]
# Random or wall-clock fields; everything else must match exactly.
VOLATILE_KEYS = frozenset({"booking_ref", "booked_at", "approval_id"})


def output_hash(output: Any) -> str:
    stable = {k: v for k, v in (output or {}).items() if k not in VOLATILE_KEYS}
    text = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def nodes_state(store: Any) -> dict[str, list[str]]:
    return {n.id: [n.status.value, output_hash(n.output)] for n in sorted(store.list_nodes(), key=lambda n: n.id)}


async def run_e1(trip: PlannedTrip, scenario: dict[str, Any], event: Any, drift: bool) -> dict[str, Any]:
    """The "ours" steps of run_benchmark.run_strategy, keeping the graph instead of scoring it."""
    store = copy.deepcopy(trip.store)
    ledger = copy.deepcopy(trip.ledger)
    known = {a.action_id for a in ledger.list()}
    if drift:
        trip.providers.drift.enable(scenario["id"], **DRIFT)
    try:
        outcome = await trip.orch.handle_disruption(store, event, strategy=STRATEGY.strategy,
                                                    use_frozen_context=STRATEGY.frozen_context, ledger=ledger)
        for action in [a for a in ledger.pending() if a.status == ActionStatus.PENDING]:
            await trip.orch.approve_action(store, ledger, action.action_id, decided_by="simulated-user")
    finally:
        trip.providers.drift.disable()
    replan = outcome.replan
    return {
        "status": outcome.status,
        "stale": list(replan.stale) if replan else [],
        "regenerated": list(replan.regenerated) if replan else [],
        "changed": list(replan.changed) if replan else [],
        "failed": list(replan.failed) if replan else [],
        "actions": [[a.node_id, a.kind.value, a.tier.value, a.status.value]
                    for a in ledger.list() if a.action_id not in known],
        "nodes": nodes_state(store),
    }


async def snapshot(scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    scenarios = scenarios if scenarios is not None else load_scenarios()
    trips = load_trips()
    planned: dict[str, PlannedTrip] = {}
    out: dict[str, Any] = {"strategy": STRATEGY.name, "drift": DRIFT, "volatile_keys": sorted(VOLATILE_KEYS),
                           "trips": {}, "scenarios": {}}
    for scenario in scenarios:
        tid = scenario["trip"]
        if tid not in planned:
            planned[tid] = await PlannedTrip.create(trips[tid]["goal"])
            out["trips"][tid] = nodes_state(planned[tid].store)
        trip = planned[tid]
        event = DisruptionSimulator(seed=0).make(scenario["type"], scenario["target"], **scenario["facts"])
        entry: dict[str, Any] = {"oracle": await ground_truth(trip.orch.registry, trip.store, event)}
        for drift in (True, False):
            entry["drift_on" if drift else "drift_off"] = await run_e1(trip, scenario, event, drift)
        out["scenarios"][scenario["id"]] = entry
    return out


def differences(expected: dict[str, Any], actual: dict[str, Any], limit: int = 20) -> list[str]:
    """Human-readable paths where *actual* departs from *expected* (at most *limit*)."""
    found: list[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if len(found) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    found.append(f"{path}/{key}: {'missing' if key not in b else 'unexpected'}")
                else:
                    walk(a[key], b[key], f"{path}/{key}")
        elif a != b:
            found.append(f"{path}: expected {a!r}, got {b!r}")

    walk(expected, actual, "")
    return found


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def write_snapshot(data: dict[str, Any]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="record the snapshot instead of checking it")
    args = parser.parse_args()
    data = await snapshot()
    if args.write:
        write_snapshot(data)
        print(f"wrote {SNAPSHOT_PATH} ({len(data['trips'])} trips, {len(data['scenarios'])} scenarios × 2 drift modes)")
        return
    diffs = differences(load_snapshot(), data)
    if diffs:
        print("E1 differs from the golden snapshot:\n  " + "\n  ".join(diffs))
        raise SystemExit(1)
    print(f"E1 matches the golden snapshot ({len(data['scenarios'])} scenarios × 2 drift modes)")


if __name__ == "__main__":
    asyncio.run(main())
