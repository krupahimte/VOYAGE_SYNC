"""
Ground truth for the benchmark (plan §6.6).

The oracle is ``app.core.metrics.oracle_affected``. Starting from the graph
before the event, it applies the event and then re-checks **every** contract
in the graph, not only those reachable from the changed node, with full
knowledge of the new facts. It regenerates the shallowest broken nodes and
repeats until every contract holds. The nodes it had to regenerate are the true
affected set A. It always runs with market drift switched off: A depends on the
event, not on what a re-search happens to return.

``validate`` compares A with the hand labels in eval/scenarios/labels.yaml.
"""

from __future__ import annotations

from typing import Any

import yaml

from eval.scenario_set import SCENARIO_DIR, nights


async def ground_truth(registry: Any, store: Any, event: Any) -> list[str]:
    from app.core.metrics import oracle_affected

    return await oracle_affected(registry, store, event)


def load_labels() -> dict[str, Any]:
    with open(SCENARIO_DIR / "labels.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def expected_for(scenario: dict[str, Any], goal: dict[str, Any], labels: dict[str, Any]) -> list[str] | None:
    """The hand label for *scenario*, or None when it is not in the labelled subset."""
    days = [f"day_{d}_plan" for d in range(1, nights(goal) + 1)]
    expand = lambda ids: sorted(x for i in ids for x in (days if i == "ALL_DAY_PLANS" else [i]))  # noqa: E731
    for entry in labels.get("explicit") or []:
        if entry["scenario"] == scenario["id"]:
            return expand(entry["affected"])
    for rule in labels.get("rules") or []:
        if rule["kind"] == scenario["kind"] and all(scenario["facts"].get(k) == v for k, v in rule["facts"].items()) \
                and (rule["facts"] or not scenario["facts"]):
            return expand(rule["affected"])
    return None


def validate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    *results*: dicts with ``scenario``, ``label`` (list or None) and ``oracle`` (list).
    Returns agreement statistics and every mismatch.
    """
    labelled = [r for r in results if r["label"] is not None]
    mismatches = [{"scenario": r["scenario"], "label": r["label"], "oracle": sorted(r["oracle"]),
                   "missing_in_oracle": sorted(set(r["label"]) - set(r["oracle"])),
                   "extra_in_oracle": sorted(set(r["oracle"]) - set(r["label"]))}
                  for r in labelled if sorted(r["label"]) != sorted(r["oracle"])]
    return {"labelled": len(labelled), "agree": len(labelled) - len(mismatches), "mismatches": mismatches}
