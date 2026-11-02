"""
Hybrid work, Phase 9 — the v3 scenario sets (dev / held-out × realistic / stress), the stress
option profile, combined disruptions, the dev-stress ground truth, the statistics and the
E1–E4 experiment runner (including its held-out guard).
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import eval.ground_truth as gt  # noqa: E402
from eval import experiments as ex  # noqa: E402
from eval import scenario_set_v3 as v3  # noqa: E402
from eval import stats as st  # noqa: E402
from eval.scenario_set import load_scenarios  # noqa: E402

from app.agents.providers import build_providers  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.repair.commit import repair  # noqa: E402
from app.core.repair.objective import TERMS, terms  # noqa: E402
from app.core.repair.search import RepairSearch, exhaustive  # noqa: E402

_planned: dict[tuple, object] = {}


async def planned(trip_id: str, catalogue: str = "stress", split: str = "dev"):
    key = (trip_id, catalogue, split)
    if key not in _planned:
        _planned[key] = await v3.plan_trip(v3.load_trips(split)[trip_id]["goal"], v3.PROFILES[catalogue])
    return _planned[key]


def scenario(split: str, catalogue: str, sid: str) -> dict:
    return next(s for s in v3.load(split, catalogue) if s["id"] == sid)


class TestScenarioSets:
    def test_generated_files_are_up_to_date(self):
        for split in v3.SPLITS:
            for catalogue in v3.CATALOGUES:
                assert v3.load(split, catalogue) == v3.expand(split, catalogue)

    def test_dev_realistic_is_the_legacy_benchmark(self):
        keys = ("id", "trip", "kind", "severity", "type", "target", "facts")
        assert [{k: s[k] for k in keys} for s in v3.load("dev", "realistic")] == load_scenarios()

    def test_heldout_trips_are_new(self):
        dev, held = v3.load_trips("dev"), v3.load_trips("heldout")
        assert len(dev) == 10 and len(held) == 20 and not set(dev) & set(held)
        tags = {t for trip in held.values() for t in trip["tags"]}
        assert {"domestic", "international", "solo", "couple", "group"} <= tags

    def test_stress_catalogue(self):
        for split, trips in (("dev", 10), ("heldout", 20)):
            scenarios = v3.load(split, "stress")
            assert len(scenarios) == trips * 22 and len({s["id"] for s in scenarios}) == len(scenarios)
            combined = [s for s in scenarios if s["severity"] == "combined"]
            assert len(combined) == trips * 6 and all(len(s["events"]) == 2 for s in combined)
            assert all(s["id"].split("__")[1] == "stress" for s in scenarios)


class TestStressProfile:
    def test_off_by_default(self):
        providers = build_providers(Settings(voyagesync_mode="mock"))
        assert providers.flights.offer_count == 3 and providers.places.extra_hotels == 0

    async def test_more_options_same_planned_flights(self):
        realistic = await planned("santorini_solo_luxury", "realistic")
        stress = await planned("santorini_solo_luxury", "stress")
        for nid in ("flight_outbound", "flight_return"):
            a, b = realistic.store.get_node(nid).output, stress.store.get_node(nid).output
            assert (a["flight_number"], a["departure_time"]) == (b["flight_number"], b["departure_time"])
        assert stress.store.get_node("flight_outbound").output["offers_considered"] == 8


class TestCombinedDisruptions:
    async def test_both_targets_are_repaired_together(self):
        s = scenario("dev", "stress", "santorini_solo_luxury__stress__cancellation_and_overbooked")
        trip = await planned("santorini_solo_luxury")
        store = copy.deepcopy(trip.store)
        effective = gt.apply_events(store, s)
        assert [(o.target, o.kind) for o in effective] == [("flight_outbound", "invalidate"),
                                                           ("hotel_stay", "invalidate")]
        outcome, changed, err = await repair(trip.orch.registry, store, "flight_outbound", "invalidate",
                                             policy="greedy", params=v3.search_params(v3.STRESS_PROFILE),
                                             also=(("hotel_stay", "invalidate"),))
        region = set(outcome.best.state.region)
        assert not err and {"flight_outbound", "hotel_stay", "budget_check"} <= region
        hotel, flight = store.get_node("hotel_stay"), store.get_node("flight_outbound")
        assert hotel.output["hotel_id"] not in hotel.inputs["exclude_hotel_ids"]
        assert flight.output["offer_id"] not in flight.inputs["exclude_offer_ids"]
        assert {"flight_outbound", "hotel_stay"} <= set(changed)

    async def test_facts_targets_stay_fixed(self):
        s = scenario("dev", "stress", "santorini_solo_luxury__stress__overbooked_and_storm")
        trip = await planned("santorini_solo_luxury")
        store = copy.deepcopy(trip.store)
        effective = gt.apply_events(store, s)
        search = RepairSearch(trip.orch.registry, store, effective[0].target, effective[0].kind,
                              params=v3.search_params(v3.STRESS_PROFILE, max_exhaustive=5000),
                              max_plan_evaluations=5000, also=((effective[1].target, effective[1].kind),))
        outcome = await exhaustive(search)
        weather = effective[1].target
        assert weather in search.fixed and all(weather not in p.state.region for p in outcome.plans)

    def test_objective_excludes_every_target(self):
        class G:
            def __init__(self, outs):
                from app.core.models import NodeType, TaskNode
                self.nodes = [TaskNode(id=k, type=NodeType.FLIGHT, agent="a", output=v) for k, v in outs.items()]

            def list_nodes(self):
                return self.nodes

            def get_node(self, nid):
                raise KeyError(nid)

        before = G({"a": {"flight_number": "1"}, "b": {"flight_number": "2"}})
        after = G({"a": {"flight_number": "9"}, "b": {"flight_number": "8"}})
        assert terms(before, after, {"a", "b"})["disruption"] == 0.0
        assert terms(before, after, "a")["disruption"] == 1.0


class TestDevStressGroundTruth:
    def test_verifies_and_is_complete(self):
        truth = gt.load_verified("dev", "stress")
        assert truth["split"] == "dev" and truth["catalogue"] == "stress" and len(truth["scenarios"]) == 220
        entries = [e[m] for e in truth["scenarios"].values() for m in ("drift_off", "drift_on")]
        assert not any(e.get("truncated") for e in entries)
        assert sum(e["complete_repairs"] > 500 for e in entries if e.get("complete_repairs")) >= 20

    async def test_reproducible_on_a_trip(self):
        truth = gt.load_verified("dev", "stress")
        subset = [s for s in v3.load("dev", "stress") if s["trip"] == "goa_couple_domestic"]
        assert await gt.compute(subset, "dev", "stress") == {s["id"]: truth["scenarios"][s["id"]] for s in subset}


class TestStats:
    def test_wilcoxon_exact_and_normal(self):
        assert st.wilcoxon([1, 2, 3, 4, 5])["p"] == pytest.approx(0.0625)
        assert st.wilcoxon([1, -2, 3, -4, 5, 0, 0])["zeros"] == 2
        assert st.wilcoxon([0, 0])["p"] == 1.0
        big = st.wilcoxon([0.5] * 60)
        assert big["method"].startswith("normal") and big["p"] < 1e-9
        assert st.wilcoxon([1, -1] * 40)["p"] > 0.9

    def test_holm_and_effect_size(self):
        assert st.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == {"a": 0.03, "c": 0.06, "b": 0.06}
        assert st.rank_biserial([1, 2, 3]) == 1.0 and st.rank_biserial([1, -1]) == 0.0

    def test_cluster_ci_is_seeded_and_resamples_trips(self):
        data = {"t1": [1.0, 1.0], "t2": [0.0, 0.0], "t3": [0.5]}
        a, b = st.cluster_ci(data, resamples=2000, seed=3), st.cluster_ci(data, resamples=2000, seed=3)
        assert a == b and a["trips"] == 3 and a["low"] <= a["mean"] <= a["high"]

    def test_pareto(self):
        assert st.pareto({"x": 1, "y": 1}, {"x": 2, "y": 1}, "xy") == "dominates"
        assert st.pareto({"x": 2, "y": 1}, {"x": 1, "y": 1}, "xy") == "dominated"
        assert st.pareto({"x": 1, "y": 3}, {"x": 2, "y": 1}, "xy") == "incomparable"
        assert st.pareto({"x": 1}, {"x": 1}, "x") == "equal"


class TestRunner:
    async def test_small_run(self):
        cfg = ex.Config("dev", "stress", ex.ARMS, [True], seeds=2, budget=100)
        ids = {"santorini_solo_luxury__stress__outbound_delay_600", "santorini_solo_luxury__stress__hotel_overbooked",
               "santorini_solo_luxury__stress__overbooked_and_storm"}
        scenarios = [s for s in v3.load("dev", "stress") if s["id"] in ids]
        rows = await ex.run(cfg, scenarios, progress=False)
        assert rows and all(set(r) == set(ex.FIELDS) for r in rows)
        assert len(rows) == 3 * (len(ex.ARMS) - len(ex.STOCHASTIC) + 2 * len(ex.STOCHASTIC))
        by = {(r["scenario"], r["arm"], r["run"]): r for r in rows}
        conflict = "santorini_solo_luxury__stress__outbound_delay_600"
        assert by[(conflict, "E1", 0)]["silent_failure"] and not by[(conflict, "E1", 0)]["conflict"]
        assert all(by[(conflict, a, 0)]["conflict"] for a in ("E1g", "E2", "E3", "E4", "RS", "EXH"))
        assert all(r["plan_evaluations"] <= 100 for r in rows if r["arm"] in ("E3", "E4", "RS"))
        exh = {r["scenario"]: r["fitness"] for r in rows if r["arm"] == "EXH" and r["feasible"]}
        assert all(r["fitness"] <= exh[r["scenario"]] + 1e-9 for r in rows
                   if r["feasible"] and r["scenario"] in exh and r["arm"] not in ex.LEGACY)
        again = await ex.run(cfg, scenarios, progress=False)
        strip = lambda rs: [{k: v for k, v in r.items() if k != "latency_ms"} for r in rs]  # noqa: E731
        assert strip(again) == strip(rows)
        summary = ex.summarise(rows)
        assert any(k.endswith("|E3") for k in summary)

    def test_seeds(self):
        assert ex.run_seed(0, "s", 0) == ex.run_seed(0, "s", 0) != ex.run_seed(0, "s", 1)

    def test_parse_weights(self):
        assert ex.parse_weights("") == {}
        assert ex.parse_weights("disruption=0.5, money=1.5") == {"disruption": 0.5, "money": 1.5}
        with pytest.raises(SystemExit):
            ex.parse_weights("preference=2")

    async def test_weights_change_the_objective_only(self):
        """Fitness is rescored with the changed weights; the terms of an arm that does not search stay the same."""
        scenarios = [s for s in v3.load("dev", "stress") if s["id"] == "santorini_solo_luxury__stress__hotel_overbooked"]
        equal = await ex.run(ex.Config("dev", "stress", ["E1", "E2"], [True], seeds=1), scenarios, progress=False)
        same = await ex.run(ex.Config("dev", "stress", ["E1", "E2"], [True], seeds=1,
                                      weights={"money": 1.0}), scenarios, progress=False)
        strip = lambda rs: [{k: v for k, v in r.items() if k != "latency_ms"} for r in rs]  # noqa: E731
        assert strip(same) == strip(equal)
        heavy = await ex.run(ex.Config("dev", "stress", ["E1", "E2"], [True], seeds=1,
                                       weights={"money": 1.5}), scenarios, progress=False)
        e1, e1_heavy = equal[0], heavy[0]
        assert all(e1[t] == e1_heavy[t] for t in TERMS)
        assert e1_heavy["fitness"] == pytest.approx(-sum((1.5 if t == "money" else 1.0) * e1[t] for t in TERMS), abs=1e-5)

    async def test_heldout_needs_a_frozen_preregistration(self, monkeypatch, tmp_path):
        draft = tmp_path / "p.yaml"
        draft.write_text("status: draft\nfrozen_sha256: null\n", encoding="utf-8")
        monkeypatch.setattr(ex, "PREREG", draft)
        with pytest.raises(ex.PreregistrationError, match="not frozen"):
            await ex.run(ex.Config("heldout", "realistic", ["E1"], [True]), [], progress=False)
        frozen = "status: frozen\nfrozen_sha256: {}\nx: 1\n"
        text = frozen.format("null")
        draft.write_text(frozen.format(ex.prereg_digest(text)), encoding="utf-8")
        ex.verify_frozen()
        draft.write_text(frozen.format(ex.prereg_digest(text)).replace("x: 1", "x: 2"), encoding="utf-8")
        with pytest.raises(ex.PreregistrationError, match="changed after"):
            ex.verify_frozen()


class TestReport:
    @staticmethod
    def _row(scenario, arm, fitness, *, width="", feasible=True, silent=False, catalogue="stress", run=0):
        from eval.report_v3 import TERMS
        return {"scenario": scenario, "trip": scenario.split("_")[0], "catalogue": catalogue, "drift": "on",
                "arm": arm, "beam_width": width, "run": run, "fitness": fitness, "feasible": feasible,
                "conflict": False, "silent_failure": silent, "plan_evaluations": 10.0,
                **{t: -fitness / 4 for t in TERMS}}

    def test_sign_test(self):
        assert st.sign_test(5, 0)["p"] == pytest.approx(2 / 32)
        assert st.sign_test(0, 0)["p"] == 1.0 and st.sign_test(3, 3)["p"] == 1.0

    def test_verdicts_follow_the_preregistered_rules(self):
        from eval.report_v3 import analyse
        rows = []
        for i in range(30):
            s = f"t{i % 6}_s{i}"
            rows += [self._row(s, "E1g", -0.5), self._row(s, "E2", -0.3, width="10"),
                     self._row(s, "E3", -0.2), self._row(s, "RS", -0.4),
                     self._row(s, "E4", -0.2, width="10"), self._row(s, "E2", -0.2, width="20"),
                     self._row(s, "E1", -0.3, silent=i < 12)]
        result = analyse(rows, 10, 20, set())
        assert result["holds"] == {"H1": True, "H2": True, "H3": False, "H4": True}
        assert result["ga_justified"] is False
        assert result["tests"]["H3"]["ties"] == 30 and result["tests"]["H1"]["wins"] == 30


class TestFreeze:
    def test_freezing_a_copy(self, tmp_path, monkeypatch):
        import shutil

        import yaml

        from eval import freeze_preregistration as fz

        import re

        copy_path = tmp_path / "preregistration.yaml"
        shutil.copy(ex.PREREG, copy_path)
        # The real file is frozen since 2026-11-02: turn the copy back into a draft first.
        text = copy_path.read_text(encoding="utf-8").split("\n\nfrozen_values:")[0] + "\n"
        for key, value in (("status", "draft"), ("frozen_at", "null"), ("frozen_sha256", "null")):
            text = re.sub(rf"^{key}:[^#\n]*", f"{key}: {value} ", text, count=1, flags=re.MULTILINE)
        copy_path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(fz, "PREREG", copy_path)
        monkeypatch.setattr(ex, "PREREG", copy_path)
        digest = fz.freeze(10, 20)
        data = yaml.safe_load(copy_path.read_text(encoding="utf-8"))
        assert data["status"] == "frozen" and data["frozen_sha256"] == digest
        assert data["frozen_values"]["beam_width_w_star"] == 10 and len(data["frozen_values"]["ground_truth_sha256"]) == 4
        assert data["split"]["dev_trips"] == list(v3.load_trips("dev"))
        assert data["split"]["heldout_trips"] == list(v3.load_trips("heldout"))
        ex.verify_frozen()
        with pytest.raises(SystemExit, match="already frozen"):
            fz.freeze(10, 20)
