# VoyageSync v3: paper-ready held-out results

Prepared 2026-11-02, using only the completed Phase 10 held-out benchmark.

**Sources:**
- Primary (pre-registered): `eval/results_v3/heldout/ANALYSIS_HELDOUT.md` and `analysis_heldout.json`, both produced by `eval/report_v3.py`.
- Supplementary (not pre-registered tests): `eval/results_v3/heldout/supplementary/supplementary_heldout.json`.
- The full audit report: `eval/results_v3/HELDOUT_FINAL_REPORT.md`.
- Dev numbers appear **only** in Table C, labelled as tuning data: `eval/results_v3/dev/ANALYSIS_DEV.md`.

**Not used:**
- The weight sensitivity analysis. It was started and then stopped; no results exist, and nothing here depends on it.
- No v1 or v2 numbers appear in Tables A–D.

**Conventions:**
- All results are **deterministic mock mode**: offline mock providers, MockLLM template code, 0 real LLM or network calls.
- N/A means the metric is not recorded in v3, or does not apply to the arm.
- **[P]** marks a pre-registered primary result; **[S]** marks a supplementary analysis.

---

## Part 1: Tables

### Table A: algorithm comparison (held-out, drift on, 20 unseen trips, 760 scenarios: 320 realistic and 440 stress)

Rates are over all 760 scenarios. Objective terms, fitness and retention are over feasible runs. For E3, E4 and RS, every value is a mean over 20 seeds per scenario. ↑ = higher is better, ↓ = lower is better.

**A1: validity, safety and plan quality**

| Metric | E1 | E1g | E2 (W=10) | E2′ (W=50) | E3 (GA) | E4 (Beam+GA) | RS | EXH |
|---|---|---|---|---|---|---|---|---|
| Executability: feasible, all 7 hard constraints ↑ | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % |
| Silent failures (infeasible plan committed without a flag) ↓ | **5.3 % (40)** | 0 % (0) | 0 % (0) | 0 % (0) | 0 % (0) | 0 % (0) | 0 % (0) | 0 % (0) |
| Conflicts reported (infeasible, not committed) | 0 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % |
| Contract satisfaction (share of contracts) | N/A¹ | N/A¹ | N/A¹ | N/A¹ | N/A¹ | N/A¹ | N/A¹ | N/A¹ |
| User-requirement satisfaction ↑ | 0.864 | 0.864 | 0.865 | 0.865 | 0.865 | 0.865 | 0.865 | 0.865 |
| Requirement regression | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Fitness ↑ | −0.336 | −0.331 | −0.316 | −0.307 | −0.305 | −0.305 | −0.305 | −0.305 |
| Disruption (share of visible tasks changed) ↓ | 0.101 | 0.096 | 0.103 | 0.102 | 0.103 | 0.103 | 0.103 | 0.103 |
| Travel cost change (Δcost / budget, plus overrun) ↓ | 0.103 | 0.103 | 0.080 | 0.073 | 0.069 | 0.069 | 0.069 | 0.069 |
| Activity-time retention ↑ | 0.988 | 0.988 | 0.987 | 0.988 | 0.987 | 0.987 | 0.987 | 0.987 |
| Re-approvals (share of booked items changed) ↓ | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 |
| Task Preservation Rate | N/A² | N/A² | N/A² | N/A² | N/A² | N/A² | N/A² | N/A² |
| Contract recovery | N/A³ | N/A³ | N/A³ | N/A³ | N/A³ | N/A³ | N/A³ | N/A³ |
| Strict recovery | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

**A2: scope and optimality**

