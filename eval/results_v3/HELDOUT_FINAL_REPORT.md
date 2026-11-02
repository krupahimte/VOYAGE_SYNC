# VoyageSync Metrics v3: held-out final results report

Generated 2026-11-02 from the Phase 10 held-out run. **All numbers are deterministic mock-mode results**: offline mock providers, MockLLM template code, 0 real LLM or network calls (`eval/scenario_set.py:use_backend` forces `VOYAGESYNC_MODE=mock` and an empty API key). Nothing here measures real-provider or real-LLM performance.

Sources:
- `eval/results_v3/heldout/ANALYSIS_HELDOUT.md` and `analysis_heldout.json`: the official, pre-registered analysis (`eval/report_v3.py`, run once on held-out).
- `eval/results_v3/heldout/supplementary/`: read-only verification and supplementary analyses (scripts plus `supplementary_heldout.json`). These are marked **supplementary** wherever they are used. They are not part of the pre-registered tests.
- Raw runs: `eval/results_v3/heldout/main/*/runs.csv` (16 files) and `eval/results_v3/heldout/e2_prime/*/runs.csv` (4 files).

---

## 1. Executive summary

- **The held-out run is complete.**
  - 16 of 16 main processes and 4 of 4 E2′ processes finished, with no errors.
  - 101,840 result rows: 100,320 main plus 1,520 E2′.
  - 760 scenarios per drift condition. No missing or duplicate scenarios, seeds or arms.
  - The pre-registration digest and the ground-truth hashes verify.
- **H1 (beam beats greedy): supported.**
  - E2 (W*=10) Pareto-dominates E1g in 77 scenarios. The reverse happens in 9 (equal 573, incomparable 61).
  - Exact sign test: p = 1.34e-14, Holm-adjusted p = 8.05e-14.
- **H2 (GA beats random search): not supported.**
  - The difference is significant, but in the opposite direction: RS is better in 14 scenarios, E3 in 3, with 403 ties.
  - Rank-biserial r = −0.645. Wilcoxon p = 0.0105, Holm p = 0.0105.
- **H3 (hybrid beats a wider beam, E2′=50): supported.**
  - E4 is better in 11 scenarios, worse in 0, tied in 409. r = +1.0.
  - Mean Δfitness +0.0042, 95 % trip-bootstrap CI [0.0007, 0.0084].
  - Wilcoxon p = 0.00098, Holm p = 0.0020.
  - Important caveat: on held-out, E4 used more full evaluations than E2′ (32.0 vs 23.6). See §10.
- **H4 (no silent failures): supported.**
  - E1 committed an infeasible plan without flagging it in 40 scenarios. E2, E3 and E4 did so in 0.
  - Exact sign test for each pair: p = 1.82e-12, Holm p = 9.09e-12.
- **GA justified (the pre-registered rule needs H2 and H3): no.** Under its own pre-registered rule, the paper must report "GA not justified".
- **Robustness.** All verdicts stay the same in three checks:
  - drift off (secondary analysis);
  - excluding the 22 scenarios that contain a timed-out run (sensitivity check);
  - the large-option-space subset, for direction.
- **Recommended final algorithm:** E2, contract-pruned beam search at the pre-registered W* = 10, with conflict reporting. Reasons are in §11.

**One undisclosed parameter deviation was found** (§8). The runner uses a 60 s safety timeout for the search arms, where the pre-registration specifies 2 s. It applied equally to dev and held-out, and it does not change any verdict.

---

## 2. Held-out experiment completion status

| Item | Expected | Found | Status |
|---|---|---|---|
| Main processes (2 catalogues × 2 drift × 4 trip groups) | 16 | 16, each log ends `wrote … (N runs)` | complete |
| E2′ width-50 processes (2 catalogues × 2 drift) | 4 | 4, each log ends `wrote …` | complete |
| Tracebacks or errors in any log | 0 | 0 | ok |
| Launcher | `eval/results_v3/heldout/RUN_COMMANDS.sh` | final line: "all 16 main processes completed" | ok |

Launch history (recorded in `RUN_COMMANDS.sh`):
- **First launch.** The bash array named `GROUPS` collides with a read-only built-in variable, so every main process ran 0 scenarios. Those empty outputs were deleted. No held-out main result existed or was seen. The 4 E2′ processes don't use that array; they ran correctly and were kept.
- **Second launch.** Main processes only.
- **Machine sleep.** The logs show a jump of about 11 h, from about 4,000 s to about 44,000 s. That is consistent with the machine sleeping during the run. It affects wall-clock latency, and it caused part of the safety-timeout hits (§3).

---

## 3. Dataset and scenario verification

**Scenarios and rows.** Each scenario contributes 66 rows: one per deterministic arm (E1, B1, B2, E1g, E2, EXH) plus 20 seeds for each stochastic arm (E3, E4, RS).

| Catalogue | Drift | Scenarios (expected / found) | Missing | Duplicates | Main rows | E2′ rows |
|---|---|---|---|---|---|---|
| realistic | on | 320 / 320 | 0 | 0 | 21,120 (4 × 5,280) | 320 |
| realistic | off | 320 / 320 | 0 | 0 | 21,120 | 320 |
| stress | on | 440 / 440 | 0 | 0 | 29,040 (4 × 7,260) | 440 |
| stress | off | 440 / 440 | 0 | 0 | 29,040 | 440 |
| **Total** | | **1,520 scenario × drift** | **0** | **0** | **100,320** | **1,520** |

Checks, all passed (`supplementary/verify_heldout.py`):
- **Trip groups.**
  - The 4 trip groups are exactly the 20 pre-registered held-out trips.
  - None of them is a dev trip.
  - Each scenario appears in exactly one group.
