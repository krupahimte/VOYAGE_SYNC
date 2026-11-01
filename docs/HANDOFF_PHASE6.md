# VoyageSync — hand-off for a new chat (start at Phase 6)

> **For the user:** commit Phase 5 first. Then open a new Claude Code chat in the repo root (`voyageesync/`) and paste:
> *"Read docs/HANDOFF_PHASE6.md, then docs/CLAUDE_CODE_PROMPT.md and PROGRESS.md. Phases 0–5 are done. Start Phase 6 and follow the prompt's rules exactly."*

---

## 1. What this project is

VoyageSync is a final-year IPD project at DJSCE (team: Ashita Patel, Krupa Himte, Mahima Acharekar, Risha Deshmukh; guide: Prof. Nancy Nadar). It is a **dependency-aware, risk-tiered multi-agent travel planner**:
- a trip is a **typed task graph** whose edges carry **contracts**;
- after a disruption, only the nodes whose contracts break are regenerated (**partial re-planning**);
- a **Risk Gate** decides what runs automatically and what needs approval;
- an **evaluation harness** measures how precisely the system re-planned.

**Read these files; they are the instructions and the spec:**
- `docs/CLAUDE_CODE_PROMPT.md`: the complete instructions, phase list and ground rules. **Follow it exactly.**
- `docs/VoyageSync_Project_Plan.md`: the spec. §5.3 is the example graph, §6 the component design (§6.4 Risk Gate, §6.6 metrics), §8.1 the API, §10 the demo script.
- `docs/voyagesync_architecture.png`: the architecture slide.
- `PROGRESS.md`: what was built in each phase, the refactors log and the **open questions**. Keep it updated.

## 2. Ground rules (summary of the prompt; the prompt wins on any conflict)

- **No git commands that change the repo.** Read-only `git status/diff/log` are fine; the user makes every commit.
- **Stop after every phase** and post the phase report:
  1. what was built
  2. files
  3. test counts
  4. how to check it
  5. a suggested commit message
  6. the line "**Please review and commit Phase N, then reply 'continue' to start Phase N+1.**"

  Then wait for "continue".
- **Reuse, don't rewrite.** Log every refactor in `PROGRESS.md`.
- **Mock mode is the default.** The whole suite and the demo run with no keys and no network.
- The orchestrator, re-planner, monitor, **Risk Gate** and evaluation harness are **built by us**: no LangGraph, CrewAI or AutoGen.
- **Keep the frontend's visual design.** Add new UI; don't restyle.
- Small decisions the plan doesn't settle go under "Open questions" in `PROGRESS.md` with the choice made, and work continues.
- **Never invent numbers.** The Phase 8 results must be real.

## 3. Environment (Windows)

- Python **3.14** only; venv at `backend/.venv`. Run tests from `backend/`:
  `.venv\Scripts\python.exe -m pytest tests/ -q`
- Pins in `backend/requirements.txt`: fastapi 0.143.2, pydantic 2.14.1, sqlmodel 0.0.48, pytest 9.1.1, pytest-asyncio 1.4.0 (`asyncio_mode = auto`), httpx 0.28.1.
- Frontend: React 19, Vite 8, TypeScript, Tailwind 4; `reactflow@11` is already a dependency; `node_modules` is installed. `npm run build` and `npm run lint` (oxlint) both pass.
- `.claude/launch.json` defines two preview servers, `backend` (port 8000) and `frontend` (port 5173).
- **Tooling gotchas seen in this environment:**
  - The Bash tool (Git Bash) breaks on heredocs that contain backticks or unmatched apostrophes. Write a patch script to the scratchpad and run that instead.
  - System `python` writes files in **cp1252** by default. Always use `encoding="utf-8"` (and `newline=""`), or run with `.venv\Scripts\python.exe -X utf8`.
  - The PowerShell tool cannot reach `localhost` (`Invoke-RestMethod` dies silently). Use curl from Bash for local HTTP checks.
  - The repo stores LF line endings and `core.autocrlf=true`. Mixed endings in the working copy are harmless.

## 4. State at hand-off

