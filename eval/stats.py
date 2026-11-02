"""
Statistics for the E1–E4 comparisons (pre-registration ``statistics``), standard library only.

  wilcoxon      paired Wilcoxon signed-rank test, Pratt's handling of zero differences; exact
                (the permutation distribution of the signed ranks) for up to EXACT_MAX non-zero
                differences, the normal approximation with tie correction above
  rank_biserial the matched-pairs rank-biserial effect size, (T+ − T−) / (T+ + T−)
  holm          Holm's step-down adjustment of a family of p-values
  cluster_ci    a percentile bootstrap CI of the mean paired difference that resamples whole
                trips (scenarios of one trip are correlated), seeded for reproducibility
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Iterable

EXACT_MAX = 50


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based) of *values*; ties share their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _signed_ranks(diffs: list[float]) -> tuple[list[float], int]:
    """Pratt: rank |d| with the zeros included, then drop the zeros. Returns (signed ranks, number of zeros)."""
    ranks = _ranks([abs(d) for d in diffs])
    signed = [r if d > 0 else -r for r, d in zip(ranks, diffs) if d != 0]
    return signed, sum(1 for d in diffs if d == 0)


def wilcoxon(diffs: Iterable[float]) -> dict[str, Any]:
    """Two-sided paired Wilcoxon signed-rank test of the differences (Pratt's zero method)."""
    d = [float(x) for x in diffs]
    signed, zeros = _signed_ranks(d)
    n = len(signed)
    t_plus = sum(r for r in signed if r > 0)
    t_minus = -sum(r for r in signed if r < 0)
    if n == 0:
        return {"n": len(d), "nonzero": 0, "zeros": zeros, "t_plus": 0.0, "t_minus": 0.0, "p": 1.0,
                "method": "no differences"}
    if n <= EXACT_MAX:
        doubled = [round(abs(r) * 2) for r in signed]
        dist = {0: 1}
        for r in doubled:
            nxt: dict[int, int] = defaultdict(int)
            for s, c in dist.items():
                nxt[s] += c
                nxt[s + r] += c
            dist = nxt
        total = 2 ** n
        observed = round(t_plus * 2)
        mean = sum(doubled) / 2
        p = sum(c for s, c in dist.items() if abs(s - mean) >= abs(observed - mean) - 1e-9) / total
        return {"n": len(d), "nonzero": n, "zeros": zeros, "t_plus": t_plus, "t_minus": t_minus,
                "p": min(1.0, p), "method": "exact"}
    all_ranks = _ranks([abs(x) for x in d])
    ranked = [r for r, x in zip(all_ranks, d) if x != 0]
    mean = sum(ranked) / 2
    var = sum(r * r for r in ranked) / 4                # exact variance given the (tied) ranks
    z = (t_plus - mean - math.copysign(0.5, t_plus - mean)) / math.sqrt(var) if var else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"n": len(d), "nonzero": n, "zeros": zeros, "t_plus": t_plus, "t_minus": t_minus, "z": round(z, 4),
            "p": min(1.0, p), "method": "normal (continuity-corrected)"}


def sign_test(wins: int, losses: int) -> dict[str, Any]:
    """Two-sided exact binomial sign test of *wins* vs *losses* (ties excluded beforehand)."""
    n = wins + losses
    if n == 0:
        return {"wins": 0, "losses": 0, "p": 1.0}
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {"wins": wins, "losses": losses, "p": min(1.0, 2 * tail)}


def rank_biserial(diffs: Iterable[float]) -> float | None:
    signed, _ = _signed_ranks([float(x) for x in diffs])
    t_plus = sum(r for r in signed if r > 0)
    t_minus = -sum(r for r in signed if r < 0)
    return None if t_plus + t_minus == 0 else round((t_plus - t_minus) / (t_plus + t_minus), 4)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-adjusted p-values (monotone, capped at 1)."""
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted, running = {}, 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[name] = running
    return adjusted


def cluster_ci(diffs_by_trip: dict[str, list[float]], *, level: float = 0.95, resamples: int = 10_000,
               seed: int = 0) -> dict[str, Any]:
    """Mean paired difference with a percentile bootstrap CI that resamples trips."""
    trips = [t for t, xs in diffs_by_trip.items() if xs]
    values = [x for t in trips for x in diffs_by_trip[t]]
    if not values:
        return {"mean": None, "low": None, "high": None, "trips": 0, "n": 0}
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        sample = [x for _ in trips for x in diffs_by_trip[trips[rng.randrange(len(trips))]]]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int((1 - level) / 2 * resamples)]
    hi = means[min(resamples - 1, int((1 + level) / 2 * resamples))]
    return {"mean": round(statistics.fmean(values), 6), "low": round(lo, 6), "high": round(hi, 6),
            "trips": len(trips), "n": len(values), "level": level, "resamples": resamples}


def pareto(a: dict[str, float], b: dict[str, float], keys: Iterable[str]) -> str:
    """'dominates' if *a* is no worse than *b* on every key and better on one (lower is better), etc."""
    keys = list(keys)
    no_worse = all(a[k] <= b[k] + 1e-12 for k in keys)
    no_better = all(a[k] >= b[k] - 1e-12 for k in keys)
    if no_worse and no_better:
        return "equal"
    if no_worse:
        return "dominates"
    if no_better:
        return "dominated"
    return "incomparable"