- **Arms and seeds.**
  - Every scenario has every arm with the expected number of rows.
  - Each stochastic arm has runs 0–19, with 20 distinct seeds.
- **Run configuration.** Every manifest records:
  - preregistration digest `d2bd96c9…7184`, status `frozen`;
  - seeds 20, beam width 10, budget 500, base seed 0.
  - The E2′ rows are E2 only, at beam width 50, deterministic (1 seed).
- **Row labels.** Each row's trip, kind, catalogue and drift label match the scenario file.
- **Ground truth.**
  - `eval.ground_truth.load_verified` passes for both held-out catalogues. That checks the file hash and the 21 option-space source hashes.
  - The hashes equal the frozen values: realistic `57004d22…`, stress `2dc84138…`.
  - Every row has a ground-truth entry.
- **Initial plans and events.** Each trip is planned once and every arm re-plans a deep copy of it, with the same events from the scenario file. With drift on, the market drift is seeded per scenario, so all arms face the same market.
- **Protected v1/v2 files.** `eval/manifest.py` reports "All protected benchmark files match the manifest", and `git diff` against HEAD is empty for `eval/results`, `eval/results_v2`, `eval/RESULTS.md` and `eval/scenarios`.
- **E1 golden snapshot.** `eval/golden.py` reports that E1 matches the golden snapshot (160 scenarios × 2 drift modes).
- **Pre-registration.** `experiments.verify_frozen()` returns ok.

**Flagged runs (safety timeout).** 62 of 100,320 main rows hit the search wall-clock safety timeout (60 s; see §8). Each is flagged `timed_out=True, truncated=True`. They are kept and flagged, as the pre-registration requires ("flagged, never silently kept").

| Catalogue / drift | E2 | E3 | E4 | RS |
|---|---|---|---|---|
| realistic on | 0 | 4 | 0 | 0 |
| realistic off | 1 | 0 | 2 | 1 |
| stress on | 0 | 4 | 7 | 15 |
| stress off | 0 | 3 | 8 | 17 |

- On dev, 0 runs hit the timeout.
- 22 drift-on scenarios and 24 drift-off scenarios contain at least one timed-out run.
- Of the 60 feasible timed-out runs, 58 reached exactly the same fitness as the same arm's other seeds. One was worse and one was better.
- 2 timed-out runs ended infeasible, both in drift off:
  - the one E2@10 realistic scenario counted as a conflict (299 of 300 feasible);
  - the one E4 stress scenario (419 of 420).
- **Sensitivity check (supplementary):** excluding all 22 drift-on scenarios with a timeout gives the same verdicts:
  - H1: 71 vs 9, Holm 2.6e-12;
  - H2: 3/13/386, r = −0.62, Holm 0.018;
  - H3: 11/0/391, Holm 0.0020;
  - H4: 40 vs 0.

---

## 4. Complete algorithm comparison (held-out, drift on)

**Arms:**
- **E1:** the original contract-aware replanner.
- **B1 and B2:** the legacy baselines (full replan, naive downstream).
- **E1g:** greedy repair (beam width 1).
- **E2@10:** beam search at W*.
- **E2@50:** E2′.
- **E3:** GA.
- **E4@10:** hybrid (beam seeds the GA).
- **RS:** random search.
- **EXH:** exhaustive search.

Means are over runs. For stochastic arms this equals the mean of per-scenario seed means, because every scenario has 20 seeds. Objective terms and fitness are over feasible runs.

### 4.1 Quality, validity and safety (both catalogues pooled, 760 scenarios)

| Arm | Feasible (all hard constraints) | Conflict reported | Silent failure | Mean violations | Fitness ↑ | Disruption ↓ | Money ↓ | Activity time lost ↓ | Approvals ↓ | Activity retention ↑ | Requirement satisfaction |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E1 | 94.74 % | 0.00 % | **5.26 %** | 0.053 | −0.3362 | 0.1007 | 0.1030 | 0.0122 | 0.1204 | 0.9878 | 0.8641 |
| B1 | 94.74 % | 0.00 % | **5.26 %** | 0.053 | −0.8808 | 0.4630 | 0.0816 | 0.0192 | 0.3171 | 0.9808 | 0.8622 |
| B2 | 94.74 % | 0.00 % | **5.26 %** | 0.053 | −0.3625 | 0.1123 | 0.1031 | 0.0124 | 0.1347 | 0.9876 | 0.8638 |
| E1g | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3308 | 0.0955 | 0.1032 | 0.0117 | 0.1204 | 0.9883 | 0.8638 |
| E2@10 | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3158 | 0.1027 | 0.0800 | 0.0127 | 0.1204 | 0.9873 | 0.8645 |
| E2@50 (E2′) | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3071 | 0.1017 | 0.0727 | 0.0123 | 0.1204 | 0.9877 | 0.8645 |
| E3 | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3047 | 0.1026 | 0.0692 | 0.0126 | 0.1204 | 0.9874 | 0.8648 |
| E4@10 | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3046 | 0.1025 | 0.0692 | 0.0126 | 0.1204 | 0.9874 | 0.8648 |
| RS | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3046 | 0.1025 | 0.0692 | 0.0126 | 0.1204 | 0.9874 | 0.8648 |
| EXH | 94.74 % | 5.26 % | 0.00 % | 0.105* | −0.3046 | 0.1025 | 0.0692 | 0.0126 | 0.1204 | 0.9874 | 0.8648 |

\* For the repair arms, violations are counted on the best-effort plan of a reported conflict, which is **not committed**. For E1, B1 and B2 they are violations of committed plans, and those are the silent failures.

