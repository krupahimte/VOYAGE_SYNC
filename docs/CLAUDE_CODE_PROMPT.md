# Prompt for Claude Code: complete VoyageSync (Phases 3–8)

> **How to use:** open Claude Code in the project root (`voyageesync/`) and paste everything below the line.
> Before that, copy `VoyageSync_Project_Plan.md` and `VoyageSync_Architecture.png` into `voyageesync/docs/`, and commit your current work yourself so there's a rollback point. Claude Code will not touch git; it stops after every phase and asks you to commit.

---

You are taking over **VoyageSync**, our final-year IPD project at DJSCE. It is a dependency-aware, risk-tiered multi-agent travel planner. Phases 0–2 are already built and tested. Your job is to complete Phases 3–8 end to end, so the project ends with a working demo, an evaluation with real numbers, and documentation.

## 0. Read and explore first (don't write code yet)

1. Read `docs/VoyageSync_Project_Plan.md` fully and look at `docs/VoyageSync_Architecture.png`. Together they are the spec. Sections 3, 5, 6, 9 and 10 matter most.
2. Explore the repo and map what already exists. Expected, but verify:
   - `backend/app/core/graph.py`: TaskNode, Edge, contracts, GraphStore (`update_node_status`, `get_node`, `list_nodes`, `list_edges`, `get_history`), versioned persistence
   - `backend/app/core/scheduler.py`: async wave-based topological scheduler, semaphore concurrency, retries, failure propagation, ExecutionEvent log
   - `backend/app/agents/base.py`: BaseAgent, AgentResult (`.ok()` / `.fail()`), AgentRegistry
   - `backend/app/agents/mocks.py`: 6 mock agents (Flight, Stay, Transfer, DayPlan, Weather, Budget) and `build_default_registry()`
   - `backend/tests/`: about 109 passing tests; `pytest.ini` uses `asyncio_mode = auto`
   - `frontend/`: React + Vite + TypeScript (`TripGoalForm.tsx`, `TripPlanner.tsx`, `AdaptivePlanPreview.tsx`). The Create trip button currently shows "planning engine is under development".
3. Check whether **any Phase 3 (Orchestrator) work was already started**, for example an `orchestrator.py`, LLM client, or `/trips` route. If so, finish it rather than duplicating it.
4. Run the test suite and confirm it is green before changing anything. The environment is **Windows/PowerShell**, so use `;` rather than `&&`. Run tests with `.venv\Scripts\python.exe -m pytest tests/ -v` from `backend/`.
5. Create `PROGRESS.md` in the repo root. Record what exists, any differences from the plan, and a checklist for Phases 3–8. Keep it updated after every phase. It is how the team follows your work.
6. **Stop here and report your findings to me** (what exists, test results, anything that differs from the plan, any Phase 3 work already started). Wait for me to reply "continue" before starting Phase 3.

## Ground rules

- **Reuse, don't rewrite.** Build on the existing graph, contracts, scheduler and agent interfaces. Refactor only when necessary, and note it in `PROGRESS.md`.
- **Keep the existing node types and mock agents.** The architecture slide shows four user-facing agents (Flight, Hotel, Itinerary, Budget). TRANSFER, DAY_PLAN and WEATHER nodes can stay as they are; transfer and day planning belong to the itinerary side, and weather is a data source for the Monitor and Itinerary. Document that mapping instead of renaming code.
- **Build these ourselves**, without LangGraph, CrewAI or AutoGen: Orchestrator, partial re-planning, Disruption Monitor, Risk Gate, Evaluation Harness. They are the research contribution. External APIs and the LLM are used only behind thin agent wrappers.
- **Mock mode by default.** Every external call (LLM and APIs) must have a deterministic mock, chosen through config or env (for example `VOYAGESYNC_MODE=mock|live`). The full test suite and the demo must run with **no API keys and no network**.
- **Secrets:** read keys from `.env` only, add `.env.example`, and never commit keys. If a live key is missing, fall back to mock mode and log a warning. Don't stop.
- **No git actions at all.** Do not run `git add`, `git commit`, `git push`, `git checkout`, `git reset`, `git stash`, `git merge`, `git rebase`, or any other git command that changes the repo or its history. Read-only commands (`git status`, `git diff`, `git log`) are fine for understanding the repo. I make every commit myself.
- **A phase is done only when** its tests are written and passing, the full suite is green, and `PROGRESS.md` is updated.
- **Stop after every phase and ask me to commit.** At the end of each phase, stop and post a short phase report:
  1. what was built
  2. files created or changed
  3. test results (count passed / failed)
  4. how I can check it myself (one or two commands or clicks)
  5. a suggested commit message, e.g. `feat(phase-3): orchestrator with LLM decomposition`
  6. the line: **"Please review and commit Phase N, then reply 'continue' to start Phase N+1."**

  Then wait. Do not start the next phase until I reply "continue".
- **Within a phase,** don't stop for small decisions the plan doesn't answer. Write the question in `PROGRESS.md` under "Open questions", make the most reasonable choice, and keep going. Ask me first only if the decision is hard to undo.
- Keep the frontend's existing visual design. Add new UI, don't restyle.