| Metric | E1 | E1g | E2 (W=10) | E2′ (W=50) | E3 (GA) | E4 (Beam+GA) | RS | EXH |
|---|---|---|---|---|---|---|---|---|
| Tasks regenerated (scenarios with an effect) | 3.35 | 3.04 | 3.11 | 3.07 | 3.07 | 3.07 | 3.07 | 3.07 |
| Necessity coverage ↑ | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Excess scope (tasks above the minimum) ↓ | 0.014 | 0.000 | 0.061 | 0.025 | 0.024 | 0.024 | 0.024 | 0.024 |
| Scope optimality (min / \|R\|) ↑ | 0.996 | 1.000 | 0.984 | 0.994 | 0.994 | 0.994 | 0.994 | 0.994 |
| [S] Matches the EXH optimum, realistic (n=300) | 74.7 % | 90.3 % | **100 %** | 100 % | 100 % | 100 % | 100 % | (ref.) |
| [S] Matches the EXH optimum, stress (n=420) | 57.1 % | 72.4 % | **86.7 %** | 97.4 % | 96.7 % | 99.8 % | 99.0 % | (ref.) |
| [S] Matches the EXH optimum, pooled (n=720) | 64.4 % | 79.9 % | **92.2 %** | 98.5 % | 98.1 % | 99.9 % | 99.4 % | (ref.) |
| [S] Mean fitness gap to EXH, pooled ↓ | 0.0316 | 0.0262 | 0.0112 | 0.0025 | 0.00005 | 0.00004 | 0.00001 | 0 |

**A3: cost**

| Metric | E1 | E1g | E2 (W=10) | E2′ (W=50) | E3 (GA) | E4 (Beam+GA) | RS | EXH |
|---|---|---|---|---|---|---|---|---|
| Full plan evaluations | N/A⁴ | 1.0 | 5.7 | 16.8 | 20.0 | 23.0 | 61.9 | 128.7 |
| Partial evaluations (beam ranking, not in the budget) | N/A⁴ | 8.3 | 41.8 | 118.5 | 0 | 41.8 | 0 | 0 |
| Mock provider API calls | 2.39 | 3.34 | 11.83 | 17.04 | 16.26 | 15.57 | 21.39 | 22.08 |
| MockLLM calls (template code, not a model) | 0.23 | 0.23 | 0.23 | 0.24 | 0.24 | 0.24 | 0.24 | 0.24 |
| Latency median / p95, ms (mock mode)⁵ | 5.0 / 16.1 | 10.4 / 40.0 | 18.7 / 116.2 | 8.3 / 179.4 | 332.2 / 1,272.9 | 340.4 / 1,169.0 | 247.2 / 3,512.9 | 16.9 / 801.5 |
| Runs that hit the 60 s safety timeout (of 760 or 15,200) | 0 | 0 | 0 | 0 | 8 | 7 | 15 | 0 |

**Notes:**
- ¹ v3 has no per-contract share. Contract validity is one of the 7 hard constraints inside *Executability*.
- ² Task Preservation Rate is a v2 metric and is not recorded in v3. The nearest v3 quantity is *Disruption*, which is not the same metric.
- ³ Contract recovery is a v2 metric. The nearest v3 quantity is *Executability (feasible)*.
- ⁴ E1 does not search, so plan-evaluation counts don't apply.
- ⁵ Latency was measured with 16 processes running in parallel, plus a machine sleep. It is not comparable across arms or with real providers. E2′ ran in a separate, less loaded launch.
- All 40 infeasible scenarios are the 10-hour outbound delay (`outbound_delay_600`, every trip × both catalogues). No feasible repair exists there.

Legacy baselines B1 (full replan) and B2 (naive downstream) were also run on held-out. They appear in `ANALYSIS_HELDOUT.md` and are left out of Table A, as requested. Their v1 and v2 results are separate experiments and must not be pooled with Table A.

### Table B: pre-registered hypothesis tests [P] (held-out, drift on, Holm over 6 tests)