**The 40 infeasible scenarios.** They are the same for every arm: the 10-hour outbound delay (`outbound_delay_600`) on every held-out trip, in both catalogues, where arrival comes after the check-in cutoff and no option exists. The repair arms report them as conflicts. E1, B1 and B2 commit them silently.

### 4.2 Scope against the ground truth

| Arm | Tasks regenerated (scenarios with an effect) | Necessity coverage | Excess scope (tasks above the minimum) | Scope optimality (min / \|R\|) |
|---|---|---|---|---|
| E1 | 3.35 | 1.000 | 0.014 | 0.996 |
| B1 | 15.41 | 1.000 | 10.83 | 0.301 |
| B2 | 7.01 | 1.000 | 3.24 | 0.590 |
| E1g | 3.04 | 1.000 | 0.000 | 1.000 |
| E2@10 | 3.11 | 1.000 | 0.061 | 0.984 |
| E2@50 | 3.07 | 1.000 | 0.025 | 0.994 |
| E3 / E4@10 / RS / EXH | 3.07 | 1.000 | 0.024 | 0.994 |

### 4.3 Cost

Latency is mock-mode wall-clock time, measured under 16 parallel processes plus a machine sleep. **It is not comparable across arms or with real providers.** E2@50 ran in the separate, less loaded first launch, which is why it shows a lower median than E2@10.

| Arm | Full plan evaluations | Partial evaluations (beam ranking) | Option generations | Provider API calls | MockLLM calls | Latency median / p95 (ms) | Timed-out runs |
|---|---|---|---|---|---|---|---|
| E1 | n/a | n/a | n/a | 2.39 | 0.226 | 5.0 / 16.1 | 0 |
| B1 | n/a | n/a | n/a | 16.05 | 5.268 | 18.3 / 55.0 | 0 |
| B2 | n/a | n/a | n/a | 6.62 | 0.232 | 7.4 / 28.6 | 0 |
| E1g | 1.00 | 8.25 | 3.34 | 3.34 | 0.226 | 10.4 / 40.0 | 0 |
| E2@10 | 5.70 | 41.75 | 16.19 | 11.83 | 0.234 | 18.7 / 116.2 | 0 |
| E2@50 | 16.76 | 118.46 | 32.95 | 17.04 | 0.240 | 8.3 / 179.4 | 0 |
| E3 | 20.02 | 0 | 35.21 | 16.26 | 0.242 | 332.2 / 1,272.9 | 8 |
| E4@10 | 22.96 | 41.75 | 31.78 | 15.57 | 0.242 | 340.4 / 1,169.0 | 7 |
| RS | 61.91 | 0 | 82.82 | 21.39 | 0.242 | 247.2 / 3,512.9 | 15 |
| EXH | 128.70 | 0 | 150.45 | 22.08 | 0.242 | 16.9 / 801.5 | 0 |

### 4.4 Optimality gap to EXH (supplementary)

The gap is EXH fitness minus the arm's fitness, in scenarios where both are feasible: 300 realistic and 420 stress. EXH was never truncated on held-out.

| Arm | Realistic: mean gap | Realistic: share optimal | Stress: mean gap | Stress: max gap | Stress: share optimal |
|---|---|---|---|---|---|
| E1 | 0.00975 | 74.7 % | 0.04716 | 1.067 | 57.1 % |
| B1 | 0.52486 | 15.7 % | 0.61289 | 1.932 | 10.5 % |
| B2 | 0.03450 | 68.3 % | 0.07466 | 1.910 | 51.9 % |
| E1g | 0.00677 | 90.3 % | 0.04010 | 1.011 | 72.4 % |
| E2@10 | 0 | 100 % | 0.01915 | 1.011 | 86.7 % |
| E2@50 | 0 | 100 % | 0.00425 | 0.491 | 97.4 % |
| E3 | 0 | 100 % | 0.000086 | 0.0245 | 96.7 % |
| E4@10 | 0 | 100 % | 0.000062 | 0.0262 | 99.8 % |
| RS | 0 | 100 % | 0.000012 | 0.0019 | 99.0 % |

On the realistic catalogue, every search arm from E2@10 up reaches the exhaustive optimum in all 300 feasible scenarios.

B1 and B2 sometimes score higher than EXH (B1 in 7 + 4 scenarios, B2 in 33 + 31). They are not limited to the repair region and the frozen context, so they can change tasks the repair arms must keep. That is a different action space, not a better search.

### 4.5 Pre-registered primary metric: Pareto dominance against E1 (supplementary)

Scenarios where both arms are feasible (720 pooled). Each cell gives dominates / is dominated / equal / incomparable, and the exact sign-test p.

| Arm | Dominates E1 | Dominated by E1 | Equal | Incomparable | Sign-test p |
|---|---|---|---|---|---|
| E1g | 151 | 0 | 563 | 6 | 7.0e-46 |
| E2@10 | 210 | 9 | 449 | 52 | 6.7e-51 |
| E2@50 | 200 | 0 | 464 | 56 | 1.2e-60 |
| E3 | 194 | 0 | 464 | 62 | 8.0e-59 |
| E4@10 | 195 | 0 | 464 | 61 | 4.0e-59 |
| RS | 195 | 0 | 464 | 61 | 4.0e-59 |
| EXH | 195 | 0 | 464 | 61 | 4.0e-59 |
| B2 | 99 | 112 | 493 | 16 | 0.41 |
| B1 | 8 | 269 | 80 | 363 | 6.6e-69 |

These p-values are unadjusted and outside the Holm family. E2@10 dominates E1 more often than EXH does, because EXH maximises the scalar fitness, not Pareto dominance.

### 4.6 Metric availability

Metrics from the requested list that v3 does or does not record:

| Requested metric | In v3? | Where / closest v3 field |
|---|---|---|
| Executability | Yes (v3 definition) | `feasible`: all 7 hard constraints, including contracts and schemas. This is **not** v2's executability. |
| Task validity | Partly | `violations` (count of hard-constraint violations) |
| Contract satisfaction (share of contracts) | **No** | Folded into `feasible`. The per-contract share exists only in v1/v2. |
| User-requirement satisfaction | Yes | `requirement_satisfaction` |
| Requirement regression | **No** | v2 only |
| Precision / recall | Legacy only | For E1, B1 and B2 on the realistic catalogue, against the legacy oracle and labelled policy-conditioned (pre-registered). Drift on: E1 0.988 / 1.000, B2 0.546 / 1.000, B1 0.276 / 1.000. Drift off: E1 1.000 / 1.000. Not defined for the new arms or on stress. |
| F1 | **No** | Not recorded in v3 |
| Task Preservation Rate | **No** | v2 only. The closest v3 measure is the `disruption` term (share of user-visible tasks changed). |
| Replanning scope | Yes | `regenerated_count`, `necessity_coverage`, `excess_scope`, `scope_optimality` |
| Plan stability | Indirect | `disruption` term. This is not v1's stability metric. |
| Replanning latency | Yes, mock only | `latency_ms`, with the caveats in §4.3 |
| LLM / MockLLM calls | Yes | `llm_calls` counts MockLLM template invocations; there are 0 real LLM calls |
| API calls | Yes | `api_calls` counts mock provider calls. The pre-registered `api_requests_unique` is **not recorded**. |
| Travel cost change | Yes (v3 definition) | `money` term: Δtotal / budget, plus 1 × the new overrun |
| Within-budget rate | **No** | v2 only |
| Activity-time retention | Yes (v3 definition) | 1 − `activity_time_lost`, over all days. v2 counted only affected day plans. |
| Contract recovery | **No** (as defined in v2) | The closest v3 measure is `feasible` |
| Strict recovery | **No** | v2 only |
| Silent failures | Yes | `silent_failure` |
| Conflicts (reported, not committed) | Yes | `conflict` |

### 4.7 Per catalogue (drift on, from `ANALYSIS_HELDOUT.md`)

Values are means of per-scenario seed means over feasible scenarios. CIs are 95 % trip-cluster bootstraps.

| Catalogue | Arm | Feasible | Conflicts | Silent | Fitness mean [95 % CI] | Seed SD | Plan evaluations |
|---|---|---|---|---|---|---|---|
| realistic | E1 | 300 / 320 | 0 | 20 | −0.2369 [−0.2861, −0.1975] | 0 | n/a |
| realistic | B1 | 300 / 320 | 0 | 20 | −0.7521 [−0.8293, −0.6853] | 0 | n/a |
| realistic | B2 | 300 / 320 | 0 | 20 | −0.2617 [−0.3163, −0.2173] | 0 | n/a |
| realistic | E1g | 300 / 320 | 20 | 0 | −0.2340 [−0.2825, −0.1952] | 0 | 1.00 |
| realistic | E2@10, E2@50, E3, E4@10, RS, EXH | 300 / 320 | 20 | 0 | −0.2272 [−0.2714, −0.1916] (identical) | 0 | 4.20, 6.97, 6.04, 9.99, 7.04, 6.97 |
| stress | E1 | 420 / 440 | 0 | 20 | −0.4071 [−0.4844, −0.3450] | 0 | n/a |
| stress | B1 | 420 / 440 | 0 | 20 | −0.9728 [−1.0232, −0.9262] | 0 | n/a |
| stress | B2 | 420 / 440 | 0 | 20 | −0.4346 [−0.5198, −0.3666] | 0 | n/a |
| stress | E1g | 420 / 440 | 20 | 0 | −0.4000 [−0.4751, −0.3387] | 0 | 1.00 |
| stress | E2@10 | 420 / 440 | 20 | 0 | −0.3791 [−0.4371, −0.3314] | 0 | 6.75 |
| stress | E2@50 | 420 / 440 | 20 | 0 | −0.3642 [−0.4141, −0.3226] | 0 | 23.61 |
| stress | E3 | 420 / 440 | 20 | 0 | −0.3600 [−0.4066, −0.3214] | 9.7e-05 | 29.81 |
| stress | E4@10 | 420 / 440 | 20 | 0 | −0.3600 [−0.4066, −0.3213] | 3.0e-05 | 32.03 |
| stress | RS | 420 / 440 | 20 | 0 | −0.3599 [−0.4066, −0.3213] | 3.8e-05 | 100.31 |
| stress | EXH | 420 / 440 | 20 | 0 | −0.3599 [−0.4066, −0.3213] | 0 | 213.91 |

The drift-off tables are in `ANALYSIS_HELDOUT.md` (secondary analysis).

---

## 5. H1–H4 statistical results (held-out, drift on, pre-registered)

Family: H1, H2, H3, H4 (E1 vs E2), H4 (E1 vs E3) and H4 (E1 vs E4), with Holm's adjustment. Unit of analysis: the scenario (seed means for the stochastic arms). For H2 and H3, positive Δ means the first arm is better (higher fitness).