- Branch `phase-3-orchestrator`. Last commit `4648193 phase 4`. **Phase 5 is complete but uncommitted** (the user will commit it).
- **253 tests pass, 0 fail** (35 pre-existing deprecation warnings).
- `tests/conftest.py`:
  - forces `VOYAGESYNC_MODE=mock` and clears all API keys;
  - **blocks all real HTTP**, so tests must use `httpx.MockTransport`.

| Phase | Status | Main pieces |
|---|---|---|
| 0–2 | done (team) | `GraphStore`, contracts, async wave `Scheduler`, `BaseAgent`/`AgentRegistry`, Phase 2 mocks |
| 3 Orchestrator | ✅ | LLM decomposition with validate → retry with error → template fallback; template edges and contracts; user memory; `/trips` API; UI wired |
| 4 Real agents | ✅ | Duffel, Geoapify, OSRM, Open-Meteo and Frankfurter behind mock mode; output validation; recorded fixtures |
| 5 Monitor + re-plan | ✅ | events, simulator, dedup, PROPAGATE with frozen context, B1/B2 baselines, `/disruptions` API |
| **6 Risk Gate + HITL** | **next** | see §7 |
| 7 UI (React Flow demo) | todo | |
| 8 Evaluation harness | todo | |

## 5. Code map (backend/app)

**Core**
- `core/models.py`
  - `NodeType`: FLIGHT, STAY, TRANSFER, DAY_PLAN, WEATHER, BUDGET.
  - `NodeStatus`: PENDING, READY, RUNNING, DONE, STALE, AWAITING_APPROVAL, FAILED.
  - `RiskTier`: T0/T1/T2. **Its docstring comments are wrong** (they say blocking/degradable/optional); redefine them in Phase 6.
  - `TaskNode{id, type, agent, inputs, output, status, version, risk_tier}`; `Edge{source_id, destination_id, contract, contract_config, description}`.
- `core/graph.py`: `GraphStore` (add/get/list, `update_node_status`, which bumps the version and appends history, `set_node_inputs`, `validate` → topological order, `get_history`).
- `core/contracts.py`
  - 11 templates.
  - `check_edge(contract, config, src_id, src.output, dst.output)` builds the contract config from node outputs through *binders*.
  - `evaluate_contract(name, config)` evaluates a contract from a plain config.
  - `STORM_CONDITIONS`.
- `core/templates.py`: canonical node IDs and inputs, plus `template_edges()`. A 5-night trip has **16 nodes and 27 edges**:
  - nodes: `flight_outbound`, `flight_return`, `hotel_stay`, `airport_transfer_in`, `airport_transfer_out`, `weather_day_N`, `day_N_plan`, `budget_check`;
  - each cost node → `budget_check` with `BUDGET_ITEM_CURRENT`;
  - hotel → transfers with `TRANSFER_SERVES_HOTEL`.
- `core/scheduler.py`: runs only PENDING nodes. `run(store, context=..., node_context={nid: {...}})`. Agents receive `resolved_inputs` (own inputs + flat parent outputs + `_parents[pid] = {type, output}`) and `frozen_context`.
- `core/orchestrator.py`
  - `Orchestrator(llm=, registry=)`.
  - `plan_trip(goal, user_context)` returns `OrchestratorResult` (`graph_store`, `contract_checks`, `decomposition`, `usage`).
  - `handle_disruption(store, event, strategy=, use_frozen_context=)` returns `MonitorOutcome`.
  - `check_graph_contracts(store)`.
  - The default registry is `build_agent_registry()`: the real agents, sharing the orchestrator's LLM.
- `core/replanner.py`
  - `Replanner(registry).replan(store, target, trigger "facts"|"invalidate", strategy contract|full|naive, use_frozen_context)` returns `ReplanResult{stale, regenerated, changed, failed, stale_reasons, contract_checks, levels, latency_ms, llm_calls, api_calls, contracts_total, contracts_holding}`.
  - It processes nodes level by level and regenerates through the Scheduler.
  - Frozen context per node is `{"replan": True, "previous_output", "frozen_neighbours"}`.
- `core/monitor/`
  - `events.py`: `DisruptionEvent`, `DisruptionType`.
  - `simulator.py`: `DisruptionSimulator(seed)`, `PRESETS`, `random_event`.
  - `apply.py`: `apply_event` → facts / invalidate / duplicate / invalid, plus the impact pre-check.
  - `monitor.py`: `DisruptionMonitor.handle` and `poll_weather`.