| | Hypothesis | Compared | n (decisive) | Result | Effect size [95 % CI] | 95 % CI of the mean Δfitness | Raw p | Holm p | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| H1 | Beam Pareto-dominates greedy more often than the reverse | E2 (W=10) vs E1g | 720 (86) | 77 dominate / 9 dominated (573 equal, 61 incomparable) | Share of decisive pairs won: 0.895 [0.811, 0.951]ᵃ | N/A (sign test) | 1.34 × 10⁻¹⁴ | 8.05 × 10⁻¹⁴ | **Supported** |
| H2 | GA reaches higher fitness than random search at an equal budget | E3 vs RS (stress) | 420 (17) | 3 better / 14 worse / 403 tied | Rank-biserial r = −0.645 | [−0.000195, 0.000000] | 0.0105 | 0.0105 | **Not supported** (significant in favour of RS) |
| H3 | Hybrid reaches higher fitness than a budget-matched beam | E4 (W=10) vs E2′ (W=50) (stress) | 420 (11) | 11 better / 0 worse / 409 tied | Rank-biserial r = +1.00 | [+0.000687, +0.008395] | 9.77 × 10⁻⁴ | 1.95 × 10⁻³ | **Supported**, with the caveat in Part 2 §3 |
| H4 | E2–E4 have no silent failures, while E1 does | E1 vs E2, E1 vs E3 and E1 vs E4 (each) | 760 (40) | 40 vs 0 silent failures in each pair | Share of discordant pairs: 1.00 [0.912, 1.00]ᵃ | N/A | 1.82 × 10⁻¹² (each) | 9.09 × 10⁻¹² (each) | **Supported** |

**GA justified (pre-registered: H2 and H3 must both hold): No.**

**Notes:**
- **Tests.** H1 and H4 use an exact two-sided sign test. H2 and H3 use an exact Wilcoxon signed-rank test (Pratt zero handling) on per-scenario seed-mean fitness differences.
- **Confidence intervals.** The CIs of the mean Δ are 95 % bootstraps that resample whole trips (20 clusters). ᵃ marks Clopper–Pearson 95 % intervals, computed as a supplementary analysis.
- **Means (SD) over paired feasible scenarios:**
  - H1: E2 −0.3158 (0.3398), E1g −0.3308 (0.3693).
  - H2: E3 −0.359992 (0.3354), RS −0.359918 (0.3353).
  - H3: E4 −0.359969 (0.3354), E2′ −0.364155 (0.3448).

**Table B2: robustness of the verdicts.** These are secondary or supplementary analyses and are not part of the Holm family.

| Analysis | H1 | H2 | H3 | H4 |
|---|---|---|---|---|
| Drift off, secondary (pre-registered) | 78 vs 10, Holm 2.0e-13 ✓ | 2 / 25 / 393, r = −0.85, Holm 9.8e-6 ✗ (RS better) | 13 / 1 / 405, r = +0.86, Holm 3.7e-4 ✓ | 40 vs 0 ✓ |
| Large option spaces only (>500 complete repairs, 32 scenarios; pre-registered, unadjusted) | N/A | 3 / 2 / 27, p = 0.75 ✗ | 6 / 0 / 26, Δ +0.022 [0.003, 0.052], p = 0.031 | N/A |
| [S] Excluding the 22 scenarios with a timed-out run | 71 vs 9, Holm 2.6e-12 ✓ | 3 / 13 / 386, Holm 0.018 ✗ | 11 / 0 / 391, Holm 0.0020 ✓ | 40 vs 0 ✓ |
| [S] Per catalogue, unadjusted | Realistic 11 vs 0 (p = 9.8e-4); stress 66 vs 9 (p = 7.7e-12) | N/A | N/A | 20 vs 0 in each (p = 1.9e-6) |

### Table C: development vs held-out (does it generalise?)

Dev is tuning data: the 10 dev trips, 5 seeds, 160 realistic and 220 stress scenarios. It was used to choose W* and E2′ and **is not evidence**. Held-out: 20 unseen trips, 20 seeds, 320 realistic and 440 stress scenarios.

| | Dev: result (Holm p) | Dev verdict | Held-out: result (Holm p) | Held-out verdict | Generalises? |
|---|---|---|---|---|---|
| H1: beam vs greedy | 43 vs 5 (8.2e-8) | holds | 77 vs 9 (8.0e-14) | holds | **Yes** |
| H2: GA vs RS | 0 / 4 / 206, r = −1.0 (0.19) | does not hold | 3 / 14 / 403, r = −0.645 (0.0105) | does not hold | **Yes.** RS ≥ GA in both; the reverse effect reaches significance only with the larger held-out sample. |
| H3: hybrid vs E2′ | 5 / 1 / 204, r = +0.67 (0.19) | does not hold | 11 / 0 / 409, r = +1.0 (0.0020) | holds | **Same direction.** Dev had too few non-tied scenarios (6) to reach significance. |
| H4: silent failures | 20 vs 0 (9.5e-6) | holds | 40 vs 0 (9.1e-12) | holds | **Yes** |
| GA justified | no | | no | | **Yes** |