## Phase 3 — Orchestrator Agent

- `backend/app/core/orchestrator.py`: **decompose** a trip request (destination, origin, dates, budget, travellers, interests) into a graph.
  - The LLM proposes tasks only from the **fixed task-type vocabulary**, using structured JSON output validated with Pydantic.
  - The Orchestrator attaches **edges and contracts from templates**. It never accepts contracts written as free text by the LLM.
  - If the LLM output is invalid, retry with the validation error included. After N failures, fall back to a deterministic template graph.
- Provider-agnostic LLM client (`app/llm/`) with a **MockLLM** (deterministic) and one live provider chosen by env.
- Uses the user profile and memory (a simple table: past trips, preferences, approval history) as context.
- Hands the graph to the existing Scheduler, which executes it with mock agents.
- FastAPI: `POST /trips`, `GET /trips/{id}/graph`, `GET /trips/{id}/itinerary` (see plan §8.1).
- Wire the **Create trip** button to `POST /trips` and show the resulting itinerary in the existing preview.
- Tests: valid decomposition, invalid LLM output with retry and fallback, graph-contract validity, end-to-end API test.

## Phase 4 — Real agents (behind mock mode)

- Flight Agent uses the **Duffel API in test mode** (sandbox "Duffel Airways"). Amadeus Self-Service was shut down in July 2026, so don't use it.
- Hotel Agent uses a places or listings API (Geoapify or OpenTripMap) for search, plus **our own mock booking provider**.
- Itinerary and Transfer use OpenTripMap / Geoapify for places, OpenRouteService or OSRM for travel times, and the LLM for day-plan text.
- Weather uses Open-Meteo (no key needed). Budget uses Frankfurter for currency conversion.
- Every agent **validates API output** before writing to the graph (schema, price > 0, arrival after departure, and so on).
- Record a few real responses as fixtures for mock mode and tests.

## Phase 5 — Disruption Monitor and partial re-planning (the core novelty)

- `app/core/monitor/`: a `DisruptionEvent` model, a **Disruption Simulator** (scripted, seeded events: delay of X hours, cancellation, hotel overbooked, storm on day N, price spike), optional live polling, and deduplication.
- In the Orchestrator, implement **`PROPAGATE`** (plan §6.2). Starting from the changed node, mark a child STALE **only if its edge contract breaks**, and stop where contracts still hold. Then re-schedule only the STALE nodes, passing untouched neighbours as **frozen context**.
- Implement the baselines **B1 (full replan)** and **B2 (naive: all descendants stale)** behind the same interface.
- API: `POST /trips/{id}/disruptions`.
- Key test, matching the Santorini example in plan §5.3: a 5-hour outbound flight delay regenerates the airport transfer, day 1 and the budget check, while **the hotel stays untouched**.

## Phase 6 — Risk Gate and human-in-the-loop

Keep it rule-based and simple:
- **T0** (search, compare, draft) runs automatically.
- **T1** (small changes such as rescheduling a pending transfer) runs, then notifies the user with an undo option.
- **T2** (book, cancel, change a flight, pay, or anything above a spend threshold) **pauses** with status AWAITING_APPROVAL.
- Payment and cancellation are always T2. A T2 action never executes without approval, even after a timeout.
- API: `GET /approvals`, `POST /approvals/{id}`. Add WebSocket `/ws/trips/{id}` (or SSE) for live node status, approvals and replans.
- Test: **unsafe-action rate = 0**, meaning no T2 action can execute without approval under any test path.

## Phase 7 — UI integration (the demo)

- A graph view built with React Flow: grey = done, amber = stale, green = regenerated, red = disrupted node. Edge tooltips show the contract.
- A **"Simulate disruption"** panel with preset scenarios.
- **Approval cards or modal** showing the option, the price difference, what changes and why.
- T1 notification toasts with undo.
- A metrics panel showing this replan's precision, recall and stability against the B1 baseline.
- The full demo script in plan §10 must run from the browser in mock mode.

## Phase 8 — Evaluation Harness

- `eval/scenarios/` (YAML): about 10 trip templates × about 10 disruption types × several severities, at least 100 scenarios.
- `eval/oracle.py` computes the ground-truth affected set by re-checking every constraint with full knowledge of the new facts.
- `eval/run_benchmark.py` runs ours, B1 and B2, plus ablations: no contracts, no frozen context, no Risk Gate.
- Metrics (plan §6.6): replan precision, recall and F1, plan stability, constraint satisfaction, recovery latency, cost (LLM calls and API calls), and unsafe-action rate.
- Output: `eval/results/results.csv`, charts (PNG), and `eval/RESULTS.md` with the results table and a short analysis.

## Finish

1. Run the whole suite. Make sure `npm run build` passes and the demo script works end to end in mock mode.
2. Update `README.md`: setup on Windows, how to run the backend and frontend, mock vs live mode, how to run the demo and the benchmark.
3. Update `PROGRESS.md` with the final state, known limitations and open questions.
4. Report back with what was built in each phase, test counts, and **the real benchmark numbers**. They will fill the resume bullets in plan §13. Never invent numbers.
5. Ask me to make the final commit. Don't run any git commands yourself.
