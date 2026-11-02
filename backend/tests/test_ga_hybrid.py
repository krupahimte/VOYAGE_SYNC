"""
Hybrid work, Phase 7 — the genetic algorithm (E3) and the beam-then-GA hybrid (E4).

Correctness invariants only. Whether the GA adds value (H2, H3) is an experiment for the
pre-registered E1–E4 runs, not a unit test, and is not measured here.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.ground_truth import load_verified  # noqa: E402
from eval.scenario_set import load_scenarios, load_trips  # noqa: E402

from app.core.monitor.apply import apply_event  # noqa: E402
from app.core.monitor.simulator import DisruptionSimulator  # noqa: E402
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.core.repair.policies import (GAParams, beam, chromosome_genes, decode, encode, ga,  # noqa: E402
                                      hybrid)
from app.core.repair.search import RepairSearch, SearchParams, exhaustive, greedy  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402

BUDGET = 200
TRIPS = ("santorini_solo_luxury", "tokyo_family_week")
# One scenario per disruption kind (the most severe level) on two trips: 18 scenarios.
def _severity(s: dict) -> float:
    return max((v for v in s["facts"].values() if isinstance(v, (int, float))), default=0)


SAMPLE = [s for s in load_scenarios() if s["trip"] in TRIPS and s["id"] in {
    max((x for x in load_scenarios() if x["trip"] == s["trip"] and x["kind"] == s["kind"]), key=_severity)["id"]}]
_planned: dict[str, tuple] = {}


async def planned(trip_id: str):
    if trip_id not in _planned:
        orch = Orchestrator()
        result = await orch.plan_trip(TripGoal.model_validate(load_trips()[trip_id]["goal"]))
        _planned[trip_id] = (orch, result.graph_store)
    return _planned[trip_id]


def event_of(s: dict):
    return DisruptionSimulator(seed=0).make(s["type"], s["target"], **s["facts"])


async def problem(s: dict, budget: int = BUDGET) -> RepairSearch | None:
    orch, store = await planned(s["trip"])
    work = copy.deepcopy(store)
    applied = apply_event(work, event_of(s))
    if not applied.has_effect:
        return None
    return RepairSearch(orch.registry, work, applied.target, applied.kind, params=SearchParams(timeout_s=60),
                        max_plan_evaluations=budget)


def scenario(sid: str) -> dict:
    return next(s for s in load_scenarios() if s["id"] == sid)


def fitness(outcome):
    return outcome.best.score.fitness if outcome.best else None


class TestGA:
    def test_sample_covers_every_kind(self):
        assert len(SAMPLE) == 18 and len({s["kind"] for s in SAMPLE}) == 9

    @pytest.mark.parametrize("s", SAMPLE, ids=lambda s: s["id"])
    async def test_invariants(self, s):
        if await problem(s) is None:
            pytest.skip("no effect")
        full = await exhaustive(await problem(s, 20000))
        rank = await greedy(await problem(s), by="rank")
        for seed in (0, 1):
            search = await problem(s)
            outcome, trace = await ga(search, GAParams(), seed)
            assert search.budget.plan_evaluations <= BUDGET
            # Elitism: the best key never gets worse from one generation to the next.
            keys = [tuple(g["best_key"]) for g in trace.generations]
            assert all(b <= a for a, b in zip(keys, keys[1:]))
            assert trace.stopped in ("generations", "patience", "budget")
            # Feasible exactly when a feasible repair exists (option 0 everywhere is in generation 0).
            assert outcome.feasible == full.feasible
            if outcome.feasible:
                assert fitness(outcome) <= fitness(full) + 1e-9
                assert fitness(outcome) >= fitness(rank) - 1e-9
            signatures = [p.state.signature() for p in outcome.plans]
            assert len(signatures) == len(set(signatures))

    async def test_same_seed_same_run_and_seeds_matter(self):
        s = scenario("tokyo_family_week__hotel_overbooked")
        runs = {}
        for seed in (0, 0, 1, 2):
            outcome, trace = await ga(await problem(s, 40), GAParams(population=10), seed)
            runs.setdefault(seed, []).append(([p.state.signature() for p in outcome.plans], trace.to_dict()))
        assert runs[0][0] == runs[0][1]
        assert len({str(runs[k][0]) for k in (0, 1, 2)}) > 1

    async def test_encode_decode_round_trip(self):
        s = scenario("tokyo_family_week__hotel_overbooked")
        full = await exhaustive(await problem(s, 20000))
        search = await problem(s)
        genes = chromosome_genes(search)
        index = {nid: i for i, nid in enumerate(genes)}
        assert set(full.best.state.region) <= set(genes)
        for plan in full.plans:
            chrom = encode(plan, genes)
            state, _ = await decode(search, lambda nid, n: chrom[index[nid]] % n)
            assert state.signature() == plan.state.signature()

    async def test_genes_outside_the_decoded_region_have_no_effect(self):
        s = scenario("santorini_solo_luxury__storm_last_day")
        search = await problem(s)
        genes = chromosome_genes(search)
        index = {nid: i for i, nid in enumerate(genes)}
        base = [0] * len(genes)
        state, _ = await decode(search, lambda nid, n: base[index[nid]] % n)
        outside = [i for i, nid in enumerate(genes) if nid not in state.region]
        assert outside
        other = [g + 2 if i in outside else g for i, g in enumerate(base)]
        changed, _ = await decode(search, lambda nid, n: other[index[nid]] % n)
        assert changed.signature() == state.signature()

    async def test_conflict_scenario(self):
        outcome, trace = await ga(await problem(scenario("tokyo_family_week__outbound_delay_600")), GAParams(), 0)
        assert not outcome.feasible and outcome.plans == []


class TestHybrid:
    @pytest.mark.parametrize("s", SAMPLE, ids=lambda s: s["id"])
    async def test_budget_shared_and_never_below_its_beam(self, s):
        if await problem(s) is None:
            pytest.skip("no effect")
        search = await problem(s)
        outcome, trace = await hybrid(search, 3, GAParams(), seed=0)
        assert search.budget.plan_evaluations <= BUDGET          # beam and GA together
        alone = await beam(await problem(s), 3)
        full = await exhaustive(await problem(s, 20000))
        assert outcome.feasible == alone.feasible == full.feasible
        if outcome.feasible:
            assert fitness(alone) - 1e-9 <= fitness(outcome) <= fitness(full) + 1e-9
            assert trace.seeded >= 1

    async def test_seeds_reproduce_the_beam_plans(self):
        s = scenario("tokyo_family_week__hotel_overbooked")
        search = await problem(s)
        first = await beam(search, 3)
        genes = chromosome_genes(search)
        index = {nid: i for i, nid in enumerate(genes)}
        for plan in first.plans:
            chrom = encode(plan, genes)
            state, _ = await decode(search, lambda nid, n: chrom[index[nid]] % n)
            assert state.signature() == plan.state.signature()


class TestWiring:
    @pytest.mark.parametrize("strategy", ["ga", "hybrid"])
    async def test_handle_disruption(self, strategy):
        orch, store = await planned("santorini_solo_luxury")
        for sid, expected in (("santorini_solo_luxury__hotel_overbooked", "replanned"),
                              ("santorini_solo_luxury__outbound_delay_600", "conflict")):
            outcome = await orch.handle_disruption(copy.deepcopy(store), event_of(scenario(sid)), strategy=strategy)
            assert outcome.status == expected
            assert outcome.replan.repair["ga_trace"]["generations"]

    def test_ground_truth_still_verifies(self):
        assert load_verified()["version"] == 3