**Stress-catalogue fitness means, dev → held-out:**

| Arm | Dev | Held-out |
|---|---|---|
| E1 | −0.4030 | −0.4071 |
| E1g | −0.3937 | −0.4000 |
| E2 (W=10) | −0.3882 | −0.3791 |
| E2′ (W=50) | −0.3663 | −0.3642 |
| E3 | −0.35898 | −0.35999 |
| E4 | −0.35896 | −0.35997 |
| RS | −0.35894 | −0.35992 |
| EXH | −0.35894 | −0.35991 |

The arm ordering is the same in both. Full plan evaluations on stress, dev → held-out:

| Arm | Dev | Held-out |
|---|---|---|
| E4 | 33.1 | 32.0 |
| E2′ | 23.7 | 23.6 |

### Table D: key findings and recommendation

| # | Finding | Evidence | Practical consequence |
|---|---|---|---|
| 1 | Searching over candidate repairs beats taking the first valid option | H1 [P]: 77 vs 9 dominations | Use beam search rather than greedy repair |
| 2 | Validation with conflict reporting removes silent failures | H4 [P]: 40 vs 0. The repair arms report a conflict wherever E1 commits an infeasible plan. | Never commit an unvalidated repair. Raise a user decision (T2) instead. |
| 3 | The GA adds nothing over random search | H2 [P]: RS better 14 vs 3 (r = −0.645) | Do not claim the GA as a contribution ("GA not justified") |
| 4 | The hybrid's small gain over a wider beam is bought with more evaluations | H3 [P]: 11 / 0 / 409, Δ +0.004. E4 used 32.0 full evaluations, E2′ 23.6. | Report H3 with its budget caveat, not as evidence for the GA |
| 5 | Beam (W=10) is optimal on realistic disruptions and near-optimal on stress | [S] 100 % / 86.7 % / 92.2 % match EXH (realistic / stress / pooled) at 5.7 evaluations | A good accuracy-to-cost trade-off |
| 6 | The option spaces in this benchmark are small enough for exhaustive search | EXH was never truncated; 128.7 evaluations on average | Heuristic search is not *required* here. Its value at larger scales is untested. |
| **Rec.** | **E2: contract-pruned beam search, W* = 10, with hard-constraint validation and conflict reporting** | Findings 1, 2, 5. Deterministic (seed SD 0). | The recommended VoyageSync repair algorithm. Use a wider beam (W=50: 98.5 % optimal) or EXH when the space allows and more quality is needed. |

---

## Part 2: Explanations

### 1. Why beam search is recommended over greedy repair
Both arms work on the same contract-pruned repair region, option lists and objective. The only difference is how many partial repairs they keep at each step.
- **Greedy (E1g, width 1)** commits to the best-looking option task by task. An early choice can be locally best and still force worse options downstream. For example, a cheaper flight can arrive too late for the planned pickup or for the day-1 activities.
- **Beam search (width 10)** keeps the ten best partial repairs by partial fitness, so it can recover from such choices.

On the held-out set:
- Beam strictly improves on greedy on all four objective terms at once (Pareto dominance) in 77 scenarios, and is dominated in 9 (H1, Holm p = 8.0 × 10⁻¹⁴).
- In the other 634 feasible pairs, the two are equal or trade off between terms.
- The gain comes mostly from the money term (0.080 vs 0.103 of budget), for a small rise in disruption (0.103 vs 0.096).
- The cost is about 5.7 full plan evaluations instead of 1, plus 41.8 cheap partial evaluations.

### 2. Why the genetic algorithm is not justified
Before any held-out run, the pre-registration set a rule: the GA counts as a contribution only if it beats a random-search control at an equal evaluation budget (H2) **and** the beam-seeded hybrid beats a budget-matched wider beam (H3).

