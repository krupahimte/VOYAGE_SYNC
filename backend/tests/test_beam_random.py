"""
Hybrid work, Phase 6 — beam search (E2) and random search (RS).

Only correctness invariants are tested here. How well a beam width performs is *not*
measured on these scenarios: choosing a width from them would be tuning on data that
may become held-out (pre-registration R9 says tune on dev only).
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
from app.core.replanner import Replanner  # noqa: E402
from app.core.repair.commit import repair  # noqa: E402
from app.core.repair.policies import PolicyParams, beam, decode, random_search  # noqa: E402
from app.core.repair.search import RepairSearch, SearchParams, exhaustive, greedy  # noqa: E402
from app.schemas.trip import TripGoal  # noqa: E402

_planned: dict[str, tuple] = {}
WITH_EFFECT: list[dict] = []


async def planned(trip_id: str):
    if trip_id not in _planned:
        orch = Orchestrator()
        result = await orch.plan_trip(TripGoal.model_validate(load_trips()[trip_id]["goal"]))
        _planned[trip_id] = (orch, result.graph_store)
    return _planned[trip_id]


def event_of(s: dict):
    return DisruptionSimulator(seed=0).make(s["type"], s["target"], **s["facts"])


async def problem(s: dict, max_plan_evaluations: int | None = None) -> RepairSearch | None:
    """A fresh RepairSearch for scenario *s* (None when the event has no effect)."""
    orch, store = await planned(s["trip"])
    work = copy.deepcopy(store)
    applied = apply_event(work, event_of(s))
    if not applied.has_effect:
        return None
    return RepairSearch(orch.registry, work, applied.target, applied.kind,
                        params=SearchParams(timeout_s=60), max_plan_evaluations=max_plan_evaluations)


async def scenarios_with_effect() -> list[tuple[dict, RepairSearch]]:
    out = []
    for s in load_scenarios():
        if await problem(s) is not None:
            out.append(s)
    return out


def best(outcome):
    return (outcome.feasible, outcome.best.score.fitness if outcome.best else None,
            outcome.best.state.signature() if outcome.best else None)


class TestBeam:
    async def test_width_one_is_greedy(self):
        for s in load_scenarios():
            if await problem(s) is None:
                continue
            assert best(await beam(await problem(s), 1)) == best(await greedy(await problem(s))), s["id"]

    async def test_unbounded_width_is_exhaustive(self):
        for s in load_scenarios():
            if await problem(s) is None:
                continue
            wide = await beam(await problem(s, 20000), 10_000)
            full = await exhaustive(await problem(s, 20000))
            assert best(wide) == best(full), s["id"]
            assert sorted(p.state.signature() for p in wide.plans) == \
                sorted(p.state.signature() for p in full.plans), s["id"]

    @pytest.mark.parametrize("width", [1, 3, 5, 10])
    async def test_never_beats_exhaustive_and_agrees_on_feasibility(self, width):
        for s in load_scenarios():
            if await problem(s) is None:
                continue
            got, full = await beam(await problem(s), width), await exhaustive(await problem(s, 20000))
            assert got.feasible == full.feasible, s["id"]
            if got.feasible:
                assert got.best.score.fitness <= full.best.score.fitness + 1e-9, s["id"]

    async def test_deterministic(self):
        s = next(x for x in load_scenarios() if x["id"] == "tokyo_family_week__hotel_overbooked")
        first, second = (await beam(await problem(s), 5)).summary(), (await beam(await problem(s), 5)).summary()
        for summary in (first, second):
            summary["budget"].pop("timed_out")
        assert first == second

    async def test_width_must_be_positive(self):
        s = next(x for x in load_scenarios() if x["id"] == "tokyo_family_week__hotel_overbooked")
        with pytest.raises(ValueError):
            await beam(await problem(s), 0)


class TestDecoderAndRandom:
    async def test_decoder_at_index_zero_is_the_rank_policy(self):
        for s in load_scenarios():
            if await problem(s) is None:
                continue
            state, _ = await decode(await problem(s), lambda nid, n: 0)
            rank = await greedy(await problem(s), by="rank")
            assert (state.signature() if state else None) == \
                (rank.best.state.signature() if rank.best else None), s["id"]

    async def test_same_seed_same_result_and_seeds_matter(self):
        s = next(x for x in load_scenarios() if x["id"] == "tokyo_family_week__hotel_overbooked")
        runs = {}
        for seed in (0, 0, 1, 2, 3):
            outcome = await random_search(await problem(s, 3), seed)
            runs.setdefault(seed, []).append([p.state.signature() for p in outcome.plans])
        assert runs[0][0] == runs[0][1]
        assert len({tuple(map(tuple, runs[k][0])) for k in (0, 1, 2, 3)}) > 1

    async def test_budget_unique_plans_and_never_beats_exhaustive(self):
        for s in load_scenarios():
            search = await problem(s, 10)
            if search is None:
                continue
            outcome = await random_search(search, seed=7)
            signatures = [p.state.signature() for p in outcome.plans]
            assert len(signatures) == len(set(signatures)) and search.budget.plan_evaluations <= 10
            full = await exhaustive(await problem(s, 20000))
            assert outcome.feasible == full.feasible or not outcome.feasible, s["id"]
            if outcome.feasible:
                assert outcome.best.score.fitness <= full.best.score.fitness + 1e-9, s["id"]

    async def test_a_large_budget_covers_a_small_space(self):
        s = next(x for x in load_scenarios() if x["id"] == "tokyo_family_week__hotel_overbooked")
        outcome = await random_search(await problem(s, 500), seed=0)
        full = await exhaustive(await problem(s, 20000))
        assert len(outcome.plans) == len(full.plans) == 48 and best(outcome) == best(full)

    async def test_conflict_scenario_charges_each_distinct_dead_end_once(self):
        """Phase 7 rule (shared with the GA): a distinct dead end costs one evaluation, a repeated one nothing."""
        s = next(x for x in load_scenarios() if x["id"] == "tokyo_family_week__outbound_delay_600")
        search = await problem(s, 5)
        outcome = await random_search(search, seed=0)
        assert not outcome.feasible and outcome.plans == []
        # Budget 5 × draw factor 20 = 100 draws, reached before the 200-draw stall rule.
        assert search.budget.plan_evaluations == 1 and "100 draws" in outcome.dead_ends[-1]
        assert "stopped: draw limit" in outcome.dead_ends[-1]


class TestWiring:
    @pytest.mark.parametrize("strategy", ["beam", "random"])
    async def test_handle_disruption_commits_or_conflicts(self, strategy):
        orch, store = await planned("santorini_solo_luxury")
        for sid, expected in (("santorini_solo_luxury__hotel_overbooked", "replanned"),
                              ("santorini_solo_luxury__outbound_delay_600", "conflict")):
            s = next(x for x in load_scenarios() if x["id"] == sid)
            outcome = await orch.handle_disruption(copy.deepcopy(store), event_of(s), strategy=strategy)
            assert outcome.status == expected, (strategy, sid)
            assert outcome.replan.validation["feasible"] == (expected == "replanned")

    async def test_policy_params_reach_the_search(self):
        orch, store = await planned("santorini_solo_luxury")
        s = next(x for x in load_scenarios() if x["id"] == "santorini_solo_luxury__hotel_overbooked")
        work = copy.deepcopy(store)
        replanner = Replanner(orch.registry, policy_params=PolicyParams(beam_width=2))
        applied = apply_event(work, event_of(s))
        result = await replanner.replan(work, applied.target, applied.kind, strategy="beam")
        assert result.repair["policy"] == "beam2" and result.repair["committed"]

    async def test_repair_entry_point_takes_policy_params(self):
        orch, store = await planned("santorini_solo_luxury")
        s = next(x for x in load_scenarios() if x["id"] == "santorini_solo_luxury__hotel_overbooked")
        work = copy.deepcopy(store)
        applied = apply_event(work, event_of(s))
        outcome, changed, err = await repair(orch.registry, work, applied.target, applied.kind, policy="random",
                                             policy_params=PolicyParams(seed=3))
        assert outcome.feasible and changed and not err and "seed 3" in outcome.dead_ends[-1]

    def test_ground_truth_still_verifies(self):
        """search.py was not touched, so the option-space hashes still match."""
        assert load_verified()["version"] == 3
