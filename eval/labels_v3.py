"""
Team labels for ground truth v3 (pre-registration R2): two annotators, blind to any
repair output, label which tasks *must* change; Cohen's kappa measures their
agreement, and the adjudicated labels are compared with the computed necessity set.

    backend\\.venv\\Scripts\\python.exe eval\\labels_v3.py sheets                 # briefs + blank sheet
    backend\\.venv\\Scripts\\python.exe eval\\labels_v3.py agree A.csv B.csv      # kappa between annotators
    backend\\.venv\\Scripts\\python.exe eval\\labels_v3.py compare adjudicated.csv  # vs the necessity set

The briefs show each trip *before* the disruption, the contracts between its tasks and
the alternatives the option space offers; never a repaired plan or the ground truth.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.scenario_set import load_scenarios, load_trips, use_backend  # noqa: E402

use_backend()

from app.core.models import NodeType  # noqa: E402
from app.core.scheduler import Scheduler  # noqa: E402
from eval.ground_truth import GT_DIR, PARAMS  # noqa: E402

LABEL_DIR = GT_DIR / "labels"
# Every disruption level on three trips: international solo 5 nights, domestic couple 3 nights,
# international group 7 nights (48 scenarios).
LABEL_TRIPS = ("santorini_solo_luxury", "goa_couple_domestic", "tokyo_family_week")
FIELDS = ["scenario", "task", "must_change", "confidence", "note"]


def subset() -> list[dict[str, Any]]:
    return [s for s in load_scenarios() if s["trip"] in LABEL_TRIPS]


# ── Sheets ────────────────────────────────────────────────────────────────────

def _hm(iso: Any) -> str:
    return str(iso)[11:16] if iso else "—"


def _task_line(node) -> str:
    o = node.output or {}
    t = node.type
    if t == NodeType.FLIGHT:
        return f"{o.get('flight_number')} {o.get('origin_iata')}→{o.get('destination_iata')} " \
               f"{_hm(o.get('departure_time'))}–{_hm(o.get('arrival_time'))}, {o.get('cost')} {o.get('currency')}"
    if t == NodeType.STAY:
        return f"{o.get('hotel_name')}, {o.get('check_in')} → {o.get('check_out')}, check-in until " \
               f"{_hm(o.get('checkin_cutoff'))}, {o.get('cost')} {o.get('currency')}"
    if t == NodeType.TRANSFER:
        return f"pickup {_hm(o.get('pickup_time'))}, drop-off {_hm(o.get('dropoff_time'))} " \
               f"({o.get('duration_minutes')} min)"
    if t == NodeType.DAY_PLAN:
        acts = "; ".join(f"{a.get('time')} {a.get('name')}{' (outdoor)' if a.get('outdoor') else ''}"
                         for a in o.get("activities") or [])
        return f"{o.get('date')}: {acts or 'nothing scheduled'}"
    if t == NodeType.WEATHER:
        return f"{o.get('date')}: {o.get('condition')}"
    return f"total {o.get('total_cost')} of {o.get('budget_limit')} {o.get('currency')}"


async def _alternatives(trip, node) -> list[str]:
    """What the option space offers for this task at planning time (not a repair)."""
    if node.type not in (NodeType.FLIGHT, NodeType.STAY, NodeType.TRANSFER):
        return []
    ri = Scheduler._resolve_inputs(None, trip.store, node.id)
    options = await trip.orch.registry.get(node).candidates(node, ri, {}, PARAMS.options_for(node.type))
    lines = []
    for o in options[1:]:
        fake = type("N", (), {"type": node.type, "output": o.output})
        lines.append(_task_line(fake))
    return lines


async def write_sheets() -> tuple[Path, Path]:
    from eval.run_benchmark import PlannedTrip

    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    trips = load_trips()
    lines = ["# Labelling briefs (ground truth v3)", "",
             "Read `LABELLING_PROTOCOL.md` first. Each trip is shown **before** any disruption.", ""]
    rows = []
    for tid in LABEL_TRIPS:
        trip = await PlannedTrip.create(trips[tid]["goal"])
        g = trips[tid]["goal"]
        lines += [f"## {tid}", "", f"{g['departure_city']} → {g['destination']}, {g['departure_date']} – "
                  f"{g['return_date']}, {g['travelers']} traveller(s), budget {g['budget']} {g['currency']}.", "",
                  "### Tasks (planned)", "", "| Task | Plan | Other options the planner may choose |",
                  "|---|---|---|"]
        for nid in trip.store.validate():
            node = trip.store.get_node(nid)
            alts = await _alternatives(trip, node)
            note = "re-fillable with other nearby places" if node.type == NodeType.DAY_PLAN else ""
            lines.append(f"| `{nid}` | {_task_line(node)} | {'<br>'.join(alts) or note or '—'} |")
        lines += ["", "### Dependencies (each must hold after a repair)", ""]
        lines += [f"- `{e.source_id}` → `{e.destination_id}`: {e.description} ({e.contract})"
                  for e in trip.store.list_edges()]
        lines += ["", "### Disruptions to label", ""]
        for s in [x for x in subset() if x["trip"] == tid]:
            facts = ", ".join(f"{k}={v}" for k, v in s["facts"].items()) or "—"
            lines.append(f"- `{s['id']}`: {s['type']} on `{s['target']}` ({facts})")
            # Weather is a fact and a delayed flight is reality: neither can be re-planned, so neither is asked.
            rows += [{"scenario": s["id"], "task": nid, "must_change": "", "confidence": "", "note": ""}
                     for nid in trip.store.validate()
                     if trip.store.get_node(nid).type != NodeType.WEATHER
                     and (nid != s["target"] or s["type"] in ("FLIGHT_CANCELLATION", "HOTEL_OVERBOOKED"))]
        lines.append("")
    brief = LABEL_DIR / "briefs.md"
    brief.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    sheet = LABEL_DIR / "annotator_template.csv"
    with open(sheet, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return brief, sheet


# ── Agreement ─────────────────────────────────────────────────────────────────

def load_sheet(path: Path) -> dict[tuple[str, str], bool]:
    """(scenario, task) → must change; every row must be answered yes or no."""
    labels = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            answer = row["must_change"].strip().lower()
            if answer not in ("yes", "no"):
                raise ValueError(f"{path.name}: {row['scenario']} / {row['task']} is not answered yes/no")
            labels[(row["scenario"], row["task"])] = answer == "yes"
    return labels


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    """Cohen's kappa for two binary raters; None when undefined (both raters constant and identical)."""
    if len(a) != len(b) or not a:
        raise ValueError("the two label lists must be equally long and non-empty")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