H2 fails, and in the opposite direction: random search reaches higher fitness than the GA in 14 of the 17 non-tied stress scenarios (r = −0.645, Holm p = 0.0105). The same holds with drift off and on the large-space subset.

The data point to the stopping rule as the reason:
- The GA stops after 10 generations without improvement, using about 30 evaluations on stress.
- Random search keeps sampling until 200 draws in a row bring nothing new, using about 100 evaluations.
- Both are under the same cap of 500.

So the genetic operators do not find better repairs than undirected sampling under the same cap. By the pre-registered criterion, the GA component is not justified. The GA parameters were fixed in advance and not tuned, so the result applies to the pre-registered configuration.

### 3. What the H3 caveat means
H3 compares the hybrid (beam W=10 seeding a GA) with a wider beam, E2′. E2′'s width was fixed on dev as the widest beam whose mean number of full plan evaluations does not exceed the hybrid's (W=50).

On held-out, the hybrid is never worse than E2′ and better in 11 of 420 stress scenarios (r = 1.0, Holm p = 0.0020). Three qualifications apply:
1. **The budget match is approximate.** On held-out, the hybrid used 32.0 full evaluations against E2′'s 23.6 (36 % more). E2′ in turn used more partial evaluations (118.5 pooled, 187.7 on stress), which the budget does not count.
2. **The effect is small and concentrated.**
   - The mean gain is 0.0042 fitness units (95 % CI [0.0007, 0.0084]).
   - 409 of 420 scenarios are exact ties.
   - One scenario contributes 0.49 of the summed difference of 1.76.
3. **The gain cannot be credited to the GA.** H2 shows random search does at least as well as the GA. The hybrid's advantage is therefore best read as the value of *extra search after the beam*, not of the genetic operators.

H3 is reported as supported under the pre-registered rule, with these qualifications.

### 4. Why eliminating silent failures matters
A silent failure is a repaired plan that violates a hard constraint (a broken contract, impossible dates, a missing booking) but is committed and presented to the traveller as valid.

In all 40 scenarios with no feasible repair, the original replanner E1 committed such a plan. These are the 10-hour delays, where arrival comes after the hotel's check-in cutoff. Each search arm instead validated the candidate against all hard constraints, found none feasible, and reported a conflict: a best-effort plan plus a T2 decision for the user (H4: 40 vs 0, Holm p = 9.1 × 10⁻¹²).

For a travel planner this is the difference between telling the traveller "your hotel cannot be reached tonight, choose what to do" and silently booking an itinerary that fails on arrival. Validation does not reduce the number of feasible scenarios (94.7 % for every arm). It changes only whether infeasibility is visible.

### 5. Why beam width 10 is the recommended configuration
The width was not chosen on held-out data. The pre-registered tuning rule picks the smallest width in {1, 3, 5, 10} whose mean feasible fitness on the dev split is within 10⁻⁶ of the best width's. On dev, the mean feasible fitness was:

| Width | Dev mean feasible fitness |
|---|---|
| 1 | −0.3248 |
| 3 | −0.3211 |
| 5 | −0.3227 |
| 10 | **−0.3195** (best) |

So W* = 10. This was frozen before the held-out run and is the configuration tested in H1, H3 and H4.

On held-out, W = 10 is optimal on every realistic scenario and costs 5.7 full evaluations, about a third of W = 50 (16.8). A wider beam is better on the stress catalogue (W=50 matches the optimum in 97.4 % of stress scenarios against 86.7 %). That is a quality-versus-cost choice beyond the pre-registered configuration, so it is reported as an observation, not as the recommendation.

### 6. How closely beam search matches the exhaustive optimum [S]
EXH enumerates every complete repair in the option space and was never truncated on held-out. That makes it an exact optimality reference.

**Beam search at W = 10:**
- **Realistic catalogue:** reaches the exhaustive optimum in 300 of 300 feasible scenarios (mean gap 0).
- **Stress catalogue:** reaches it in 86.7 % of 420 feasible scenarios (mean gap 0.019, worst case 1.01).
- **Pooled:** 92.2 % optimal, mean gap 0.011.

