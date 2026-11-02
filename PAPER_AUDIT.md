# Paper audit: VoyageSync reconstructed research paper

This audit covers `VoyageSync_Reconstructed_Research_Paper.md`. It was prepared on 2026-11-02 by comparing the paper with:
- the original paper (`C:\Users\admin1\Downloads\VoyageSync_Original_Style.pdf`, unchanged);
- the repository code;
- the v1, v2 and v3 result files.

No code, evaluation script, result file or pre-registration was modified, and nothing was committed.

---

## 1. What changed from the original paper

| Original | Reconstructed |
|---|---|
| Title: "…Hybrid System for Adaptive Travel Planning" | "…Risk-Tiered System for Adaptive Travel Planning with Search-Based Repair Optimization" |
| The BFS–ToT–GA hybrid as *proposed future work* (§V) | Implemented: a new §V, *Search-Based Repair Optimization*. Beam search replaces ToT, and the GA and the hybrid are evaluated. |
| One experiment (Table I, 160 scenarios, B2 vs proposed) | Three stages: v1 (Table I), v2 (Table II) and v3 (Tables III–VI), separated in Tables VII and VIII |
| §VI-D "Planned ablation" (E1–E4, held-out "where feasible") | Done: a pre-registered held-out evaluation with Holm-corrected tests |
| Proposed metrics (latency, TPR, scope, recovery, constraint satisfaction) | Measured in v2, with formulas taken from `eval/metrics_v2.py` and `METRICS_REPORT.md` |
| §VIII: a decision log "should record…" | Implemented (§V-I), with a retrieval endpoint |
| "If no feasible candidate exists, report the conflict…" (proposed) | Implemented (§V-H): a T2 DECIDE action with a best-effort plan |
| §VII: PostgreSQL state store | SQLite (SQLModel/SQLAlchemy), corrected |
| Duplicated paragraphs in §IV and §VI (each subsection printed twice) | Removed |
| No architecture details, equations or algorithm description | Added: §III, equations (depth, risk score, objective, metrics) and Algorithm 1 (GA) |

## 2. Original sections retained

| Original section | Kept as |
|---|---|
| Problem framing (I, III) | §I |
| Related-work discussion: TDAG, ReAct, TravelAgent, delegated agents, tourism recommenders; the caution against comparing with TDAG | §II |
| HTN-style decomposition through `propose_template_tasks()` (IV-A) | §IV-A |
| Kahn's algorithm and longest-path depth (IV-B) | §IV-C |
| Contract-aware partial replanning and the five-hour-delay example (IV-C) | §IV-D |
| Risk tiers (IV-D) | §IV-E, now with the exact rules and score |
| The v1 Table I numbers and the derived 63.3 % / 71.2 % / +33.4 pp figures | §VII-A |
| The limitations about mock mode, TDAG, the DAG assumption and the missing user study | §IX |
| The reference list | References [2]–[10], unchanged and still to be verified |

## 3. Sections substantially rewritten or new

- **Rewritten:** Abstract, §I (now ending in a list of contributions), §II (citation placeholders added), §VIII Discussion, §IX Threats to validity, §X Conclusion.
- **New:**
  - §III System Architecture;
  - §V Search-Based Repair Optimization (workflow, 7 constraints, 4-term objective, 8 arms, the GA algorithm, commit, conflicts, decision log);
  - §VI Methodology, split into v1, v2 and v3;
  - §VII Results, with Tables I–VIII.

## 4. Implementation facts verified or corrected

Each fact was checked in the code; file references are given.

**Verified as described:**

