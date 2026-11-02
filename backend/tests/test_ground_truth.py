"""
Hybrid work, Phase 5 — ground truth v3: hash-protected, reproducible, independent of
any strategy's repair choice; the scope metrics; and the team-labelling tools.
"""

from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import ground_truth as gt  # noqa: E402
from eval import labels_v3 as lab  # noqa: E402
from eval.scenario_set import load_scenarios  # noqa: E402

INVALIDATING = ("FLIGHT_CANCELLATION", "HOTEL_OVERBOOKED")


@pytest.fixture(scope="module")
def truth():
    return gt.load_verified()


class TestIntegrity:
    def test_stored_file_and_sources_verify(self, truth):
        assert truth["version"] == 3 and len(truth["scenarios"]) == 160
        assert set(truth["source_hashes"]) == set(gt.SOURCES)

    def test_an_edited_file_is_refused(self, tmp_path, monkeypatch):
        path, sha = tmp_path / "ground_truth.yaml", tmp_path / "ground_truth.sha256"
        shutil.copy(gt.GT_PATH, path)
        shutil.copy(gt.SHA_PATH, sha)
        path.write_text(path.read_text(encoding="utf-8").replace("min_scope: 1", "min_scope: 0", 1),
                        encoding="utf-8")
        monkeypatch.setattr(gt, "GT_PATH", path)
        monkeypatch.setattr(gt, "SHA_PATH", sha)
        with pytest.raises(gt.GroundTruthError, match="was modified"):
            gt.load_verified()

    def test_a_changed_option_space_is_refused(self, monkeypatch):
        hashes = gt.source_hashes()
        monkeypatch.setattr(gt, "source_hashes", lambda: {**hashes, "app/agents/flight.py": "0" * 64})
        with pytest.raises(gt.GroundTruthError, match="app/agents/flight.py"):
            gt.load_verified()

    async def test_reproducible(self, truth):
        assert await gt.compute() == truth["scenarios"]


class TestProperties:
    def test_definitions_hold_for_every_scenario(self, truth):
        for s in load_scenarios():
            for mode in ("drift_off", "drift_on"):
                e = truth["scenarios"][s["id"]][mode]
                assert e["applicable"]
                if not e["feasible"]:
                    assert e["necessity"] is None and e["min_scope"] is None and e["feasible_repairs"] == 0
                    continue
                necessity, union = set(e["necessity"]), set(e["union"])
                assert e["min_scope"] >= len(necessity) and e["feasible_repairs"] >= 1
                for region in e["minimal_regions"]:
                    assert necessity <= set(region) <= union and len(region) == e["min_scope"]
                if s["type"] in INVALIDATING:
                    assert s["target"] in necessity, s["id"]
                else:
                    assert s["target"] not in union, s["id"]

    def test_conflicts_are_exactly_the_ten_hour_delays(self, truth):
        for mode in ("drift_off", "drift_on"):
            conflicts = {sid for sid, e in truth["scenarios"].items() if not e[mode]["feasible"]}
            assert conflicts == {s["id"] for s in load_scenarios() if s["id"].endswith("outbound_delay_600")}

    def test_legacy_oracle_differs_where_its_repair_policy_matters(self, truth):
        """Recorded finding: A = N in 147 of 150 feasible scenarios; in 3, A holds 2 avoidable tasks."""
        same, larger = [], []
        for sid, e in truth["scenarios"].items():
            if e["drift_off"]["feasible"]:
                n, a = set(e["drift_off"]["necessity"]), set(e["legacy_oracle"])
                (same if n == a else larger).append(sid)
                assert n <= a, sid
        assert len(same) == 147 and len(larger) == 3
        assert all(sid.endswith("outbound_delay_180") for sid in larger)


class TestScopeMetrics:
    def test_necessity_coverage(self):
        assert gt.necessity_coverage(["a", "b", "c"], ["a", "b"]) == 1.0
        assert gt.necessity_coverage(["a"], ["a", "b"]) == 0.5
        assert gt.necessity_coverage(["a"], []) is None and gt.necessity_coverage([], None) is None

    def test_excess_and_optimality(self):
        assert gt.excess_scope(["a", "b", "c"], 1) == 2 and gt.excess_scope(["a"], None) is None
        assert gt.scope_optimality(["a", "b"], 1) == 0.5 and gt.scope_optimality(["a"], 1) == 1.0
        assert gt.scope_optimality([], 0) == 1.0 and gt.scope_optimality([], None) is None


class TestLabels:
    def test_kappa(self):
        assert lab.cohen_kappa([True, True, False, False], [True, False, False, False]) == 0.5
        assert lab.cohen_kappa([True, False], [True, False]) == 1.0
        assert lab.cohen_kappa([False, False], [False, False]) is None
        with pytest.raises(ValueError):
            lab.cohen_kappa([True], [])

    def test_sheet_is_blind_and_complete(self, truth):
        rows = list(csv.DictReader(open(lab.LABEL_DIR / "annotator_template.csv", encoding="utf-8")))
        scenarios = {s["id"]: s for s in lab.subset()}
        assert {r["scenario"] for r in rows} == set(scenarios) and len(scenarios) == 48
        assert all(r["must_change"] == "" for r in rows)
        assert not any(r["task"].startswith("weather_") for r in rows)
        for r in rows:
            s = scenarios[r["scenario"]]
            assert r["task"] != s["target"] or s["type"] in INVALIDATING
        brief = (lab.LABEL_DIR / "briefs.md").read_text(encoding="utf-8")
        assert not any(word in brief for word in ("necessity", "min_scope", "minimal_regions", "legacy_oracle"))

    def test_unanswered_rows_are_rejected(self, tmp_path):
        path = tmp_path / "a.csv"
        shutil.copy(lab.LABEL_DIR / "annotator_template.csv", path)
        with pytest.raises(ValueError, match="not answered"):
            lab.load_sheet(path)

    def _sheet(self, tmp_path, name, answer) -> Path:
        rows = list(csv.DictReader(open(lab.LABEL_DIR / "annotator_template.csv", encoding="utf-8")))
        path = tmp_path / name
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=lab.FIELDS)
            w.writeheader()
            w.writerows([r | {"must_change": "yes" if answer(r) else "no"} for r in rows])
        return path

    def test_labels_equal_to_the_ground_truth_agree_exactly(self, tmp_path, truth):
        necessity = {sid: set(e["drift_off"]["necessity"] or []) for sid, e in truth["scenarios"].items()}
        a = lab.load_sheet(self._sheet(tmp_path, "a.csv", lambda r: r["task"] in necessity[r["scenario"]]))
        b = lab.load_sheet(self._sheet(tmp_path, "b.csv", lambda r: r["task"] == "budget_check"))
        assert lab.agreement(a, a)["kappa"] == 1.0
        assert lab.agreement(a, b)["agree"] < len(a)
        result = lab.compare_with_ground_truth(a, truth)
        assert result["fp"] == result["fn"] == 0 and result["exact"] == result["scenarios"] == 45
        assert lab.compare_with_ground_truth(b, truth)["mismatches"]


class TestPreregistration:
    def test_recorded_hash_matches_the_file(self):
        prereg = yaml.safe_load((gt.EVAL_DIR / "preregistration.yaml").read_text(encoding="utf-8"))
        assert prereg["ground_truth"]["sha256"] == gt.SHA_PATH.read_text(encoding="utf-8").strip()
