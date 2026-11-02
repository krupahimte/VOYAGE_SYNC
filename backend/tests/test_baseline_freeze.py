"""
Hybrid work, Phase 0 — baseline freeze: the published benchmark artefacts are
untouched, E1 still reproduces its golden snapshot, and the pre-registration
draft is well formed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import manifest  # noqa: E402
from eval.golden import differences, load_snapshot, output_hash, snapshot  # noqa: E402


class TestProtectedArtefacts:
    def test_manifest_covers_the_published_results(self):
        files = manifest.current()
        assert "results/results.csv" in files and "results_v2/METRICS_REPORT.md" in files
        assert "RESULTS.md" in files and "scenarios/scenarios.yaml" in files

    def test_protected_files_unchanged(self):
        assert manifest.check() == []

    def test_line_endings_do_not_change_text_digests(self, tmp_path):
        lf, crlf = tmp_path / "a.csv", tmp_path / "b.csv"
        lf.write_bytes(b"x,y\n1,2\n")
        crlf.write_bytes(b"x,y\r\n1,2\r\n")
        assert manifest.digest(lf) == manifest.digest(crlf)


class TestGoldenSnapshot:
    def test_volatile_fields_are_ignored(self):
        base = {"offer_id": "o1", "cost": 10.0}
        assert output_hash(base | {"booking_ref": "BK1", "booked_at": "t1"}) == \
            output_hash(base | {"booking_ref": "BK2", "booked_at": "t2"})
        assert output_hash(base) != output_hash(base | {"cost": 10.5})

    def test_differences_lists_paths(self):
        assert differences({"a": {"b": 1, "c": 2}}, {"a": {"b": 2}}) == ["/a/b: expected 1, got 2", "/a/c: missing"]

    def test_recorded_snapshot_is_complete(self):
        data = load_snapshot()
        assert data["strategy"] == "ours" and len(data["trips"]) == 10 and len(data["scenarios"]) == 160
        assert all({"oracle", "drift_on", "drift_off"} <= set(s) for s in data["scenarios"].values())

    async def test_e1_matches_golden_snapshot(self):
        assert differences(load_snapshot(), await snapshot()) == []


class TestPreregistration:
    def test_draft_is_well_formed(self):
        path = Path(__file__).resolve().parents[2] / "eval" / "preregistration.yaml"
        prereg = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert prereg["status"] in ("draft", "frozen")
        assert set(prereg["arms"]) == {"E1", "E1g", "E2", "E3", "E4", "RS", "EXH"}
        assert {"H1", "H2", "H3", "H4"} <= set(prereg["claims"])
        assert prereg["split"]["unit"] == "trip" and prereg["facts_target_is_decision"] is False
        if prereg["status"] == "frozen":
            assert prereg["frozen_sha256"]