def agreement(a: dict, b: dict) -> dict[str, Any]:
    if set(a) != set(b):
        raise ValueError("the two sheets do not label the same (scenario, task) pairs")
    keys = sorted(a)
    disagreements = [k for k in keys if a[k] != b[k]]
    return {"pairs": len(keys), "agree": len(keys) - len(disagreements),
            "kappa": cohen_kappa([a[k] for k in keys], [b[k] for k in keys]),
            "disagreements": [list(k) for k in disagreements]}


def compare_with_ground_truth(labels: dict, gt: dict[str, Any], mode: str = "drift_off") -> dict[str, Any]:
    """Adjudicated labels vs the computed necessity set, per task and per scenario (conflicts skipped)."""
    by_scenario: dict[str, set[str]] = {}
    for (sid, task), must in labels.items():
        by_scenario.setdefault(sid, set())
        if must:
            by_scenario[sid].add(task)
    tp = fp = fn = tn = 0
    exact, mismatches = 0, []
    for sid, labelled in sorted(by_scenario.items()):
        entry = gt["scenarios"][sid][mode]
        if not entry.get("feasible"):
            continue
        necessity = set(entry["necessity"])
        tasks = {task for (s, task) in labels if s == sid}
        tp += len(labelled & necessity)
        fp += len(labelled - necessity)
        fn += len((necessity & tasks) - labelled)
        tn += len(tasks - labelled - necessity)
        if labelled == necessity & tasks:
            exact += 1
        else:
            mismatches.append({"scenario": sid, "labels_only": sorted(labelled - necessity),
                               "ground_truth_only": sorted((necessity & tasks) - labelled)})
    return {"scenarios": exact + len(mismatches), "exact": exact, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "mismatches": mismatches}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sheets")
    agree = sub.add_parser("agree")
    agree.add_argument("a", type=Path)
    agree.add_argument("b", type=Path)
    comp = sub.add_parser("compare")
    comp.add_argument("adjudicated", type=Path)
    args = parser.parse_args()
    if args.cmd == "sheets":
        brief, sheet = asyncio.run(write_sheets())
        print(f"wrote {brief} and {sheet} ({len(subset())} scenarios)")
    elif args.cmd == "agree":
        print(agreement(load_sheet(args.a), load_sheet(args.b)))
    else:
        from eval.ground_truth import load_verified

        print(compare_with_ground_truth(load_sheet(args.adjudicated), load_verified()))


if __name__ == "__main__":
    main()