| Fact | Source |
|---|---|
| Task set: 2 flights, stay, 2 transfers, weather and a day plan per day, budget | `backend/app/core/templates.py:73` (`propose_template_tasks`) |
| Decomposition: a model proposal is validated against the template shape, with fallback to the template; in mock mode MockLLM returns the template; inputs and edges are never taken from the model | `core/orchestrator.py:209–242`, `templates.py:93` |
| 9 edge-contract types used by the edge templates (11 defined in the code, 2 not used by the templates) | `templates.py:142–183`, `core/contracts.py:20` |
| Kahn's algorithm for the topological order | `core/graph.py:232–262` |
| Longest-path depth | `core/replanner.py:80` (`_depths`) |
| Contract strategy: a child goes stale only if its edge contract breaks; levels processed in depth order; regenerated tasks re-checked; frozen context | `core/replanner.py:1–17`, `:190–253` |
| DFS used for B2's descendants (stack-based) | `core/replanner.py:91` (`_descendants`) |
| Risk Gate: T0/T1/T2; book/change/cancel/pay always T2; risk score weights 0.4/0.3/0.2/0.1; thresholds 0.15 and 0.6; spend threshold 250; signed single-use grants | `core/risk_gate.py` |
| DECIDE is always T2 | `core/risk_gate.py` |
| Six agents (Flight, Hotel, Transfer, DayPlan, Weather, Budget) as Python `BaseAgent` classes in a registry | `agents/registry.py:23–33` |
| Candidate generation | `BaseAgent.candidates` and the agent implementations |
| 7 hard constraints, with budget soft | `core/repair/constraints.py:1–13` |
| Objective formulas: shift scale 180 min, overrun factor 1.0, equal weights | `core/repair/objective.py` |
| Deb key | `core/repair/objective.py` |
| Beam search is deterministic, named beam not ToT; RS stopping rule | `core/repair/policies.py` |
| GA parameters | `eval/preregistration.yaml` (parameters.ga) and `policies.py` (`GAParams`) |
| Atomic commit on a deep copy, re-validation, `restore_from`; conflict with best-effort plan | `core/repair/commit.py:1–15` |
| Decision log endpoint `GET /trips/{trip_id}/disruptions/{event_id}/decision` | `api/trips.py:292` |
| WebSocket `/ws/trips/{trip_id}` | `api/live.py:81` |
| SQLite default (`sqlite:///./voyagesync.db`) via SQLModel | `config.py:29`, `db/database.py` |
| Backend dependencies: FastAPI, Uvicorn, Pydantic, SQLModel | `backend/requirements.txt` |
| Frontend dependencies: React 19, TypeScript, Vite, Tailwind CSS, reactflow, lucide-react | `frontend/package.json` |
| OpenAI-compatible LLM client and MockLLM | `llm/client.py` |
| Integrations: Duffel, Geoapify, Open-Meteo, Frankfurter, OSRM | `config.py:43–44`, `integrations/*.py` |
| Repair strategies are selectable through the API | `schemas/disruption.py:20` (strategy literal) |

**Corrected:**
1. PostgreSQL → SQLite.
2. "Redirect users to a vendor checkout" (original §IV-D): **not implemented**. Bookings go to a sandbox booking provider (`integrations/booking.py`). The paper now says this.
3. The original's ToT component is not claimed; the implemented search is beam search.

**Facts you should know when writing:**
- **The application's default replanning strategy is still `contract` (E1)** (`orchestrator.handle_disruption(strategy="contract")`). The recommended beam search must be selected through the API. The paper states this (§IX-9). Decide whether to change the default before submission; that is a code change and was not made.
- **The frontend has T2 approval cards but no conflict-specific view.** No "conflict" handling was found in `frontend/src`.

## 5. Results incorporated

All values were taken from the result files, not from progress notes.

**v1** (Table I): source `eval/RESULTS.md` §2 and `eval/results/summary.json`.
- Proposed, B2 and B1: precision, recall, F1, stability, tasks regenerated, LLM and API calls.
- Hand-label agreement 71/71.
- Reproduction: 1,920 rows match.

**v2** (Table II): source `eval/results_v2/METRICS_REPORT.md` (drift on).

**Two corrections to the values supplied in your instructions:**

| Your figure | What the report says | Used in the paper |
|---|---|---|
| B2 median latency 0.0027 s | **0.0029 s** (p95 0.0076 s). 0.0027 s is the *proposed* approach's p95 with drift **off**. | 0.0029 s |
| "900 replanned scenarios and 60 no-effect scenarios" | These are **runs** over 6 strategies (960 runs = 160 × 6), i.e. 150 replanned and 10 no-effect scenarios per strategy (`eval/results_v2/results.csv`) | described as runs |

Added from the same report (verified): *within budget after replan* and the *user-visible TPR* row.

**v3** (Tables III–VI):
- Primary: `eval/results_v3/heldout/ANALYSIS_HELDOUT.md` and `analysis_heldout.json` (official `report_v3.py`).
- Supplementary: `eval/results_v3/heldout/supplementary/supplementary_heldout.json`.
- Dev (Table VI only): `eval/results_v3/dev/ANALYSIS_DEV.md`.
- Pooled optimality shares (64.4 / 79.9 / 92.2 / 98.5 / 98.1 / 99.9 / 99.4 %) were computed as n-weighted combinations of the per-catalogue supplementary values.
- Largest EXH run: 3,432 evaluations, from the held-out `runs.csv`.

**Not used:** anything from the weight sensitivity run. It was stopped; no results exist.

## 6. Tables and figures

**Tables:**
- Table I: v1 benchmark.
- Table II: v2 operational metrics.
- Table III-A: v3 validity, safety and quality.
- Table III-B: v3 scope and optimality.
- Table III-C: v3 cost.
- Table IV: H1–H4.
- Table V: robustness.
- Table VI: dev vs held-out.
- Table VII: strategies × stages.
- Table VIII: the three stages.
- In-text tables: the contracts (§IV-B), the hard constraints (§V-B), the search arms (§V-D), the GA parameters (§V-E) and the timeout distribution (§IX-2).

**Figures** (placeholders with captions, to draw in Overleaf):
- Fig. 1: architecture.
- Fig. 2: dependency graph with contracts.
- Fig. 3: contract-aware replanning example.
- Fig. 4: repair-search workflow.
- Fig. 5: Risk Gate.
- Fig. 6: v3 evaluation workflow.

Each caption names only implemented components.