| | H1 | H2 | H3 | H4 |
|---|---|---|---|---|
| Claim | Beam Pareto-dominates greedy more often than the reverse | GA reaches higher fitness than RS at an equal budget cap | Hybrid reaches higher fitness than a wider beam | E2–E4 have no silent failures, while E1 does |
| Metric | Pareto dominance on the 4 objective terms | Per-scenario seed-mean fitness | Per-scenario seed-mean fitness | Silent failure: an infeasible plan committed without a flag |
| Compared | E2@10 vs E1g | E3 vs RS | E4@10 vs E2@50 (E2′) | E1 vs each of E2@10, E3 and E4@10 |
| Data | Both catalogues, both arms feasible | Stress, both arms feasible | Stress, both arms feasible | Both catalogues, all scenarios |
| n | 720 paired scenarios; 86 decisive | 420 paired; 17 non-tied | 420 paired; 11 non-tied | 760 scenarios; 40 discordant per pair |
| Mean (SD) | Fitness: E2 −0.3158 (0.3398), E1g −0.3308 (0.3693) | E3 −0.359992 (0.3354), RS −0.359918 (0.3353) | E4 −0.359969 (0.3354), E2′ −0.364155 (0.3448) | Silent failures: E1 40, E2 0, E3 0, E4 0 |
| Paired difference | Dominates 77, dominated 9, equal 573, incomparable 61 | Better / worse / tie 3 / 14 / 403. Mean Δ −7.4e-05 (SD 1.18e-03). Median 0. Range [−0.0237, +0.0019] | Better / worse / tie 11 / 0 / 409. Mean Δ +0.004186 (SD 0.0347). Median 0. Range [0, +0.491] | 40 vs 0 for each pair |
| Effect size | 77 / 86 = 0.895 of decisive pairs favour E2 (Clopper-Pearson 95 % [0.811, 0.951]) | Rank-biserial r = −0.645 | Rank-biserial r = +1.000 | 40 / 40 = 1.00 (Clopper-Pearson 95 % [0.912, 1.0]) |
| 95 % CI of the mean Δ (trip bootstrap) | n/a (sign test) | [−0.000195, 0.000000] | [+0.000687, +0.008395] | n/a |
| Test | Exact two-sided sign test | Exact Wilcoxon signed-rank (Pratt) | Exact Wilcoxon signed-rank (Pratt) | Exact two-sided sign test |
| Raw p | 1.341e-14 | 0.01048 | 0.0009766 | 1.819e-12 (each pair) |
| Holm p | 8.046e-14 | 0.01048 | 0.001953 | 9.095e-12 (each pair) |
| Significant at α = 0.05 (Holm) | Yes | Yes, **in the opposite direction** | Yes | Yes |
| Pre-registered rule | More E2 dominations and Holm p < 0.05 | r > 0 and Holm p < 0.05 | r > 0 and Holm p < 0.05 | E2–E4 have 0 silent failures and every Holm p < 0.05 |
| **Verdict** | **Supported** | **Not supported.** RS is significantly better than the GA. | **Supported**, with the budget caveat in §10 | **Supported** |

**GA justified (H2 and H3 both hold): no.**

**Per catalogue (supplementary, unadjusted):**
- H1: realistic 11 vs 0 (p = 0.00098); stress 66 vs 9 (p = 7.7e-12).
- H4: 20 vs 0 in each catalogue (p = 1.9e-06).

**Secondary: large option spaces** (32 stress scenarios with more than 500 complete repairs, unadjusted):

| Test | n | Better / worse / tie | Mean Δ [95 % CI] | r | p |
|---|---|---|---|---|---|
| H2: E3 vs RS | 32 | 3 / 2 / 27 | −0.000615 [−0.002316, 0.000277] | 0.20 | 0.75 |
| H3: E4@10 vs E2@50 | 32 | 6 / 0 / 26 | +0.022034 [0.002896, 0.052343] | 1.00 | 0.031 |

**Secondary: drift off** (supplementary, using the same functions as `report_v3`, Holm within the family):

| Test | Result | r | Holm p | Same verdict as drift on? |
|---|---|---|---|---|
| H1 | 78 dominate vs 10 dominated (equal 558, incomparable 73) | n/a | 2.0e-13 | Yes |
| H2 | 2 / 25 / 393; mean Δ −3.5e-05 [−6.3e-05, −1.1e-05] | −0.850 | 9.8e-06 | Yes (RS better) |
| H3 | 13 / 1 / 405 (n = 419); mean Δ +0.004683 [0.001299, 0.009075] | +0.859 | 3.7e-04 | Yes |
| H4 | 40 vs 0 for each pair | n/a | 9.1e-12 | Yes |

---

## 6. Development versus held-out results

Dev: the 10 templates of `eval/scenarios/trips.yaml`, 5 seeds, 160 realistic plus 220 stress scenarios. It was used only for tuning (W* and E2′) and is **not evidence**. Held-out: 20 new trips, 20 seeds, 320 realistic plus 440 stress scenarios.

| | Dev result | Dev verdict | Held-out result | Held-out verdict |
|---|---|---|---|---|
| H1 | 43 vs 5; Holm 8.2e-08 | holds | 77 vs 9; Holm 8.0e-14 | holds |
| H2 | 0 / 4 / 206; r = −1.0; Holm 0.19 | does not hold | 3 / 14 / 403; r = −0.645; Holm 0.0105 | does not hold (significant the other way) |
| H3 | 5 / 1 / 204; r = +0.669; Holm 0.19 | does not hold | 11 / 0 / 409; r = +1.0; Holm 0.0020 | **holds** |
| H4 | 20 vs 0; Holm 9.5e-06 | holds | 40 vs 0; Holm 9.1e-12 | holds |
| GA justified | no | | no | |

**Stress fitness means, dev → held-out:**

| Arm | Dev | Held-out |
|---|---|---|
| E1 | −0.4030 | −0.4071 |
| E1g | −0.3937 | −0.4000 |
| E2@10 | −0.3882 | −0.3791 |
| E2@50 | −0.3663 | −0.3642 |
| E3 | −0.35898 | −0.35999 |
| E4@10 | −0.35896 | −0.35997 |
| RS | −0.35894 | −0.35992 |
| EXH | −0.35894 | −0.35991 |

Plan evaluations on stress, dev → held-out:

| Arm | Dev | Held-out |
|---|---|---|
| E4 | 33.1 | 32.0 |
| E2@50 | 23.7 | 23.6 |
| RS | 104.1 | 100.3 |

The arm ranking and the effect directions are the same on dev and held-out. H3 changed from "does not hold" to "holds" because held-out has more non-tied scenarios (11 vs 6). Dev was too small to detect it. The held-out result is the confirmatory one.

---

## 7. v1 versus v2 versus v3

| | v1: original evaluation | v2: Metrics v2 | v3: search-algorithm comparison |
|---|---|---|---|
| Research question | Does contract-checked partial replanning regenerate only what is needed, compared with full and naive replanning? | Does it also keep the plan executable, satisfy constraints and user requirements, and preserve unaffected tasks, at what cost? | Given a contract-pruned repair region, which search over candidate repairs finds better repairs (greedy, beam, GA, hybrid, random, exhaustive), and does validation stop silent failures? |
| Data | 160 scenarios on 10 trips (now the v3 dev trips) | The same 160 scenarios | Dev: 160 realistic + 220 stress. **Held-out: 20 new trips, 320 realistic + 440 stress.** |
| Strategies | Ours (E1), B1, B2, 3 ablations | The same | E1, B1, B2 (legacy, unchanged), E1g, E2, E3, E4, RS, EXH, E2′ |
| Metrics | Precision, recall and F1 against the legacy oracle; tasks regenerated; plan stability; constraint satisfaction; latency; LLM / API calls; T2 prompts; unsafe actions | 12 groups: executability, contract and requirement satisfaction, requirement regression, resource use, travel cost change, within budget, activity-time retention, latency, Task Preservation Rate, scope, contract recovery, strict recovery | Hard-constraint feasibility, conflicts, silent failures, a 4-term objective and fitness, Pareto dominance, scope against an exhaustive ground truth (necessity, excess, optimality), requirement satisfaction, evaluations, partial evaluations, option generations, API / MockLLM calls, latency |
| Ground truth | The legacy oracle (contract propagation); 71 / 71 hand labels agree | The same oracle | Necessity set and minimum scope over **all feasible repairs** in the option space (exhaustive), hash-protected. Manual labels not yet done. |
| Statistics | Descriptive | Descriptive | Pre-registered tests, Holm correction, trip-cluster bootstrap CIs, rank-biserial effects, 20 seeds |
| Headline (drift on) | Ours: precision 99.6 %, recall 100 %, F1 99.7 %, stability 88.8 %, 2.27 tasks, 0.22 LLM / 1.64 API calls. B2: 42.7 %, 6.19 tasks. B1: 15.8 %, 14.39 tasks. | Ours: TPR 99.95 %, scope 15.3 %, plan-valid 93.8 %, contract satisfaction 99.8 %, strict recovery 88.8 %. B2: TPR 78.05 %. B1: TPR 59.87 %. | §4 and §5 of this report |
| Status | Reproduced exactly in Phase 10 (all 1,920 rows, except latency). Files unchanged. | Files unchanged (manifest verified) | This report |

**Do not mix the versions.**
- The v3 rows for E1, B1 and B2 are new runs on the held-out trips, scored with the v3 objective and the v3 hard-constraint validator. They are not the v2 B1/B2 results and must not be pooled with them.
- Legacy precision on held-out (E1 98.8 %) is a different scenario set from v1's 99.6 %.
- The v3 "feasible" is not v2's "executability", and v3 "activity retention" is not v2's 3c.

---

## 8. Pre-registration compliance and deviations

The frozen file `eval/preregistration.yaml` has status frozen, `frozen_sha256` d2bd96c9…7184 and frozen_at 2026-11-02. It is **unchanged**, and its self-excluding digest verifies. Every run manifest carries that digest.

**Compliant:**
- **Split.** 20 held-out trips, unit = trip, disjoint from dev.
- **Run parameters.** Seeds 20 with the rule sha256(base 0 | scenario | run); budget 500; W* = 10 and E2′ = 50 (frozen values); GA parameters at the pre-registered defaults.
- **Analysis.** Tests, Holm family, criteria and drift-on primary analysis as pre-registered.
- **Ground truth.** Hashes equal the frozen values.
- **Baselines.** Legacy B1 and B2 kept and unchanged.
- **Held-out analysis run.** `report_v3.py` was run once on the held-out data.

**Deviations recorded in the frozen file before the freeze** (unchanged):
1. Split: 10 existing trips as dev and 20 new trips as held-out, instead of a stratified 9/21 split.
2. The stress catalogue: a larger option space plus 6 combined two-event disruptions. This also covers the stress option counts that differ from `options_per_task`.
3. Ground-truth source hashes re-stamped after a recompute that reproduced the stored ground truth exactly.
4. H2/H3 direction criterion: rank-biserial > 0 instead of median Δ > 0.
5. Report fixes: differences rounded to 1e-9; Holm p-values no longer rounded.

**Deviations and issues found during this held-out analysis** (not in the frozen file, which must not be edited):