**Agents and providers**
- `agents/`
  - `flight.py`, `hotel.py`, `itinerary.py` (DayPlanAgent and TransferAgent; LLM descriptions), `weather.py`, `budget.py`.
  - `validation.py`: `finish(type, output)` validates the output before it reaches the graph.
  - `providers.py`: `build_providers(settings, llm=, client=)` picks mock or live per provider, with a `FallbackProvider`.
  - `registry.py`; `common.py`.
  - `mocks.py`: the simple Phase 2 mocks plus `build_default_registry()`, used by unit tests. They do **not** support re-planning features.
- `integrations/`: `http.py` (`HttpJson`, `ProviderError`, `UsageMeter`), `flights.py` (Duffel), `places.py` (Geoapify), `geocoding.py`, `weather.py`, `fx.py` (Frankfurter), `routing.py` (OSRM), `booking.py` (**`MockBookingProvider.quote()` only; `book()` is still to be written, in Phase 6**), plus `fixtures/` and `scripts/record_fixtures.py`.
- `llm/`: `client.py` (`MockLLM`, `OpenAICompatibleLLM`, `get_llm_client`, `extract_json_object`) and `prompts.py`.

**Persistence and API**
- `db/`
  - `models.py`: `graphs`, `graph_nodes`, `node_history`, `graph_edges`, `user_profiles`, `trips`, **`approval_history`** (`ApprovalRecord`: already defined, never written yet), `disruptions`.
  - `graph_repo.save_graph(store, session)` upserts the graph and appends only new history.
  - `memory_repo.get_user_context()` already counts `approval_history` approved/rejected rows into the LLM prompt.
  - `disruption_repo.py`.
- `api/trips.py`
  - `POST /trips`
  - `GET /trips/{id}/graph` (edges include live `holds`/`explanation`)
  - `GET /trips/{id}/itinerary`
  - `POST`/`GET /trips/{id}/disruptions`
  - `POST /trips/{id}/monitor/poll`
  - `GET /disruptions/presets`
  - `get_orchestrator` is a FastAPI dependency (tests override it).
- `config.py`: `VOYAGESYNC_MODE`, `VOYAGESYNC_LLM_*`, `DUFFEL_ACCESS_TOKEN`, `GEOAPIFY_API_KEY`.

**Tests:** `test_graph`, `test_scheduler`, `test_health`, `test_orchestrator`, `test_trips_api`, `test_integrations`, `test_real_agents`, `test_replanning`, `test_disruptions_api`.
- API tests use a named shared-cache in-memory SQLite engine patched into `app.db.database.engine` (`SingletonThreadPool`).
- The shared fixture is the Santorini goal: Mumbai → Santorini, 2027-06-14 to 2027-06-19, 1 traveller, 4000 USD, luxury, interests relaxation and food.

**Frontend:**
- `TripGoalForm` → `TripPlanner` calls `createTrip`, then `getItinerary` (in `src/lib/tripsApi.ts`).
- `AdaptivePlanPreview` renders `ItineraryPreview` once a trip exists.
- Types live in `src/types/trip.ts`.

## 6. Facts the next phases depend on

- **Mock timeline (keep it):**
  - outbound flight departs 08:00, arrives 14:00;
  - transfer pickup at arrival + 30 min;
  - day 1 = sunset 18:00 + dinner 20:00;
  - hotel check-in cutoff 23:59;
  - return flight departs 16:00;
  - departure transfer drops off 3 h before departure.
- A 5-hour delay regenerates exactly `airport_transfer_in`, `day_1_plan` and `budget_check`. A 10-minute delay has no effect. **Don't break these** (tests cover them).
- **Mock outbound offers:**

  | Departs | Arrives | Price |
  |---|---|---|
  | 08:00 | 14:00 | cheapest |
  | 11:30 | 17:30 | × 1.12 |
  | 06:00 | 14:00 | × 1.25 |

  On cancellation with frozen context the agent picks the 06:00 offer (same arrival); without frozen context it picks the cheapest, 11:30.