**Algorithm:** Algorithm 1 (GA), written from the pre-registered parameters and `policies.py`. Check it against `ga()` in `backend/app/core/repair/policies.py` if you typeset it.

## 7. Citations that need verification

**Retained from the original, to be verified** (authors, titles, venues, years):
- [2] TDAG;
- [3] ReAct;
- [4] TravelAgent (incomplete author list);
- [5] Towards Full Delegation (incomplete author list);
- [6] Tourist Destination Recommendations (no authors);
- [7] Synth TRIPs;
- [8] Buhalis & Law 2008;
- [9] LangChain docs and [10] Amadeus docs. **Not used** by VoyageSync (no LangChain or Amadeus in the code). Remove them unless cited.

[1] is our own original paper; complete its venue or status.

**Missing citations, marked [CITATION NEEDED] in the paper.** These are suggestions to look up and verify; none was added as a reference:
- HTN planning (e.g. the SHOP/SHOP2 line of work, or Erol, Hendler & Nau on HTN planning);
- Kahn's topological sorting algorithm (Kahn, *Communications of the ACM*, 1962);
- plan repair and replanning (e.g. Fox et al., "Plan stability: replanning versus plan repair", ICAPS 2006);
- beam search (e.g. Lowerre's HARPY system, or a standard AI textbook);
- genetic algorithms (Holland 1975 and/or Goldberg 1989);
- Deb's constraint-handling rules (Deb, 2000, *Computer Methods in Applied Mechanics and Engineering*);
- Tree of Thoughts (Yao et al., NeurIPS 2023);
- adjustable autonomy and human-in-the-loop;
- the Wilcoxon signed-rank test (Wilcoxon 1945) and Pratt's zero handling (Pratt 1959);
- Holm's procedure (Holm 1979);
- the rank-biserial correlation (e.g. Kerby 2014);
- the cluster or case bootstrap (e.g. Efron & Tibshirani 1993);
- Clopper–Pearson intervals (1934).

All of these must be checked before use; I have not verified their bibliographic details.

## 8. Claims that could not be verified

| Claim | Status |
|---|---|
| Live provider integrations (Duffel, Geoapify, Open-Meteo, Frankfurter, OSRM) and the OpenAI-compatible LLM client *work* | Not verified. The code exists, but no credentials or connectivity were tested. The paper says "implemented, not validated". |
| Vendor-checkout redirection (original paper) | Not found in the code; removed |
| Authentication and background scheduler | Not present (consistent with the original). Stated as not implemented. |
| Hand-label agreement 71/71 (v1) | Taken from `eval/RESULTS.md`; not re-run |
| Your name order and affiliation | Copied from the original PDF |

## 9. Limitations and deviations disclosed in the paper (§IX)

1. Mock mode only: no real LLM or provider calls; latency not comparable.
2. **Timeout:** 60 s used, against the pre-registered 2 s. 62 runs reached it: E2 1, E3 11, E4 17, RS 33 (30 in drift on). The exclusion analysis shows no verdict change.
3. EXH enumeration cap 20,000 / 100,000 against the pre-registered 10,000. Non-binding (maximum 3,432).
4. **H3 imbalance:** E4 used 32.0 full evaluations against 23.6 for E2′ (stress); E2′ used 187.7 partial evaluations against 60.1.
5. Weight sensitivity analysis not reported (stopped); manual labelling not done; `api_requests_unique` not recorded; preference term not measured.
6. Pre-registration not committed to Git; the main run was launched twice (the first produced no data).
7. Synthetic scenarios; a model-dependent ground truth; 20 trip clusters; few non-tied scenarios; H4 rests on one disruption type.
8. Scalability: exhaustive search is tractable in the tested spaces.
9. No user study.
10. Integrations, authentication, scheduler and deployment not validated; no conflict UI; the default strategy is still E1.
11. No direct comparison with TDAG.

## 10. Remaining work before submission

- [ ] Verify every reference ([2]–[8]), drop [9]–[10], and add verified references for each [CITATION NEEDED].
- [ ] Draw Figs. 1–6 from the captions (they describe only implemented components).
- [ ] Decide whether the paper reports the v3 legacy B1/B2 held-out rows (they are in `ANALYSIS_HELDOUT.md`; currently excluded from Tables III).
- [ ] Decide whether to run the weight sensitivity analysis (about 4 h; the infrastructure exists, uncommitted) or keep it as future work, as written.
- [ ] Decide whether to make beam search the application's default strategy (a code change). If not, keep the §IX-9 sentence.
- [ ] Optionally commit the frozen pre-registration and the results, to give them a timestamp. The paper currently discloses that they are uncommitted.
- [ ] Complete reference [1] (the status of the original paper).
- [ ] Check page limits: the reconstructed paper is far longer than four pages. Move Tables VII and VIII, Table III-C and the GA parameter table to an appendix if needed.
- [ ] Have a co-author check §IV-E (the Risk Gate) and §V-E (the GA) against the code, if they are typeset as formal pseudocode.