For comparison, the share of scenarios matching the optimum (pooled):

| Arm | Share optimal |
|---|---|
| Greedy | 79.9 % |
| E1 | 64.4 % |
| W = 50 | 98.5 % |
| GA | 98.1 % |
| Hybrid | 99.9 % |
| Random search | 99.4 % |

The last three use 3.5–11× more full evaluations than W = 10. These comparisons are supplementary; they were not among the pre-registered tests.

---

## Part 3: Draft paper text

> The text below uses deterministic mock-mode results only. Section numbers are placeholders.

### X.1 Experimental results

**Setup.**
- **Scenarios.** We evaluated the repair-search variants on a held-out benchmark of 20 trip templates that were never used during development. Each was paired with 16 single disruptions (the realistic catalogue, 320 scenarios). A stress catalogue (440 scenarios) adds 6 combined two-event disruptions per trip and a larger option space: 8 flight offers, 8 hotels and 6 pickup timings.
- **Arms.** Every arm repaired an identical copy of the same booked trip under the same disruption events and the same seeded market drift. The arms were:
  - E1, the original contract-propagation replanner;
  - E1g, greedy repair;
  - E2, beam search with W = 10;
  - E2′, beam search with W = 50;
  - E3, a genetic algorithm;
  - E4, a beam-seeded GA;
  - RS, random search;
  - EXH, exhaustive enumeration.
- **Budget and seeds.** The search arms share a budget of 500 full plan evaluations. The stochastic arms (E3, E4, RS) were run with 20 seeds per scenario.
- **Evaluation.** Each repair was validated against 7 hard constraints and scored with a four-term objective. The terms are disruption to the visible plan, money (cost change relative to the budget, with overruns counted twice), lost activity time, and re-approvals.
- **Mode.** All experiments ran in a deterministic mock mode with no real LLM or provider calls.
- **Size.** The run produced 101,840 results with no missing or duplicate scenarios, seeds or arms.

**Validity and safety.**
- Every arm produced a feasible plan in the same 94.7 % of scenarios (720 / 760). The remaining 40 scenarios are 10-hour delays for which no feasible repair exists.
- On these, the original replanner committed an infeasible plan without flagging it (a silent failure) in all 40 cases.
- Every search-based arm instead detected the infeasibility and reported a conflict to the user. No search arm produced a silent failure (Table A).

**Plan quality.**
- Searching over candidate repairs improved the objective over both E1 (fitness −0.336) and greedy repair (−0.331).
- Beam search reached −0.316 at W = 10 and −0.307 at W = 50. The GA, the hybrid, random search and exhaustive search all reached −0.305.
- The improvement is concentrated in the money term, which falls from 0.103 to between 0.080 and 0.069 of the budget. Disruption (about 0.10), activity-time retention (about 0.99) and re-approvals (0.120) are nearly unchanged.
- All search arms covered the full necessity set (coverage 1.0) and stayed within 0.06 tasks of the minimum feasible repair scope.

**Optimality and cost.**
- Against the exhaustive reference, beam search with W = 10 reached the optimal repair in all 300 feasible realistic scenarios and in 86.7 % of the 420 feasible stress scenarios (92.2 % overall). It used on average 5.7 full plan evaluations.
- Greedy repair was optimal in 79.9 % of feasible scenarios.
- The GA, the hybrid and random search reached 98.1–99.9 %, at 20.0–61.9 evaluations.
- Exhaustive search needed 128.7 evaluations and was never truncated.

### X.2 Statistical analysis

**Pre-registration.** All tests were pre-registered before the held-out run. The pre-registration file was frozen with a SHA-256 digest that every run verified. The tests are:
- **H1:** beam search Pareto-dominates greedy repair more often than the reverse; exact sign test.
- **H2:** the GA outperforms random search at an equal evaluation budget; Wilcoxon signed-rank test on per-scenario fitness.
- **H3:** the beam-seeded GA outperforms a wider beam matched to its evaluation budget; Wilcoxon signed-rank test.
- **H4:** the search arms have no silent failures while E1 does; exact sign test against each of E2, E3 and E4.

