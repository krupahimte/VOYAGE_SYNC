"""
Search policies beyond greedy and exhaustive (pre-registration arms E2 and RS).

  beam    E2 — contract-pruned beam search. Every state in the beam takes its next region
          task (depth order) and branches over that task's surviving options; the best
          ``width`` children by partial fitness are kept (ties broken by the choices made,
          so the result is deterministic). This is a deterministic search with no LLM, so
          it is named beam search, not Tree of Thoughts (paper §V.B).
  random  RS — the GA's control: random repairs drawn with the same decoder the GA uses,
          until the evaluation budget is spent. Reproducible from its seed.

Both use RepairSearch's steps unchanged (options, choose, evaluate), so the contract
pruning, the frozen tasks and the region rule are exactly those of E1g and EXH.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.repair.search import Plan, RepairSearch, SearchOutcome, SearchState


@dataclass(frozen=True)
class PolicyParams:
    beam_width: int = 5            # R9 grid {1, 3, 5, 10}; tuned on dev only
    seed: int = 0                  # random search (and later the GA); set per scenario × run by the harness
    max_draw_factor: int = 20      # random search stops after budget × this many draws (tiny spaces repeat)
    max_stall_draws: int = 200     # … or after this many draws in a row found no new repair
    ga: "GAParams" = field(default_factory=lambda: GAParams())


async def beam(search: RepairSearch, width: int) -> SearchOutcome:
    """Beam search over the region tasks; complete states are evaluated as they appear."""
    if width < 1:
        raise ValueError("beam width must be at least 1")
    start = search.initial()
    frontier: list[SearchState] = [start] if start is not None else []
    plans: list[Plan] = []
    truncated = False
    while frontier:
        children: list[tuple[float, tuple, SearchState]] = []
        for state in frontier:
            if state.complete:
                if search.budget.exhausted:
                    truncated = True
                    continue
                plans.append(search.evaluate(state))
                continue
            state, nid, ctx, options = await search.options(state)
            for rank, option in enumerate(options):
                child = search.choose(state, nid, rank, option.output, ctx)
                if child is not None:
                    children.append((-search.partial_fitness(child), child.signature(), child))
        children.sort(key=lambda c: c[:2])
        frontier = [c[2] for c in children[:width]]
        if search.budget.exhausted and frontier:
            truncated = True
            break
    return search.outcome(f"beam{width}", plans, truncated)


async def decode(search: RepairSearch,
                 pick: Callable[[str, int], int]) -> tuple[SearchState | None, tuple[tuple[str, int], ...]]:
    """
    One complete repair, choosing at each region task by *pick(task, option count)* (an index).
    Repair decoder: the first option at or after that index (wrapping) that survives the
    contract checks. Returns the state (None at a dead end: some task has no surviving option)
    and the path of (task, rank) choices, ending with (task, -1) at a dead end.
    """
    state = search.initial()
    path: list[tuple[str, int]] = []
    while state is not None and not state.complete:
        state, nid, ctx, options = await search.options(state)
        start = pick(nid, len(options)) if options else 0
        chosen = None
        for step in range(len(options)):
            rank = (start + step) % len(options)
            chosen = search.choose(state, nid, rank, options[rank].output, ctx)
            if chosen is not None:
                path.append((nid, rank))
                break
        if chosen is None:
            path.append((nid, -1))
        state = chosen
    return state, tuple(path)


class _Evaluator:
    """
    Decoded candidates, each charged to the budget once: a distinct repair is validated and
    scored; a distinct dead end (keyed by its path) is charged but has no plan. Shared by RS,
    the GA and the hybrid, so all three count work the same way.
    """
    DEAD_END_KEY = (2, 0, 0.0, ())

    def __init__(self, search: RepairSearch) -> None:
        self.search = search
        self.plans: list[Plan] = []
        self._seen: dict[tuple, tuple] = {}

    @property
    def exhausted(self) -> bool:
        return self.search.budget.exhausted

    @staticmethod
    def _identity(state: SearchState | None, path: tuple) -> tuple:
        return state.signature() if state is not None else ("dead end", path)

    def known(self, state: SearchState | None, path: tuple) -> bool:
        return self._identity(state, path) in self._seen

    def key(self, state: SearchState | None, path: tuple) -> tuple | None:
        """Deb key of the candidate (dead ends rank last); None if it is new and the budget is spent."""
        identity = self._identity(state, path)
        if identity in self._seen:
            return self._seen[identity]
        if self.exhausted:
            return None
        if state is None:
            self.search.budget.plan_evaluations += 1
            key = self.DEAD_END_KEY
        else:
            plan = self.search.evaluate(state)
            self.plans.append(plan)
            key = plan.key
        self._seen[identity] = key
        return key

    @property
    def distinct(self) -> int:
        return len(self._seen)


async def random_search(search: RepairSearch, seed: int, max_draw_factor: int = 20,
                        max_stall_draws: int = 200) -> SearchOutcome:
    """
    Random repairs until the evaluation budget is spent; each distinct candidate costs one
    evaluation (_Evaluator). In a space smaller than the budget, drawing stops after
    *max_stall_draws* draws in a row that found nothing new (the stopping reason is recorded).
    """
    rng = random.Random(seed)
    ev = _Evaluator(search)
    draws = stall = 0
    limit = search.budget.max_plan_evaluations * max_draw_factor
    while not ev.exhausted and draws < limit and stall < max_stall_draws:
        draws += 1
        state, path = await decode(search, lambda nid, n: rng.randrange(n))
        if ev.known(state, path):
            stall += 1
            continue
        stall = 0
        ev.key(state, path)
    stopped = ("budget" if search.budget.plan_evaluations >= search.budget.max_plan_evaluations
               else "timeout" if search.budget.timed_out else "stalled" if stall >= max_stall_draws else "draw limit")
    outcome = search.outcome("random", ev.plans, truncated=search.budget.timed_out)
    outcome.dead_ends.append(f"random search: {draws} draws, {ev.distinct} distinct, seed {seed}, stopped: {stopped}")
    return outcome


# ── Genetic algorithm (E3) and the hybrid (E4) ────────────────────────────────

@dataclass(frozen=True)
class GAParams:
    """Pre-registration R9 defaults; generations default to budget // population."""
    population: int = 30
    generations: int | None = None
    tournament: int = 3
    p_crossover: float = 0.8
    p_mutation: float | None = None       # None: 1 / chromosome length
    elitism: int = 2
    patience: int = 10                    # generations without a better best plan
    seed_fraction: float = 1 / 3          # hybrid: share of the first population seeded from the beam