| # | Deviation | Effect |
|---|---|---|
| D1 | **Safety timeout is 60 s, not the pre-registered 2 s.** `eval/experiments.py:145` passes `timeout_s=60` to every search arm except EXH, which gets the ground-truth timeout. The pre-registration says `wall_clock_timeout_s: 2`. This was in place for both dev and held-out and was not recorded as a deviation. | With 2 s, many more runs would have been cut short: 3,343 RS, 349 E3 and 254 E4 runs had a total latency above 2 s under the parallel load. 62 runs still hit 60 s (§3). All are flagged and kept. Excluding the affected scenarios leaves every verdict unchanged. |
| D2 | **The main run was launched twice** (`heldout_runs: 1`). The first launch ran 0 main scenarios (the bash `GROUPS` bug), so no main result existed or was seen. Its E2′ results were kept. | None on results. Disclosed. |
| D3 | **One logical run split into 16 parallel processes** by trip group. On dev, splitting reproduced the single-process rows exactly (308 / 308). | None on results. It affects latency only. |
| D4 | **Machine sleep and CPU contention** during the run. | Latency is unreliable as an absolute value; it also contributed to the timeouts in D1. |
| D5 | **The frozen file was not committed to git**, although its header says it would be. This followed the instruction not to commit. | Its integrity is checked by its self-digest, but there is no third-party timestamp of when it was frozen. |
| D6 | **Pre-registered secondary metrics that `report_v3.py` does not produce:** `pareto_dominance_vs_e1` (named as the **primary metric**) and `optimality_gap_vs_exh` are computed here as supplementary (§4.4, §4.5); `api_requests_unique` is not recorded; the **weight sensitivity analysis (×0.5 / ×1.5) was not run**; the `preference_term` is still TBD (not measured); the **manual labels** (2 annotators, κ) are not done. | They must be reported as not done, or done before submission. |
| D7 | **Exclusions and failed runs:** none excluded. 62 runs were flagged timed out (2 infeasible, both drift off). There were 0 failed processes. | Disclosed in §3. |

No change was made to scenario selection, algorithms, metric definitions or the frozen parameters after the freeze.

---

## 9. Key findings

1. **Search over candidate repairs beats greedy repair (H1).**
   - Beam search at W*=10 Pareto-dominates the greedy repair in 77 scenarios. It is dominated in 9 (89.5 % of decisive pairs).
   - It reaches the exhaustive optimum in 100 % of feasible realistic scenarios and 86.7 % of feasible stress scenarios.
   - It needs about 5.7 full evaluations on average.
2. **Validation and conflict reporting prevent silent failures (H4).**
   - In all 40 unrepairable scenarios, E1, B1 and B2 commit an infeasible plan without a flag.
   - Every repair arm reports a conflict (a T2 DECIDE action) instead.
   - All 40 are the 10-hour outbound delay, so this evidence rests on one disruption type.
3. **The GA adds nothing over random search (H2).**
   - At the same budget cap of 500, RS is better than or equal to the GA in all but 3 of 420 stress scenarios (r = −0.645, Holm p = 0.0105).
   - The GA stops early: patience 10 means about 30 evaluations, against about 100 for RS.
   - The pre-registered rule therefore gives **"GA not justified"**.
4. **The hybrid beats a wider beam (H3), but the gain is small and partly bought with more evaluations.**
   - E4 is never worse than E2@50 and better in 11 of 420 scenarios (mean Δ +0.0042).
   - E4 used 32.0 full evaluations, against 23.6 for E2′.
   - Since RS beats the GA (H2), the hybrid's gain comes from searching more after the beam, not from the genetic operators as such.
5. **Most comparisons are exact ties.**
   - 96–98 % of stress scenarios, and every realistic scenario, give the same fitness for E2@50, E3, E4, RS and EXH.
   - The realistic option space (48 plans or fewer) is solved optimally by every search arm.
6. **Exhaustive search is tractable on this benchmark.** EXH was never truncated and needs 214 evaluations on average on stress. That is far cheaper than the 10,000 cap.
7. **E1 is already near-optimal on scope** (excess scope 0.014 tasks, necessity coverage 1.0). The new arms mainly improve the money term (0.103 → 0.069) and conflict handling, not the scope. Approvals are equal for all repair arms.

---

## 10. Limitations and threats to validity

- **Mock mode only.**
  - Providers are deterministic mocks; MockLLM is template code; no real LLM or network calls.
  - API-call counts are calls to mock providers.
  - Latency is mock wall-clock time under 16 parallel processes and a machine sleep, so it is not comparable across arms or launches, nor with real APIs.
- **Effects concern few scenarios and are small.**
  - H2 rests on 17 non-tied scenarios and H3 on 11.
  - H3's mean Δ of 0.0042 fitness units is about 1 % of a stress fitness value. One scenario contributes 0.49 of the summed difference of 1.76 (28 %).
- **The H3 budget match is approximate.**
  - E2′ was chosen on dev as the widest beam whose mean full evaluations are at most E4's. On held-out, E4 used 36 % more full evaluations (32.0 vs 23.6).
  - E2′ also ran 187.7 partial evaluations per scenario against E4's 60.1 in its beam stage. Partial evaluations are not in the budget.
- **H2 compares an equal budget cap, not equal spend** (as pre-registered). The GA's stopping rule (patience 10) was not tuned. A tuned GA might behave differently; this benchmark does not test that.
- **The option spaces are small and synthetic.**
  - The stress catalogue was built to exceed the budget, yet only 32 held-out scenarios have more than 500 complete repairs, and EXH still solves every scenario.
  - The benchmark therefore cannot show where heuristic search is needed.
- **The ground truth depends on the model.** The necessity set and the minimum scope depend on the contract definitions, the option space (k) and the drift timing. These are the stated limitations. The manual labels (2 annotators) are not yet done.
- **No weight sensitivity analysis.** The objective weights are equal a priori, and the pre-registered ×0.5 / ×1.5 analysis was not run.
- **Clustering.** 760 scenarios come from only 20 trips. The CIs resample whole trips, but the Wilcoxon and sign tests treat scenarios as independent, as pre-registered. That may understate the uncertainty.
- **Narrow H4 evidence.** All silent failures come from a single disruption type, the 10-hour delay.
- **Timeouts.** 62 runs hit the 60 s safety timeout, which also differs from the pre-registered value (D1). The verdicts are robust to excluding the affected scenarios.
- **No commit, no third-party timestamp** for the pre-registration (D5).
- **Same team.** The same people designed the scenarios, the contracts, the objective and the algorithms.