**Method.**
- The unit of analysis is the scenario, with seed means for the stochastic arms.
- p-values are Holm-adjusted over the six tests.
- Effect sizes are rank-biserial correlations, and confidence intervals are 95 % bootstraps that resample whole trips.
- The beam width (W* = 10) and the matched width for H3 (W = 50) were fixed on a separate development split before the held-out run.
- The GA would be counted as justified only if both H2 and H3 held.

**Results (Table B).**
- **H1 is supported.** Beam search dominated greedy repair in 77 scenarios and was dominated in 9 (89.5 % of decisive pairs; Holm-adjusted p = 8.0 × 10⁻¹⁴).
- **H4 is supported.** E1 failed silently in 40 scenarios, against none for E2, E3 or E4 (Holm-adjusted p = 9.1 × 10⁻¹² for each comparison).
- **H2 is not supported.** The GA was better than random search in 3 scenarios and worse in 14, with 403 ties (r = −0.645, Holm-adjusted p = 0.0105). The difference is significant in favour of random search.
- **H3 is supported.** The hybrid was better than the W = 50 beam in 11 scenarios and never worse (r = 1.0; mean difference 0.0042, 95 % CI [0.0007, 0.0084]; Holm-adjusted p = 0.0020).
- **GA verdict.** Because H2 fails, the GA component is not justified under the pre-registered criterion.

**Robustness.** All four verdicts are unchanged:
- without market drift (a pre-registered secondary analysis);
- when the 22 scenarios containing a run that reached the safety timeout are excluded.

The development split gave the same directions for all four hypotheses, but only H1 and H4 reached significance there (Table C).

### X.3 Discussion

**Beam search.** The results support searching over candidate repairs, not over search strategies of increasing complexity.
- Beam search with a small width captures most of the available improvement. Its advantage over greedy repair reflects the dependency structure of travel plans: a locally cheaper option (a different flight, for example) changes what is feasible or cheap downstream (pickups, day plans, budget). Keeping several partial repairs alive lets the search avoid such traps.
- With W = 10, beam search is deterministic and optimal on every realistic disruption. It is optimal or near-optimal on most stress disruptions, at a few plan evaluations per repair.

**The genetic algorithm.**
- Under the same evaluation cap, the GA does not outperform random search. Its early stopping leaves most of the budget unused.
- The hybrid's advantage over a wider beam is small, rests on 11 of 420 scenarios, and comes with more full evaluations than the matched beam used.
- Since random search matches or exceeds the GA, the hybrid's gain is best attributed to additional search after the beam, not to the genetic operators.
- We therefore do not claim the GA as a contribution.

**Validation and conflict reporting.** The clearest practical gain is in safety, not in fitness. Validating every candidate repair against the hard constraints, and reporting a conflict when none is feasible, turns every silent failure of the original replanner into an explicit decision for the traveller. The share of trips that can be repaired stays the same.

**Recommended configuration.** Contract-pruned beam search with W = 10, together with hard-constraint validation and conflict reporting. A wider beam, or exhaustive enumeration when the option space is small, can be used when more optimality is needed and the cost allows.

### X.4 Limitations and threats to validity

**Mock mode.**
- All experiments ran in a deterministic mock mode: mock flight, hotel and place providers, and a template-based MockLLM.
- No real LLM or external API was called. API and LLM counts are calls to these mocks.
- The results say nothing about performance with real providers, real price dynamics, or LLM-generated content.

**Latency.**
- Latency is mock-mode wall-clock time, measured while 16 processes ran in parallel. Part of the run was interrupted by a machine sleep.
- It is reported for completeness and is not comparable across arms or with live systems.

**Deviation from the pre-registration (timeout).**
- The pre-registration specifies a 2-second wall-clock safety timeout per search. The experiment runner used 60 seconds for all search arms other than exhaustive search. This applied equally to the development and held-out runs, and it was found only during the held-out analysis.
- 62 of 100,320 held-out runs reached the 60-second limit, largely because of CPU contention and the sleep. 30 of them are in the primary drift-on analysis.
- These runs were kept and flagged, as pre-registered. Excluding every scenario that contains such a run does not change any verdict.
- A 2-second limit would have cut short a much larger number of stochastic runs under the same load, so the two settings are not equivalent.