- **Agent outputs already carry the data the Risk Gate needs:**
  - flight: `offer_id`, `cost`, `currency`, `status` (`offered`/`delayed`/`cancelled`);
  - hotel: `offer_id` (quote id), `refundable`, `cost`;
  - transfer: `status: "planned"`.

  Nothing is ever *booked* yet.
- **Disruption facts** are pinned in `node.inputs["facts"]`; exclusions are in `inputs["exclude_offer_ids"]` and `inputs["exclude_hotel_ids"]`.

## 7. Phase 6 — Risk Gate and human-in-the-loop (what to build)

From the prompt, using the plan's rules from §6.4:
- **T0** (search, compare, draft) runs automatically.
- **T1** (small changes such as rescheduling a pending transfer) runs, then notifies the user with an **undo** option.
- **T2** (book, cancel, change a flight, pay, any spend above a threshold, non-refundable items) **pauses** with status `AWAITING_APPROVAL`.
- Payment and cancellation are always T2. Money is never spent in T0 or T1. **A T2 action never executes without approval, even after a timeout**; the offer is held and the user reminded.
- **Optional score** from §6.4 (`w1·irreversibility + w2·normalised_cost + w3·user_visibility + w4·(1−confidence)`). The hard rules above override it.
- **API:** `GET /approvals`, `POST /approvals/{id}` (approve / reject), and a WebSocket `/ws/trips/{id}` (or SSE) streaming node status, approvals and replans.
- **Test:** **unsafe-action rate = 0**. No T2 action can execute without approval under any test path: timeouts, retries, replans, B1/B2, direct API calls, a rejected approval, a double approval.

**Suggested design** (a suggestion, not a mandate; reuse what exists):
- `app/core/risk_gate.py`
  - `Action{action_id, trip_id, node_id, kind: book|cancel|change|pay|reschedule|notify, amount, currency, refundable, details}`.
  - `classify(action) -> tier` and `score(...)`.
  - A gate that executes T0, executes and records T1 with undo data, and stores T2 as PENDING approvals while setting the node to `AWAITING_APPROVAL`.
- **Persistence:** a pending-approvals table plus a T1-notifications table with undo payloads. Write every approve/reject decision to the existing `ApprovalRecord` table (memory).
- **Booking through the gate:** add `MockBookingProvider.book(quote)` and a mock flight `book(offer)`, or a Duffel order call in live test mode. These must only be callable from the gate after approval; one way is to require an approval token.
- **Where the gate hooks in:**
  - After `plan_trip`, propose booking actions (flight × 2, hotel) as **T2**, as in demo step 2.
  - After a re-plan, a regenerated **flight** after a cancellation is a T2 *change* and needs approval; show the price difference.
  - A regenerated **pending transfer** (delay scenario) is **T1** ("Rebooked pickup for 19:30 (T1, undo)", demo step 4).
  - Day-plan and budget changes are T0.
- **Approval card data** (for Phase 7): option, old price → new price and the difference, what changes, why (the event summary).
- **Keep the existing flow intact.** Replanning and contract checks must keep working; only actions with consequences pass through the gate. "Proposed" outputs can still be written to the graph as T0 drafts.

Then Phase 7 (React Flow graph, disruption panel, approval cards, toasts, metrics panel) and Phase 8 (≥ 100 YAML scenarios, oracle, ours vs B1/B2 + ablations, CSV, charts, `eval/RESULTS.md`), as in the prompt.

## 8. Open questions already logged (see PROGRESS.md for details)

1. One city per trip (one STAY).
2. Naive local times everywhere.
3. One weather node per day.
4. `.claude/launch.json`: commit or ignore.
5. 35 pre-existing deprecation warnings.
6. **Duffel and Geoapify live clients are unverified against the real servers**: no keys were available, so they were tested only against hand-written sample fixtures.
7. Weather more than 15 days out uses the same dates from last year (archive proxy).
8. OSRM demo server is fair-use only.
9. Activity prices are per-category estimates.
10. **B1 looks too stable with deterministic mocks.** Phase 8 should add seeded price and availability drift for a fair stability comparison.
11. **Cancellations currently rebook without approval. Phase 6 must gate this.**
12. Live polling is on demand (an endpoint) and covers weather only.