---

## 11. Recommended final algorithm

**Recommendation:** E2, contract-pruned beam search at W* = 10, with the hard-constraint validator and conflict reporting. This is the pre-registered tuned configuration. The GA should not be claimed as a contribution.

**Reasons:**
- **Supported by the evidence:**
  - beats greedy (H1);
  - no silent failures (H4);
  - optimal on 100 % of feasible realistic scenarios;
  - deterministic (seed SD 0), so no seed variance.
- **Cheap:** 5.7 full evaluations and 11.8 API calls on average.
- **The GA is not justified** by the pre-registered rule (H2 fails, and in the reverse direction).
- **E4's quality gain is better explained by extra search.** RS reaches it too (99.0 % optimal on stress, against 99.8 % for E4), at the cost of more evaluations.

**When more quality is needed on large spaces:**
- **E2 at a wider width.** E2@50 is 97.4 % optimal on stress, at 16.8 evaluations.
- **EXH when the space is below `max_exhaustive`.** It is tractable everywhere on this benchmark.

**E4 in the paper:** report it as the best-scoring budgeted arm (H3), together with the budget caveat, not as evidence for the GA.

Whether the paper keeps the hybrid as its headline system is a framing decision for the team. The data supports "beam search with validation" more cleanly than "beam + GA".

---

## 12. Paper-ready tables (held-out, mock mode, drift on)

**Table A: pre-registered hypothesis tests** (Holm-adjusted over 6 tests)

| Hypothesis | Comparison | n (decisive) | Result | Effect | p | p (Holm) | Verdict |
|---|---|---|---|---|---|---|---|
| H1 | E2 (W=10) vs greedy | 720 (86) | 77 dominate / 9 dominated | 0.895 [0.811, 0.951] | 1.34e-14 | 8.05e-14 | Supported |
| H2 | GA vs random search | 420 (17) | 3 better / 14 worse | r = −0.645 | 0.0105 | 0.0105 | Not supported (reverse) |
| H3 | Hybrid vs beam (W=50) | 420 (11) | 11 better / 0 worse; Δ = +0.0042 [0.0007, 0.0084] | r = 1.00 | 9.8e-04 | 0.0020 | Supported |
| H4 | E1 vs E2 / E3 / E4 | 760 (40) | 40 vs 0 silent failures | 1.00 [0.912, 1.0] | 1.82e-12 | 9.09e-12 | Supported |

**Table B: algorithm comparison** (760 scenarios, both catalogues pooled)

| Arm | Feasible | Silent failure | Conflict reported | Fitness | Optimal vs EXH (stress) | Excess scope | Plan evaluations | API calls |
|---|---|---|---|---|---|---|---|---|
| E1 (contract BFS) | 94.7 % | 5.3 % | 0 % | −0.336 | 57.1 % | 0.014 | n/a | 2.39 |
| B1 (full replan) | 94.7 % | 5.3 % | 0 % | −0.881 | 10.5 % | 10.83 | n/a | 16.05 |
| B2 (naive) | 94.7 % | 5.3 % | 0 % | −0.363 | 51.9 % | 3.24 | n/a | 6.62 |
| E1g (greedy) | 94.7 % | 0 % | 5.3 % | −0.331 | 72.4 % | 0.000 | 1.0 | 3.34 |
| E2 (beam, W=10) | 94.7 % | 0 % | 5.3 % | −0.316 | 86.7 % | 0.061 | 5.7 | 11.83 |
| E2′ (beam, W=50) | 94.7 % | 0 % | 5.3 % | −0.307 | 97.4 % | 0.025 | 16.8 | 17.04 |
| E3 (GA) | 94.7 % | 0 % | 5.3 % | −0.305 | 96.7 % | 0.024 | 20.0 | 16.26 |
| E4 (hybrid) | 94.7 % | 0 % | 5.3 % | −0.305 | 99.8 % | 0.024 | 23.0 | 15.57 |
| RS (random) | 94.7 % | 0 % | 5.3 % | −0.305 | 99.0 % | 0.024 | 61.9 | 21.39 |
| EXH (exhaustive) | 94.7 % | 0 % | 5.3 % | −0.305 | 100 % | 0.024 | 128.7 | 22.08 |

**Sentences that can be quoted:**
- "On 760 held-out scenarios from 20 unseen trips, contract-pruned beam search Pareto-dominated greedy repair in 77 scenarios and was dominated in 9 (Holm-adjusted p = 8.0 × 10⁻¹⁴)."
- "Hard-constraint validation turned all 40 unrepairable scenarios into reported conflicts; the original replanner and both legacy baselines committed them silently (p = 9.1 × 10⁻¹² for each comparison)."
- "A genetic algorithm did not outperform random search at an equal evaluation budget; random search was better in 14 of 17 non-tied scenarios (r = −0.65, p = 0.010), so by our pre-registered criterion the GA component is not justified."
- "Seeding a GA with beam results gave a small but consistent improvement over a wider beam (11 of 420 scenarios better, none worse; mean Δ = 0.004, 95 % CI [0.0007, 0.0084]); the hybrid used more full evaluations than the wider beam (32.0 vs 23.6)."
- "All results are from deterministic mock providers; no real LLM or API calls were made."

---

*No source code, benchmark scripts, scenarios, frozen pre-registration or existing results were modified in producing this report. New files: `eval/results_v3/heldout/ANALYSIS_HELDOUT.md`, `analysis_heldout.json` (official) and `eval/results_v3/heldout/supplementary/`. Nothing was committed.*