**Evaluation imbalance in H3.**
- The wider beam (W = 50) was matched to the hybrid's evaluation count on the development split.
- On held-out, the hybrid used 32.0 full evaluations on the stress catalogue against 23.6 for the wider beam. The wider beam in turn used more partial evaluations, which the budget does not count.
- The H3 result should be read with this imbalance in mind.

**Small effects and many ties.**
- 96–98 % of stress comparisons between the strong arms are exact ties, and every realistic comparison is.
- H2 and H3 rest on 17 and 11 non-tied scenarios respectively.

**Synthetic and small option spaces.**
- The scenarios and the stress option space were constructed by the authors.
- Only 32 held-out scenarios have more complete repairs than the evaluation budget, and exhaustive search stays tractable throughout.
- The benchmark therefore cannot show where heuristic search becomes necessary.

**Ground truth and objective.**
- The necessity set and the minimum scope are defined relative to our own contracts and option space.
- The planned manual labelling has not been carried out.
- The objective weights are equal by design. The pre-registered weight sensitivity analysis is not reported here.

**Clustering.**
- The 760 scenarios come from 20 trips.
- The confidence intervals resample whole trips, but the sign and Wilcoxon tests treat scenarios as independent, as pre-registered. That may understate the uncertainty.

**Narrow evidence for H4.** All silent failures arise from a single disruption type, the 10-hour delay.

**Pre-registration provenance.**
- The pre-registration was frozen with a self-verifying digest but was not committed to version control, so its timing has no third-party timestamp.
- The main held-out run was launched twice. The first launch produced no results, because of a launcher error.
- The parameters of the GA were fixed in advance and not tuned.

---

## Part 4: Transfer guide

### Can be transferred directly into the paper
| Item | Location here | Notes |
|---|---|---|
| Table B (hypothesis tests) | Part 1 | Primary, pre-registered. Keep the "GA justified: No" line. |
| Table A1, A2 (first four rows) and A3 | Part 1 | Keep the N/A cells and notes 1–5. Keep the latency footnote. |
| Table C (dev vs held-out) | Part 1 | Keep the label that dev is tuning data. |
| Table D (findings and recommendation) | Part 1 | |
| §X.1 Experimental results | Part 3 | |
| §X.2 Statistical analysis | Part 3 | |
| §X.3 Discussion | Part 3 | |
| §X.4 Limitations | Part 3 | Shorten if needed, but keep the items listed below. |

### Transfer only labelled as supplementary
These are not pre-registered tests, so label them, for example "(supplementary analysis)":
- the optimality rows of Table A2 (match with EXH, gap);
- the Clopper–Pearson effect intervals in Table B;
- the timeout-exclusion and per-catalogue rows of Table B2;
- Part 2 §6.

### Not to transfer
- **Numbers from v1 or v2.** They are separate experiments on the dev trips, with different metric definitions. If the paper reports them, put them in their own tables, and never pool them with Table A.
- **Anything from the weight sensitivity analysis.** No results exist.
- **Latency as a performance claim.**

### Limitations that must be disclosed
1. Deterministic mock mode only: no real LLM or provider calls, and no claims about real-world performance.
2. The 60 s safety timeout instead of the pre-registered 2 s (62 runs hit it), and that excluding the affected scenarios changes no verdict.
3. The H3 evaluation imbalance (32.0 vs 23.6 full evaluations), and that H3's gain cannot be attributed to the GA.
4. The GA is not justified by the pre-registered criterion: H2 failed, significantly in favour of random search.
5. Latency was measured under parallel load and a machine sleep, so it is not comparable.
6. The pre-registered weight sensitivity analysis and the manual ground-truth labelling are not done.
7. Small, synthetic option spaces (exhaustive search is tractable) and effects that rest on few non-tied scenarios.
8. The ground truth depends on our own contracts and option space; scenarios are clustered in 20 trips.
9. The H4 evidence rests on a single disruption type.
10. Pre-registration provenance: not committed to version control, and the main run had a failed first launch that produced no data.