@dataclass
class GATrace:
    generations: list[dict[str, Any]]
    stopped: str
    genes: list[str]
    seeded: int

    def to_dict(self) -> dict[str, Any]:
        return {"generations": self.generations, "stopped": self.stopped, "genes": self.genes,
                "seeded": self.seeded}


def chromosome_genes(search: RepairSearch) -> list[str]:
    """One gene per task the disruption can reach: each target that is re-planned, and their descendants."""
    children: dict[str, list[str]] = {}
    for e in search.store.list_edges():
        children.setdefault(e.source_id, []).append(e.destination_id)
    reach, stack = set(), [t for t, _ in search.targets]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in reach:
                reach.add(child)
                stack.append(child)
    reach |= {t for t, k in search.targets if k == "invalidate"}
    reach -= search.fixed
    return [nid for nid in search.order if nid in reach]


def encode(plan: Plan, genes: list[str]) -> list[int]:
    """The chromosome that decodes to *plan*: its chosen rank per task, 0 where it chose nothing."""
    return [plan.state.choices.get(nid, 0) for nid in genes]


async def ga(search: RepairSearch, params: GAParams, seed: int,
             seeds: list[list[int]] | tuple = ()) -> tuple[SearchOutcome, GATrace]:
    """
    Generational GA over option ranks, decoded with the repair decoder. Selection is a
    tournament on the Deb key (feasible first, fewer violations, higher fitness); uniform
    crossover; per-gene mutation; the *elitism* best distinct chromosomes survive unchanged,
    so the best key never gets worse from one generation to the next. Stops after the
    generation limit, *patience* generations without improvement, or when the budget is spent.
    """
    rng = random.Random(seed)
    genes = chromosome_genes(search)
    sizes = [max(1, search.params.options_for(search.store.get_node(nid).type)) for nid in genes]
    length = len(genes)
    p_mutation = params.p_mutation if params.p_mutation is not None else 1 / max(1, length)
    generations = params.generations or max(1, search.budget.max_plan_evaluations // params.population)
    ev = _Evaluator(search)
    index = {nid: i for i, nid in enumerate(genes)}

    async def key_of(chrom: list[int]) -> tuple:
        state, path = await decode(search, lambda nid, n: chrom[index[nid]] % n if nid in index else 0)
        found = ev.key(state, path)
        return found if found is not None else (3, 0, 0.0, ())        # not evaluated: the budget is spent

    population = [list(s) for s in seeds][: params.population - 1]
    population.insert(0, [0] * length)                                 # option 0 everywhere (E1's choices)
    while len(population) < params.population:
        population.append([rng.randrange(n) for n in sizes])

    trace = GATrace([], "generations", genes, min(len(seeds), params.population - 1))
    best: tuple | None = None
    stale = 0
    for generation in range(generations):
        scored = [(await key_of(c), c) for c in population]
        scored.sort(key=lambda kc: kc[0])
        feasible = [k for k, _ in scored if k[0] == 0]
        trace.generations.append({
            "generation": generation, "best_key": list(scored[0][0][:3]),
            "feasible_share": round(len(feasible) / len(scored), 4),
            "mean_feasible_fitness": round(-sum(k[2] for k in feasible) / len(feasible), 6) if feasible else None,
            "evaluations": search.budget.plan_evaluations})
        if best is None or scored[0][0] < best:
            best, stale = scored[0][0], 0
        else:
            stale += 1
        if ev.exhausted:
            trace.stopped = "budget"
            break
        if stale >= params.patience:
            trace.stopped = "patience"
            break
        if generation == generations - 1:
            break
        nxt: list[list[int]] = []
        for _, chrom in scored:
            if len(nxt) == params.elitism:
                break
            if chrom not in nxt:
                nxt.append(list(chrom))

        def tournament() -> list[int]:
            contenders = [scored[rng.randrange(len(scored))] for _ in range(params.tournament)]
            return min(contenders, key=lambda kc: kc[0])[1]

        while len(nxt) < params.population:
            a, b = tournament(), tournament()
            if rng.random() < params.p_crossover:
                child = [x if rng.random() < 0.5 else y for x, y in zip(a, b)]
            else:
                child = list(a)
            child = [rng.randrange(n) if rng.random() < p_mutation else g for g, n in zip(child, sizes)]
            nxt.append(child)
        population = nxt
    if search.budget.timed_out:
        trace.stopped = "timeout"
    outcome = search.outcome("ga", ev.plans, truncated=search.budget.timed_out)
    outcome.dead_ends.append(f"ga: seed {seed}, {len(trace.generations)} generations, stopped: {trace.stopped}")
    return outcome, trace


async def hybrid(search: RepairSearch, width: int, params: GAParams, seed: int) -> tuple[SearchOutcome, GATrace]:
    """
    E4: contract-pruned beam search, then the GA seeded with the best feasible plans the
    beam found, on the same evaluation budget (the beam's evaluations count against it).
    The result is the best plan either stage found.
    """
    first = await beam(search, width)
    genes = chromosome_genes(search)
    feasible = [p for p in first.plans if p.report.feasible]
    seeds = [encode(p, genes) for p in feasible[: max(0, int(params.population * params.seed_fraction))]]
    second, trace = await ga(search, params, seed, seeds)
    plans = {p.state.signature(): p for p in [*first.plans, *second.plans]}
    outcome = search.outcome(f"hybrid(beam{width}+ga)", list(plans.values()),
                             truncated=first.truncated or second.truncated)
    outcome.dead_ends.append(f"hybrid: beam gave {len(first.plans)} plans, seeded {len(seeds)}")
    return outcome, trace


def _with_trace(run):
    """Run a (outcome, GATrace) policy and keep the trace on the outcome as ``ga_trace``."""
    async def wrapped(search: RepairSearch) -> SearchOutcome:
        outcome, trace = await run(search)
        outcome.ga_trace = trace.to_dict()
        return outcome
    return wrapped


def policies(params: PolicyParams) -> dict[str, Callable[[RepairSearch], Any]]:
    return {"beam": lambda search: beam(search, params.beam_width),
            "random": lambda search: random_search(search, params.seed, params.max_draw_factor,
                                                   params.max_stall_draws),
            "ga": _with_trace(lambda search: ga(search, params.ga, params.seed)),
            "hybrid": _with_trace(lambda search: hybrid(search, params.beam_width, params.ga, params.seed))}
