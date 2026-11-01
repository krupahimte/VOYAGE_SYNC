# VoyageSync — Progress

Spec: `docs/VoyageSync_Project_Plan.md` + `docs/voyagesync_architecture.png`.
Instructions: `docs/CLAUDE_CODE_PROMPT.md`.

> Phase numbering follows `CLAUDE_CODE_PROMPT.md` (Phase 3 = Orchestrator … Phase 8 = Evaluation).
> Plan §9 numbers them one lower (its "Phase 2" is the Orchestrator).

---

## Final state (2026-10-16)

All phases are done: **337 tests pass, 0 fail** (35 pre-existing deprecation warnings), and `npm run build` and oxlint
pass. The full demo script (plan §10) runs in the browser in mock mode. The benchmark covers 160 scenarios with
real numbers in `eval/RESULTS.md`. Known limitations are listed under "Open questions" (notably 6, 18, 22–25).

---

## Step 0 — State of the repo (2026-10-16)

### Environment
- Only Python **3.14.7** is installed; there was no `backend/.venv`.
- `requirements.txt` pins `pydantic==2.10.3`, which has no Python 3.14 wheel (pydantic-core build fails).
- Created `backend/.venv` (gitignored) with current compatible versions:
  fastapi 0.143.2, pydantic 2.14.1, pydantic-settings 2.16.0, sqlmodel 0.0.48,
  pytest 9.1.1, pytest-asyncio 1.4.0, httpx 0.28.1, uvicorn 0.54.0.
  `requirements.txt` is **not yet changed** (to be updated in Phase 3).
- `frontend/node_modules` is not installed yet.

### Test baseline
`.venv\Scripts\python.exe -m pytest tests/ -v` → **109 passed, 0 failed** (35 deprecation warnings).

### What exists (verified)
| Area | File | Notes |
|---|---|---|
| Models | `backend/app/core/models.py` | `NodeType` (6 types), `NodeStatus` (7), `RiskTier`, `TaskNode`, `Edge` (`source_id`/`destination_id`, `contract` name + `contract_config` dict), `NodeHistoryEntry` |
| Graph | `backend/app/core/graph.py` | `GraphStore`: add/get/list nodes & edges, `update_node_status` (bumps version, records history), cycle/dup/self-edge checks, Kahn topo sort, `get_history`, `to_dict` |
| Contracts | `backend/app/core/contracts.py` | 4 templates: `FLIGHT_BEFORE_CHECKIN`, `TRANSFER_AFTER_FLIGHT`, `DAY_PLAN_FEASIBILITY`, `BUDGET_WITHIN_LIMIT`; `evaluate_contract(name, config)` |
| Scheduler | `backend/app/core/scheduler.py` | async waves, semaphore, per-node lock, retries, failure → dependants SKIPPED, `ExecutionEvent` log; runs only PENDING nodes |
| Agents | `backend/app/agents/base.py`, `mocks.py` | `BaseAgent.run(node, resolved_inputs, frozen_context)`, `AgentResult.ok/.fail`, `AgentRegistry` (by name, then by type); 6 mock agents + `FailingAgent`, `SlowAgent`; `build_default_registry()` |
| Persistence | `backend/app/db/*` | SQLite via SQLModel; graph/node/history/edge tables; `graph_repo` load/persist |
| API | `backend/app/api/health.py`, `graphs.py` | `/health`, `/graphs` CRUD + validate + history |
| Frontend | `frontend/src/...` | React 19 + Vite 8 + TS + Tailwind 4; `reactflow@11` already in `package.json`. Create-trip button shows "planning engine is under development" (`TripGoalForm.tsx:572`) |

### Phase 3 work already started (commit `e869b25 phase 3 partial`)
- `backend/app/core/orchestrator.py`: `build_graph_from_plan` (ref/dup/self-dep/cycle checks, contracts attached from a type-pair rule table) and `orchestrate_trip` (decompose → build → `Scheduler.run`).
- `backend/app/llm/client.py`: deterministic mock plan + live OpenAI-compatible call (env `VOYAGESYNC_LLM_MOCK`, `VOYAGESYNC_LLM_API_KEY`, `..._MODEL`, `..._BASE_URL`).
- `backend/app/llm/prompts.py`: system prompt with fixed task vocabulary, user prompt builder.
- `backend/app/schemas/trip.py`: `TripGoal`, `ProposedTask`/`ProposedPlan`, response models for `/trips`.

**Missing from Phase 3:** retry-with-validation-error + deterministic template fallback; user profile/memory table; `/trips` routes (not registered in `main.py`); trip persistence; frontend wiring; any orchestrator/LLM tests; LLM settings in `config.py` (client reads `os.environ` directly, so `.env` values are not picked up).

### Differences from the plan / issues found
1. **Contracts are not evaluated from node outputs.** `evaluate_contract` takes a static `contract_config`; the orchestrator attaches edges with an empty config, so every contract would fail as "Invalid input". PROPAGATE (§6.2) needs `holds(src.output, dst.output)` → a binding layer that builds the config from outputs.
2. **Wrong default contract.** Edges with no rule get `BUDGET_WITHIN_LIMIT` labelled "Dependency link". Missing templates for: STAY→DAY_PLAN (hotel location), TRANSFER→DAY_PLAN (reach hotel before day-1 plan), WEATHER→DAY_PLAN (no outdoor items in storms), FLIGHT(return)→TRANSFER_OUT (pickup = departure − 3h), *→BUDGET.
3. **Mock template graph ≠ §5.3.** No `airport_transfer_out`; weather nodes have no edges; transfer doesn't feed day 1; day plans only depend on the hotel. Needs reshaping for the Santorini key test (Phase 5).
4. **`RiskTier` semantics differ.** Code comments say T0 blocking / T1 degradable / T2 optional; plan says T0 autonomous / T1 notify / T2 approve. Unused in logic so far — will be redefined in Phase 6.
5. **Mock agents ignore most inputs**: hard-coded Tokyo/VS901/GBP, fixed 21:15Z arrival, no `checkin_cutoff` on stays, transfer pickup = arrival (0 min gap), budget `spent` hard-coded rather than summed from parents. Orchestrator passes `total_budget_gbp = goal.budget` regardless of currency.
6. **Scheduler input merge is flat**: all parent outputs merged into one dict, so same-named keys collide (e.g. two flights' `arrival_time` into budget). Needs per-parent namespacing for budget/frozen context.
7. **Agent interface**: `frozen_context` currently means run context (graph_id/trip_id), not "untouched neighbours" (§6.2.4). No `execute_action` yet (needed for Risk Gate).
8. **Mode switch**: `MOCK_MODE` in config and `VOYAGESYNC_LLM_MOCK` env both exist; prompt asks for `VOYAGESYNC_MODE=mock|live`.
9. Docs nits: README says `Copy-Item ..\env.example` (should be `..\.env.example`); plan references `VoyageSync_Architecture.png` but the file is `voyagesync_architecture.png` (case matters on GitHub).

### Agent mapping (architecture slide → code)
| Slide agent | Node types / mock agents |
|---|---|
| Flight Agent | `FLIGHT` → `MockFlightAgent` |
| Hotel Agent | `STAY` → `MockStayAgent` |
| Itinerary Agent | `DAY_PLAN`, `TRANSFER` → `MockDayPlanAgent`, `MockTransferAgent` |
| Budget Agent | `BUDGET` → `MockBudgetAgent` |
| (data source for Monitor + Itinerary) | `WEATHER` → `MockWeatherAgent` |

---

## Phase 3 — Orchestrator ✅ (2026-10-16)

**Tests:** 158 passed, 0 failed (109 existing + 40 `test_orchestrator.py` + 9 `test_trips_api.py`). `npm run build` and `npm run lint` pass.

### What was built
- **Mode switch** `VOYAGESYNC_MODE=mock|live` (default mock) plus LLM settings in `app/config.py`; `.env` is read from the repo root or `backend/`. `tests/conftest.py` forces mock mode so the suite never needs keys or network.
- **`app/llm/client.py`**: `LLMClient` interface with usage counters (calls and tokens, for the Phase 8 cost metric); deterministic `MockLLM` (scripted replies or the template planner); `OpenAICompatibleLLM` as the single live provider. With live mode and no key it logs a warning and uses `MockLLM`.
- **Decomposition** (`app/core/orchestrator.py`): the LLM returns JSON `{tasks:[{task_id, task_type, direction, day, theme}]}` using only the fixed vocabulary. It is parsed, validated with Pydantic (`ProposedPlan`), then checked semantically (`validate_proposal`: exactly one outbound and one return flight, one stay, one budget, days within range, no duplicate roles). An invalid reply is retried with the validator error added to the conversation. After `VOYAGESYNC_LLM_MAX_ATTEMPTS` failures it falls back to the deterministic template proposal.
- **Templates** (`app/core/templates.py`): stable node IDs (`flight_outbound`, `airport_transfer_in`, `day_1_plan`, `weather_day_1`, `budget_check`, …). Node inputs come from the goal, and edges and contracts come from templates following plan §5.3. Fields the LLM writes such as `depends_on`, `contract` or `agent` are ignored.
- **Contracts bound to outputs** (`app/core/contracts.py`): `check_edge(contract, config, src_id, src.output, dst.output)` implements `holds(src.output, dst.output)` from plan §6.1. 6 new templates: `TRANSFER_BEFORE_DAY_START`, `STAY_COVERS_DAY`, `WEATHER_SUITS_PLAN`, `DAY_ENDS_BEFORE_PICKUP`, `TRANSFER_BEFORE_DEPARTURE`, `BUDGET_ITEM_CURRENT`. `TRANSFER_AFTER_FLIGHT` gained an optional `max_gap_minutes`.
- **Mock agents** now compute their outputs from inputs and parent outputs and carry the fields that contracts read. Transfers derive their pickup from the flight, day plans start after the transfer drop-off and avoid outdoor items in storms, and the budget sums its parents' `cost` into `line_items`. They are still deterministic (stable hash) and have fixed mock FX rates (`app/agents/fx.py`).
- **User profile & memory**: tables `user_profiles`, `trips` and `approval_history` (`app/db/models.py`) and `app/db/memory_repo.py`. Preferences, the last 5 trips and the approval counts go into the LLM prompt. Profile interests, pace and accommodation fill gaps in the goal, and preferences are learned from each new trip.
- **API** (`app/api/trips.py`):
  - `POST /trips` (201): decompose, build, execute, persist the graph snapshot, record the trip.
  - `GET /trips/{id}/graph`: nodes in topological order, edges with a live contract check (`holds`, `explanation`).
  - `GET /trips/{id}/itinerary`: flights, stay, transfers, day-by-day plan with weather, budget.

  `/health` now reports `mode`.
- **Frontend**:
  - The Create trip button calls `POST /trips`, then fetches the itinerary.
  - `AdaptivePlanPreview` renders the itinerary (new `ItineraryPreview.tsx`) in the existing card style.
  - The form shows loading, success and error states.
  - The engine badge and status list read `/health`.
  - The upcoming-trip card shows "Planned".
- **Santorini check** (Mumbai → Santorini, 14–19 Jun, $4,000, luxury, 1 traveller): 16 nodes, 25 edges, all contracts hold, total within budget.

### Graph template (5-night trip → 16 nodes, 25 edges)
| Edge | Contract | Knobs |
|---|---|---|
| flight_outbound → hotel_stay | FLIGHT_BEFORE_CHECKIN | arrival + 90 min ≤ 23:59 cutoff |
| flight_outbound → airport_transfer_in | TRANSFER_AFTER_FLIGHT | pickup 15–90 min after arrival (Phase 5: was 30) |
| airport_transfer_in → day_1_plan | TRANSFER_BEFORE_DAY_START | drop-off + 30 min ≤ first activity |
| hotel_stay → day_N_plan | STAY_COVERS_DAY | date in stay, same hotel |
| weather_day_N → day_N_plan | WEATHER_SUITS_PLAN | no outdoor items in storms |
| flight_return → airport_transfer_out | TRANSFER_BEFORE_DEPARTURE | at airport 2–4 h before |
| day_last_plan → airport_transfer_out | DAY_ENDS_BEFORE_PICKUP | 60 min buffer |
| every cost node → budget_check | BUDGET_ITEM_CURRENT | budget line item = current cost |

The mock times are chosen so that the §5.3 scenario plays out in Phase 5. The outbound flight arrives at 14:00, and a 5 h delay moves arrival to 19:00. That breaks F→T (pickup 14:30 is now before arrival) and, after the transfer is regenerated, T→D1 (drop-off 20:15 is after the 18:00 sunset). F→H still holds (20:30 ≤ 23:59). The regenerated day 1 costs less, so `budget_check` becomes stale and is regenerated too.

## Phase 4 — Real agents behind mock mode ✅ (2026-10-16)

**Tests:** 217 passed, 0 failed (158 before + 30 `test_integrations.py` + 29 `test_real_agents.py`). `tests/conftest.py` now blocks every real HTTP request, so the suite is provably offline. `npm run build` and lint pass.

### What was built
- **Providers** (`app/integrations/`). Each has a parser that validates the raw response with Pydantic and rejects bad items with a reason, a live client, and a mock that runs data in the same raw format through the same parser.

  | Need | Live | Key | Mock |
  |---|---|---|---|
  | Flights | Duffel v2 test mode (`/places/suggestions`, `/air/offer_requests`) | `DUFFEL_ACCESS_TOKEN` | Duffel-format offers built from `airports.json`; the cheapest outbound arrives 14:00 |
  | Hotels and attractions | Geoapify Places v2 | `GEOAPIFY_API_KEY` | Geoapify-format features with fictional names |
  | Booking | our own `MockBookingProvider` (quotes; booking itself is Phase 6) | — | — |
  | Geocoding | Open-Meteo geocoding | none | **recorded** responses for 22 cities |
  | Weather | Open-Meteo forecast (≤ 15 days ahead) or archive for the same dates last year | none | **recorded** archive windows (Santorini, Tokyo, Lisbon, Reykjavik, Goa), else synthetic |
  | FX | Frankfurter | none | **recorded** USD rates (2026-10-15) |
  | Routing | OSRM demo server | none | distance × 1.3 at 35 km/h |
- **Mode selection** (`app/agents/providers.py`):
  - Mock mode: all mocks.
  - Live mode: keyless providers run live. Duffel and Geoapify run live only with a key; without one, a warning is logged and the mock is used.
  - Every live provider is wrapped in a `FallbackProvider`: a failing call (network, HTTP error, bad JSON) logs a warning and uses the mock, so the demo never breaks.
- **Real agents** (`app/agents/flight.py`, `hotel.py`, `itinerary.py`, `weather.py`, `budget.py`), registered by `build_agent_registry()`. This is now the Orchestrator's default; `build_default_registry()` keeps the Phase 2 mocks for unit tests.
  - **Flight:** finds airports, searches offers, rejects wrong-date or wrong-route offers, and picks the cheapest valid offer after converting to the trip currency. Reports `offers_considered` and `offers_rejected`.
  - **Hotel:** finds the accommodation type the user asked for near the destination, gets a quote from the mock booking provider, and converts the price.
  - **Transfer:** OSRM drive time between the airport and the hotel. Pickup is arrival + 30 min, or departure − 3 h − drive time.
  - **Day plan:** places near the hotel for the day's interest, using the same time slots as the mocks (so the contract timeline is unchanged). No outdoor items in storms, and the day starts after the arrival drop-off. **One LLM call per day writes the activity descriptions**; the reply is validated (JSON, known names only, ≤ 240 characters), with template text as the fallback.
  - **Weather:** Open-Meteo condition (WMO code mapped to our conditions) and the `is_storm` flag.
  - **Budget:** sums parent costs in the trip currency and converts foreign items with Frankfurter rates.
- **Output validation** (`app/agents/validation.py`): every agent output is checked against a schema before it reaches the graph:
  - flights: arrival after departure, price > 0, IATA codes;
  - stays: dates, nights, check-in cutoff on the check-in day;
  - transfers: pickup before drop-off;
  - day plans: no overlapping activities, all on the right date, cost = sum of activities;
  - budget: totals and flag consistent.

  An invalid output becomes a non-retryable failure.
- **Ambiguous destinations:** names like "Bali" or "Goa" are geocoded using the destination airport as an anchor (the first API result within 300 km). If no result is near, the airport location is used. Without this, Open-Meteo's top result for "Bali" is a town in India and "Goa" returns Genoa.
- **New edge:** `hotel_stay → airport_transfer_in/out` with the new contract `TRANSFER_SERVES_HOTEL`, since transfers now route to the hotel's coordinates. A 5-night trip now has **27 edges**.
- **Usage metering:** `UsageMeter` counts provider calls; mocks count the same way as the cached live clients. `POST /trips` returns `usage` (LLM calls, API calls per provider, provider modes) for the Phase 8 cost metric.
- **Fixtures:** `scripts/record_fixtures.py` re-records the keyless APIs (done on 2026-10-16) and records Duffel and Geoapify when keys are present.
- **Frontend:** activity descriptions, hotel address and a "Data:" line naming the source of each part of the itinerary.

### Checks run
- **Mock mode, Santorini** (luxury, $4,000): 27/27 contracts, $3,432 within budget, recorded weather and FX.
- **Real network, live mode without Duffel/Geoapify keys:** real Open-Meteo, Frankfurter and OSRM data; 27/27 contracts hold. The OSRM transfer was 10 min / 4.1 km.
- **Live mode with every HTTP call served offline** (`TestLiveModeOffline`): all sources live, no fallbacks, 1 Frankfurter call.
- **Live outage** (every call returns 503): falls back to mock and still plans.

## Phase 5 — Disruption Monitor and partial re-planning ✅ (2026-10-16)

**Tests:** 253 passed, 0 failed (217 before + 27 `test_replanning.py` + 9 `test_disruptions_api.py`). Still fully offline.

### What was built
- **Event model** (`app/core/monitor/events.py`): `DisruptionEvent{event_id, type, target_node, new_facts, severity, source, timestamp}`. The types are `FLIGHT_DELAY`, `FLIGHT_CANCELLATION`, `HOTEL_OVERBOOKED`, `WEATHER_STORM` and `PRICE_SPIKE`. Severity is derived from the facts, and invalid facts (negative delay, missing pct) are rejected.
- **Simulator** (`simulator.py`): seeded and deterministic. It has 11 presets (`delay_10m`, `delay_2h`, `delay_5h`, `return_delay_3h`, `cancel_outbound`, `cancel_return`, `hotel_overbooked`, `storm_day_2`, `storm_day_3`, `price_spike_hotel`, `price_spike_flight`) and `random_event(store)` for benchmark sweeps.
- **Applying facts and deduplication** (`apply.py`):
  - **Facts events** (delay, storm, price spike) update the target's output in place. The facts are pinned in `inputs["facts"]` so they survive later re-plans, including a full one.
  - **Invalidating events** (cancellation, overbooking) add the offer or hotel to an exclusion list and mark the target for regeneration.
  - An event already reflected in the graph is a **duplicate** and changes nothing: the same delay again, a day that is already stormy, or a cancellation alert for an offer that has since been replaced.
  - **Impact pre-check:** if no outgoing contract breaks, the event is recorded as `no_effect` and nothing is regenerated. A 10-minute delay is an example.
- **PROPAGATE** (`app/core/replanner.py`, reached via `Orchestrator.handle_disruption`):
  - Nodes are processed level by level in topological order.
  - A child is marked STALE only when its edge contract breaks.
  - Each STALE node is regenerated, then *its* outgoing contracts are re-checked against its **new** output. Propagation stops where contracts hold.
  - Each node is regenerated at most once.
  - Regenerated agents receive **frozen context**: their own previous output plus the outputs of their untouched neighbours.
- **Baselines behind the same interface:**
  - **B1 `full`:** regenerate every node except pinned facts, with no frozen context.
  - **B2 `naive`:** mark every descendant STALE without checking contracts.
  - The ablation "no frozen context" is `use_frozen_context=False`.
- **Agents use the frozen context:**
  - **Flight rebooking** keeps the frozen hotel's check-in contract and picks the offer closest in time to the previous flight, with price as the tie-breaker. Without frozen context it picks the cheapest.
  - **Hotel replacement** picks the hotel nearest the previous one.
  - **Day plans** keep every previous activity that is still feasible and only refill the rest; the LLM is called only for new activities.
  - Exclusion lists are honoured.
- **Monitor** (`monitor.py`): handles an event end to end and measures recovery latency. Optional live polling (`poll_weather`) re-checks the Open-Meteo forecast for days within 15 days and turns a newly forecast storm into an event.
- **Persistence:** a `disruptions` table holds each event, its status, strategy and the full re-plan result (stale set, regenerated, changed, contract checks, latency, LLM and API calls). This is the trace log for Phase 8.
- **API:**
  - `POST /trips/{id}/disruptions`: a preset `scenario`, or `type` + `target_node` + `new_facts`, with `strategy` and `use_frozen_context`.
  - `GET /trips/{id}/disruptions`: the trip's disruption history.
  - `POST /trips/{id}/monitor/poll`: check live sources and handle any new event.
  - `GET /disruptions/presets`: the simulator's preset scenarios.

### Results on the Santorini trip (mock mode)
| Event | Strategy | Regenerated | Contracts after |
|---|---|---|---|
| Outbound delayed 5 h | **ours** | `airport_transfer_in`, `day_1_plan`, `budget_check` (**3 of 16**; hotel untouched) | 27/27 |
| Outbound delayed 5 h | B2 naive | 9 (every descendant, including the hotel and all day plans) | 27/27 |
| Outbound delayed 5 h | B1 full | 15 (everything except the delayed flight) | 27/27 |
| Outbound delayed 10 min | ours | none (no effect) | 27/27 |
| Outbound cancelled | ours, frozen context | flight + budget (replacement still arrives 14:00) | 27/27 |
| Outbound cancelled | ours, no frozen context | flight, transfer, day 1, budget (cheapest flight arrives 17:30) | 27/27 |
| Hotel overbooked | ours | hotel, both transfers, 5 day plans, budget (day plans keep their activities) | 27/27 |
| Storm on day 3 | ours | day 3 (now indoor only) + budget | 27/27 |
| Hotel price +25 % | ours | budget only | 27/27 |

These are single runs on one trip, to show the behaviour. The benchmark numbers come in Phase 8.

## Phase 6 — Risk Gate and human-in-the-loop ✅ (2026-10-16)

**Tests:** 312 passed, 0 failed (253 before + 41 `test_risk_gate.py` + 17 `test_approvals_api.py` + 1 new
`test_trips_api.py` case). Still fully offline; still the same 35 pre-existing warnings.

### What was built
- **Risk Gate** (`app/core/risk_gate.py`, built by us):
  - `Action{action_id, trip_id, node_id, kind, tier, score, reasons, status, title, why, option, changes,
    amount, old_amount, cost_delta, refundable, offer_id, undo, result, decision, …}`. The kinds are
    `search | compare | draft | notify | reorder | reschedule | book | change | cancel | pay`.
  - `classify(action, policy)` puts **hard rules first**: book, change, cancel and pay are always T2, and so is
    any action that spends money (money is never spent in T0/T1), above the threshold or non-refundable.
    Otherwise the §6.4 score decides: `0.4·irreversibility + 0.3·normalised_cost + 0.2·user_visibility +
    0.1·(1 − confidence)`, with T1 at ≥ 0.15 and T2 at ≥ 0.6. Rescheduling a pending transfer scores 0.21 (T1);
    day-plan and budget drafts score below 0.15 (T0). A day plan whose cost rises a lot becomes T1.
  - `ActionLedger`: the in-memory list of a trip's actions (persisted like `GraphStore`).
  - `RiskGate.propose_bookings` runs after planning (demo step 2): flight × 2 and the hotel become T2 `book`
    cards and their nodes go to **AWAITING_APPROVAL**.
  - `RiskGate.review_replan` runs after every re-plan, whichever the strategy (ours, B1 or B2):
    - **Bookable nodes are reconciled.** A pending card whose offer or price changed is **superseded** and a new
      card is raised. A booked node regenerated with the same offer keeps its booking (no new approval). A
      booked node with a new offer gets a T2 **`change`** card with old price → new price, the difference, the
      changed fields and the event as the reason.
    - **Other changed nodes** become T0 drafts or T1 notifications. A moved pickup is
      "Rebooked pickup for 19:30" (T1, undo).
  - `approve` claims the pending action, re-checks that the node still holds **the approved offer at the
    approved price**, issues a **signed single-use `ApprovalGrant`** and runs the executor. If execution fails,
    the action goes back to pending with the error, nothing is booked and nothing retries on its own.
  - `reject` keeps the draft and marks it `booking_status: rejected`. `undo` (T1 only) restores the previous
    output, and refuses if the node changed since. `sweep` handles timeouts: it reminds and keeps holding,
    and **never** executes or rejects.
  - `unsafe_actions` / `unsafe_action_rate` audit provider executions against approved actions (Phase 8 metric).
- **Sandbox booking** (`app/integrations/booking.py`): `book_flight`, `book_hotel` and `cancel_booking` on our
  `MockBookingProvider`. Each needs a valid grant (HMAC-signed, offer-bound, single use), otherwise it raises
  `UnapprovedActionError`. Every execution is logged in `executions`.
  `app/agents/actions.py` (`ActionExecutor`, plan §6.5 `execute_action`) books, and for a change books the new
  offer and then cancels the old booking.
- **Orchestrator:** `plan_trip(..., ledger=)` and `handle_disruption(..., ledger=)` call the gate when a ledger
  is passed (the API always passes one). `approve_action` / `reject_action` / `executor()` are new.
  `MonitorOutcome.actions` carries the gate's actions.
- **Persistence:** new table `gate_actions` (`GateActionRecord`; status column authoritative) and
  `app/db/approval_repo.py` (`load_ledger`, `save_ledger`, `list_actions`, `record_decision`). `claim` is a
  conditional `UPDATE … WHERE status='pending'`, so a double approval can't both run. Every decision is written
  to `approval_history`, which the Orchestrator already reads into the user's memory.
- **API:**
  - `GET /approvals?trip_id=&status=` (default pending; overdue cards get a reminder);
  - `POST /approvals/{id}` `{decision: approve|reject}`: 409 if not pending, already decided or superseded;
    502 if the booking failed (still pending);
  - `GET /trips/{id}/actions`;
  - `POST /actions/{id}/undo`;
  - `POST /trips` accepts `propose_bookings` (default true) and returns `actions`; disruption responses
    return `actions`.
- **WebSocket** `/ws/trips/{id}` (`app/api/live.py`): a `snapshot` on connect, then `trip_planned`, `replan`,
  `node_status`, `approval_requested`, `approval_decided`, `approval_reminder`, `notification` (T1) and `undo`.
  "ping" gets a "pong". The in-process `EventBus` delivers across threads and event loops.

### Unsafe-action rate = 0: paths covered by tests
Timeouts (5 sweeps), a failed booking then re-approval, no executor, double and concurrent approvals, reject then
approve, approving a superseded card (cancellation and price spike), a price change before approval, a
cancellation before and after approval under **each strategy (ours, B1, B2)**, a B1 full replan of booked nodes,
direct provider calls without a grant or with a forged, reused or wrong-offer grant, and HTTP double, unknown and
invalid requests. Every API test ends with an audit of the sandbox execution log against the approved actions.

### Demo behaviour (Santorini, mock mode)
| Step | Result |
|---|---|
| Plan | 3 T2 cards: VY280 08:00–14:00 $560, ZZ190 16:00–22:00 $310, hotel 5 nights $1,908; nothing booked |
| Approve | sandbox booking ref (e.g. `BK…`), node DONE, 27/27 contracts |
| Delay 5 h | same 3 regenerated nodes; T1 "Rebooked pickup for 19:30" (undo), T0 day 1 + budget; no T2 |
| Cancel outbound (after approval) | T2 change to ZZ354 06:00–14:00, $560 → $700 (+$140); nothing re-booked until approved |

## Phase 7 — UI integration (the demo) ✅ (2026-10-16)

**Tests:** 323 passed, 0 failed (312 before + 9 `test_metrics.py` + 2 new `test_disruptions_api.py` cases).
`npm run build` and oxlint pass with no warnings. The whole demo script (plan §10) was run in the browser in mock
mode (see below).

### What was built
- **Backend: per-replan metrics** (`app/core/metrics.py`, for demo step 6):
  - **Oracle** for the true affected set A: on a copy of the pre-event graph, apply the event, then re-check
    **every** contract in the graph with full knowledge of the new facts. The shallowest broken nodes are
    regenerated and the check repeats until every contract holds.
  - Replan precision, recall and F1 (§6.6).
  - **Plan stability:** the share of user-visible items (flights, stay, transfers, day plans) left unchanged. The
    disrupted node and the Risk Gate's booking bookkeeping are left out.
  - `compare_with_baseline` scores this replan and a **B1 full replan run on a deep copy**, so it never touches
    the trip, its bookings or its ledger.
  - `POST /trips/{id}/disruptions` takes `compare_baseline: true` (default off) and returns `metrics`. The
    metrics are also stored in the disruption trace.
- **Frontend** (existing visual design and tokens; new components only):
  - `TripGraph.tsx`, a **React Flow** graph: columns follow dependency depth and rows are ordered to reduce
    crossings. When a trip opens, the graph "builds live" column by column in execution order.
    - Colours: grey = done, red = disrupted, amber = stale, green = regenerated, and a dashed blue outline for
      awaiting approval.
    - Hovering an edge shows its **contract**, its description and the live check (`✓ holds` / `✗ broken` with
      the explanation). Broken edges are red and dashed.
    - Clicking a node shows its status, version and output.
  - `DisruptionPanel.tsx`: the preset scenarios from the simulator, a strategy switch (ours / B2 / B1) and
    "measure against the oracle and B1". In demo step 4 the stale nodes turn amber first, then the regenerated
    ones turn green.
  - `ApprovalCards.tsx`: T2 cards with the option, old → new price and the difference, what changes, why,
    reminders, and Approve / Reject / Approve all.
  - `Toasts.tsx`: T1 notifications with **Undo** ("Rebooked pickup for 19:30 (T1)"), booking confirmations and
    warnings. Undo is also in the Risk Gate log, because toasts disappear.
  - `MetricsPanel.tsx`: this replan vs B1, covering tasks regenerated, precision, recall, F1, stability,
    constraints, latency and LLM/API calls, plus the oracle set.
  - `TripWorkspace.tsx` connects it all and stays in sync over **WS `/ws/trips/{id}`** (`useTripLive`, which
    reconnects on its own). The itinerary shows booking badges (Awaiting approval / Booked · ref / delayed).
  - The last trip is restored after a page reload (`localStorage`).

### Demo script run in the browser (mock mode, Santorini)
| Step | Seen |
|---|---|
| 1 Plan | "16 tasks, 27/27 constraints"; the graph builds column by column; 3 approval cards |
| 2 Approve | Approve all → 3 "Booked BK…" toasts; nodes go from dashed-blue to done; itinerary badges show the refs |
| 3–4 Delay 5 h | flight red; transfer, day 1 and budget amber, then green; hotel and days 2–5 grey; toast "Rebooked pickup for 19:30 (T1)" with Undo |
| Undo | pickup back to 14:30; toast warns that `flight_outbound → airport_transfer_in` is now broken |
| 5 Cancel | card "Change to SK317 … $560 → $627 (+$67)" with the changed fields and why; nothing booked until approved |
| 6 Metrics | delay 5 h: ours 3 regenerated, P 100 %, R 100 %, F1 100 %; B1 15 regenerated, P 20 %, R 100 %, F1 33 %; stability 78 % for both; 1 LLM + 3 API calls vs 5 LLM + 18 API calls |

### Metrics on single runs (mock mode, Santorini; ours vs B1, each against the oracle)
| Event | A | Ours: regen / P / R / stability | B1: regen / P / R / stability |
|---|---|---|---|
| Delay 10 min | 0 | 0 / 1.00 / 1.00 / 1.00 | 15 / 0.00 / 1.00 / 0.89 |
| Delay 5 h | 3 | 3 / 1.00 / 1.00 / 0.78 | 15 / 0.20 / 1.00 / 0.78 |
| Outbound cancelled | 2 | 2 / 1.00 / 1.00 / 1.00 | 16 / 0.12 / 1.00 / 0.78 |
| Hotel overbooked | 9 | 9 / 1.00 / 1.00 / 0.22 | 16 / 0.56 / 1.00 / 0.22 |
| Storm on day 3 | 2 | 2 / 1.00 / 1.00 / 0.90 | 15 / 0.13 / 1.00 / 0.90 |
| Hotel price +25 % | 1 | 1 / 1.00 / 1.00 / 1.00 | 15 / 0.07 / 1.00 / 1.00 |
| Return delayed 3 h | 1 | 1 / 1.00 / 1.00 / 0.89 | 15 / 0.07 / 1.00 / 0.89 |

These are single runs to illustrate the panel, not the benchmark (Phase 8).

## Phase 8 — Evaluation harness ✅ (2026-10-16)

**Tests:** 337 passed, 0 failed (323 before + 12 `test_eval.py` + 2 empty-day contract tests). Build and lint
still pass. The benchmark is reproducible: a re-run gives an identical CSV apart from latency.

### What was built (`eval/`)
- `scenarios/trips.yaml`: 10 trip templates (2 domestic, 8 international, 3–7 nights, 1–5 travellers, 5
  currencies). `scenarios/disruptions.yaml`: 9 kinds × severities.
  `generate_scenarios.py` → `scenarios/scenarios.yaml` has **160 scenarios**.
- `oracle.py`: the ground-truth affected set (global contract re-check to a fixed point, no drift), checked
  against `scenarios/labels.yaml` (**71 hand-labelled scenarios, 71/71 agree**). The labels were written from the
  domain rules before the oracle was run.
- `baselines.py`: ours, B1 full, B2 naive, and the ablations no contracts, no frozen context and no Risk Gate.
- `run_benchmark.py`: plans and books each trip once (a simulated user approves), then runs every scenario ×
  strategy on deep copies against the same seeded market drift. Results go to `results/results.csv`,
  `results_no_drift.csv`, `summary.json` and `summary.md`; `charts.py` draws 6 PNGs.
- `RESULTS.md`: the results tables and the analysis, including the limitations.

### Supporting changes in the app
- `app/integrations/drift.py`: **seeded market drift** for the mock flights, places and booking quotes (±8 % price,
  15 % unavailability). Off by default; only the benchmark turns it on. This resolves open question 10.
- `RiskGate.execute_without_approval`: the "no Risk Gate" ablation only. It records `decision="auto"`, so the audit
  counts it as unsafe, and no API route calls it.
- `plan_stability` now compares what the traveller experiences (flight and times, hotel and dates, pickups,
  activities) rather than every output field, so a re-quoted price for the same flight isn't a "change".
- **Bug found by the benchmark:** after an 8 h delay, day 1 could end up with no activities, and the
  transfer→day-start contract errored ("missing day_start_iso") instead of holding. Empty days now hold for
  `TRANSFER_BEFORE_DAY_START` and `DAY_ENDS_BEFORE_PICKUP`, with tests. The benchmark was re-run; no metric or
  label was changed.
- **Metric bug found by a test:** `unsafe_action_rate` counted an auto-executed action twice. It now counts
  distinct actions.

### Results (drift on; full tables in eval/RESULTS.md)
| | Ours | B1 full | B2 naive |
|---|---|---|---|
| Precision / Recall / F1 | 99.6 / 100 / 99.7 % | 15.8 / 100 / 25.2 % | 42.7 / 100 / 52.6 % |
| Plan stability | 88.8 % | 31.0 % | 55.4 % |
| Tasks regenerated | 2.27 | 14.39 | 6.19 |
| LLM / API calls | 0.22 / 1.64 | 4.49 / 14.26 | 2.89 / 5.69 |
| Unsafe actions | 0 / 31 | 0 / 134 | 0 / 66 |

The "no Risk Gate" ablation executes 31/31 T2 actions without approval (a 100 % unsafe rate) with otherwise
identical quality.

## Phase 8b — Evaluation metrics v2 (seven-metric framework) ✅ (2026-11-01)

**Tests:** 378 passed, 0 failed (337 before + 41 in `test_metrics_v2.py`). `eval/results/` and `eval/RESULTS.md`
were verified unchanged by checksum.

### What was built
- `app/core/metrics.py` gained **additive, pure functions** (no agents, no LLM):
  - `task_preservation`;
  - `executability` (E1–E4: run-level plan validity vs execution readiness, plus task-level rates);
  - `contract_evaluable`, `user_requirements`, `requirement_satisfaction`, `requirement_regressions`;
  - `travel_cost_change`, `activity_time_retention`, `contract_recovered`, `strict_recovered`, `ratio`.

  The existing functions are unchanged.
- `eval/metrics_v2.py`: per-run metric columns, a real-LLM guard (`no_real_llm`, `assert_mock_llm`), scenario
  parity and legacy parity, aggregation that never imputes (N/A counted separately), and the generated report.
- `eval/run_benchmark.py --metrics v2` writes **only** to `eval/results_v2/`: `results.csv`, `results_no_drift.csv`,
  `summary.json` and `METRICS_REPORT.md`. The default (legacy) run is unchanged.

### Checks (full run, 160 scenarios × 6 strategies × drift on/off)
- **Real LLM calls: 0** (blocked). **LLM calls while computing metrics: 0.** The oracle made 35 MockLLM
  invocations (deterministic template code).
- **Scenario parity:** 0 scenarios where the event or oracle set A differs between strategies.
- **Legacy parity:** 0 mismatches against `eval/results/results*.csv` on every original column except `latency_ms`
  (960 + 960 runs).

### Key results (drift on; full tables in eval/results_v2/METRICS_REPORT.md)
| Metric | Ours | B2 | B1 |
|---|---|---|---|
| Task Preservation Rate (unaffected tasks) | 99.95 % | 78.05 % | 59.87 % |
| Replanning scope | 15.3 % | 41.0 % | 94.5 % |
| Plan-valid runs (executability) | 93.8 % | 93.8 % | 93.8 % |
| User-requirement satisfaction | 88.9 % | 89.7 % | 89.4 % |
| Strict recovery | 88.8 % | 89.4 % | 88.8 % |
| MockLLM / API calls per replan | 0.22 / 1.64 | 2.89 / 5.69 | 4.49 / 14.26 |

**Findings recorded honestly:**
- **4 of 10 trips are over the user's budget before any disruption** (synthetic fares).
- **Ours is not better on user requirements.** It keeps existing bookings and prefers minimal-change
  replacements, so cancellations cost more:
  - outbound cancellation: +11.0 % of the budget vs +4.3 % (drift off);
  - B2 and B1 re-search and, under drift, sometimes come back cheaper (6 runs, listed in the report).
- **TPR is reported to 2 decimals,** so 99.95 % is not shown as 100 %.

## Checklist

### Phase 3 — Orchestrator
- [x] Unified mode config (`VOYAGESYNC_MODE`), LLM settings in `config.py`, `.env.example` update, `requirements.txt` refresh
- [x] `app/llm/`: provider-agnostic client, `MockLLM`, one live provider
- [x] Validation + retry with error feedback + template fallback
- [x] Contract bindings from node outputs; correct templates per edge type
- [x] User profile & memory table used as context
- [x] `POST /trips`, `GET /trips/{id}/graph`, `GET /trips/{id}/itinerary`
- [x] Wire Create-trip button + preview
- [x] Tests: decomposition, retry/fallback, contract validity, end-to-end API

### Phase 4 — Real agents (behind mock mode)
- [x] Flight (Duffel test), Hotel (Geoapify + mock booking), Itinerary/Transfer (places + OSRM + LLM), Weather (Open-Meteo), Budget (Frankfurter)
- [x] Output validation per agent; recorded fixtures (keyless APIs real; Duffel/Geoapify synthetic until keys exist)

### Phase 5 — Disruption Monitor + partial replan
- [x] `DisruptionEvent`, simulator, dedup, optional polling (weather forecast)
- [x] `PROPAGATE` with frozen context; B1/B2 baselines
- [x] `POST /trips/{id}/disruptions`; Santorini key test

### Phase 6 — Risk Gate + HITL
- [x] T0/T1/T2 rules, approvals API, WebSocket/SSE; unsafe-action-rate = 0 test

### Phase 7 — UI demo
- [x] React Flow graph view, disruption panel, approval cards, T1 toasts, metrics panel

### Phase 8 — Evaluation harness
- [x] ≥100 YAML scenarios (160), oracle (+71 hand labels), benchmark (ours/B1/B2 + 3 ablations), CSV, charts, `eval/RESULTS.md`

---

## Refactors log
- **Phase 3, `scheduler._resolve_inputs`**: also passes `_parents[parent_id] = {type, output}` because flat merging loses data when parents share key names. This is additive; the flat merge is unchanged.
- **Phase 3, `contracts._parse_iso`**: offset-aware times are converted to naive UTC so that naive and aware values can be compared without a `TypeError`.
- **Phase 3, `mocks.py`**: the mock agents were rewritten to derive outputs from inputs and parents. Class names, the registry and the fields existing tests use are kept. The old hard-coded Tokyo/GBP values are gone; legacy `*_gbp` budget keys are only emitted when the caller passes them.
- **Phase 3, `orchestrator.py` / `llm/client.py` / `schemas/trip.py`**: the partial Phase 3 code was reworked rather than duplicated. `orchestrate_trip`/`decompose_trip` became `Orchestrator.plan_trip`/`decompose`. `ProposedTask` now carries `direction`/`day`/`theme`, and `depends_on`/`inputs` from the LLM are ignored.
- **Phase 3, config**: `MOCK_MODE` replaced by `VOYAGESYNC_MODE`, and `VOYAGESYNC_LLM_MOCK` removed.
- **Phase 4, templates**: added the edges `hotel_stay → airport_transfer_in/out` (`TRANSFER_SERVES_HOTEL`). Phase 3 tests now expect 27 edges instead of 25. The mock transfer agent now reports `hotel_id`.
- **Phase 4, Orchestrator default registry**: the real agents (`build_agent_registry`) replace the Phase 2 mocks as the default; they share the Orchestrator's LLM client. `AgentRegistry` gained a `providers` attribute.
- **Phase 4, `OrchestratorResult.usage` / `TripExecutionResponse.usage`**: new field.
- **Phase 5, `Scheduler.run(node_context=…)`**: optional per-node additions to the frozen context (additive).
- **Phase 5, `GraphStore.set_node_inputs`**: replaces a node's inputs (facts and exclusions) without a version bump; the change is captured by the next status change.
- **Phase 5, `TRANSFER_AFTER_FLIGHT` minimum gap 30 → 15 min**: with pickup at arrival + 30, a 30-min minimum made *any* delay break the contract. Plan §6.3 expects a 10-minute delay to have no effect.
- **Phase 6, `Scheduler._find_ready`**: a parent in AWAITING_APPROVAL counts as settled, like DONE. It already
  holds its proposed output, so a re-plan can regenerate its children while the booking waits for approval.
- **Phase 6, `monitor/apply.py`**: events can target a node in AWAITING_APPROVAL, and applying facts keeps the
  node's status instead of forcing DONE.
- **Phase 6, `RiskTier` comments**: corrected to the plan's meaning (T0 autonomous, T1 notify, T2 approve).
- **Phase 6, `test_trips_api`**: after `POST /trips` the flights and hotel are AWAITING_APPROVAL (the booking
  cards). The old "all DONE" check now applies to `propose_bookings: false`.
- **Phase 8, mock providers**: `MockFlights`, `MockPlaces` and `MockBookingProvider` take an optional `drift`
  (shared through `Providers.drift`), inactive by default; the outputs are unchanged unless the benchmark enables it.
- **Phase 8, `metrics.plan_stability`**: compares the traveller-visible identity of items (see Phase 8 above). The
  Phase 7 test was updated to change an activity instead of a price.
- **Phase 8, `contracts.check_edge`**: a day plan with an empty activity list satisfies `TRANSFER_BEFORE_DAY_START`
  and `DAY_ENDS_BEFORE_PICKUP`.
- **Phase 8, `RiskGate.approve`**: now a thin wrapper over `_execute(decision=…)`, shared with the ablation hook.
- **Phase 7, `DisruptionRequest` / `DisruptionResponse`**: new optional `compare_baseline` and `metrics` (additive,
  off by default).
- **Phase 7, `ItineraryPreview.ItemCard`**: optional `badge` prop for the booking state; existing cards unchanged.
- **Phase 7, `UpcomingTrips` text**: "Nothing has been booked yet" became "Bookings, changes and payments always ask
  for your approval first", since bookings can now exist.
- **Phase 5, agents**: Flight, Hotel and Day-plan agents read `exclude_*` inputs and the frozen context (`previous_output`, `frozen_neighbours`). The Phase 2 mock agents in `mocks.py` ignore them, so re-planning should use the real agents (the default).

## Open questions
1. **Multi-city trips**: the template supports exactly one STAY. Is that enough for the demo and benchmark? (Choice for now: one city per trip.)
2. **Times are naive local times** (no time zones) in mocks and contracts. Duffel also returns local times without an offset, so this matches; `_parse_iso` still normalises any offset-aware time to UTC.
3. **Every day has its own weather node** (§5.3 shows one shared node feeding days 2–3). One node per day makes "storm on day N" precise. (Choice: one per day.)
4. `.claude/launch.json` was added so the two dev servers can be started from Claude Code's preview tool. Commit it or ignore it, as you prefer.
5. Pre-existing deprecation warnings (35: SQLAlchemy `mode=memory` pool selection in `test_graph.py`, Starlette/httpx test client) are unchanged and harmless.
6. **Duffel and Geoapify keys.** No keys were available, so their live clients are written from the published API formats and tested only against hand-written sample responses (`*_sample.json`, marked `_synthetic`). With a Duffel test token and a Geoapify key in `.env`, run `scripts/record_fixtures.py` and a live trip to confirm against the real servers. **This is the one unverified part of Phase 4.**
7. **Weather for trips more than 15 days ahead** uses the same calendar days from the most recent past year (Open-Meteo archive), labelled `open-meteo-archive-proxy`. This is a climatological stand-in, not a forecast. The Disruption Monitor (Phase 5) will use real forecasts when a trip is near.
8. **OSRM demo server** is fair-use only (fine for a demo). For heavier use, switch to OpenRouteService with a key or run OSRM locally.
9. **Activity prices** are per-category estimates in USD (`CATEGORY_COST_USD`), not provider prices; Geoapify does not return prices.
10. ~~B1 looks better on stability than it would in reality.~~ **Resolved in Phase 8** with seeded market drift; RESULTS.md reports both settings. Original note: With deterministic mocks, B1 regenerates 15 nodes but only 3 actually change. Real APIs drift between searches (prices, availability). Phase 8 should model that drift in the mock providers so stability can be compared fairly; otherwise only precision, cost and latency separate B1. (Proposal: seed a small price and ranking drift from the re-plan event; document it in RESULTS.md.)
11. ~~Rebooking without approval~~ **Resolved in Phase 6.** The regenerated flight is only a draft offer. The Risk Gate raises a T2 `change` card and the node waits in AWAITING_APPROVAL; nothing is booked until the user approves. This is gated only when a ledger is passed: the API always passes one, and the plain `handle_disruption` used by Phase 5 tests books nothing either way.
12. **Live polling is on demand** (`POST /trips/{id}/monitor/poll`) rather than an APScheduler loop, and covers weather only. Live flight status (AviationStack/AirLabs) needs a key; the simulator is the main source, as the plan says.

13. **Flight booking is sandboxed.** Approved flights are "booked" in our own sandbox (`MockBookingProvider`), not as Duffel orders. A Duffel test order needs passenger names, dates of birth and contact details, which VoyageSync doesn't collect. Adding it later means one call in `ActionExecutor`; the gate does not change.
14. **Rejecting a re-booking** leaves the replacement offer as an unbooked draft (`booking_status: rejected`). The system doesn't search for the next option by itself. Phase 7's approval card could offer "show the next option" through a new disruption or a re-search.
15. **"No Risk Gate" ablation (Phase 8)** isn't built yet. The plan is for the harness to auto-approve every T2 action the moment it is raised (`decided_by="ablation"`), so the audit counts those executions as unsafe. Then the metric is non-zero for that ablation and 0 for ours.
16. **Approval reminders are lazy.** They are sent when `GET /approvals` runs (and through `RiskGate.sweep`), not on a background timer. There is no APScheduler yet, as in Phase 5.
17. **Undo can bring back a broken contract.** Undoing "Rebooked pickup for 19:30" restores the 14:30 pickup, which breaks `flight_outbound → airport_transfer_in` again. The undo response lists `broken_edges` so the UI can warn; the user's choice is respected.

18. **The Phase 7 oracle is contract-based.** It re-checks every contract globally, so it can't see a dependency that no contract encodes. On these mock scenarios our PROPAGATE matches it exactly (P = R = 1). That's expected, and it is why Phase 8 has to validate the oracle on a hand-labelled subset and add scenarios where local propagation and the global check could differ.
19. **Stability is equal for ours and B1 on delays** (78 % on a 5 h delay) because the mocks are deterministic, as in open question 10. B1 differs on cancellations (78 % vs 100 %) because it lacks frozen context. Phase 8's price and availability drift will separate them further.
20. **`npm run lint` in Git Bash** can fail with "oxlint is not recognized" (a PATH quirk of the npm shim); `node node_modules/oxlint/bin/oxlint` and PowerShell both work. No code issue.
21. **The graph is small on narrow screens** (phones). It fits the view, and the zoom controls work. The demo is meant for a laptop or projector.

22. **The hand labels were written by the assistant**, from the domain rules and before running the oracle. The team should review `eval/scenarios/labels.yaml`; storms, mid-severity delays and the extreme cases are not labelled.
23. **10 of 160 scenarios (10 h delays) are unrepairable by every strategy**: arrival after the 23:59 check-in cutoff. A "request late check-in" action (T1 or T2) would be the natural extension.
24. **No user study** (plan §6.6, trust and approval fatigue). T2 prompt counts are reported as a proxy: 31 for ours vs 134 for B1.
25. **The drift parameters (±8 % price, 15 % unavailability) are assumptions**, not measured market behaviour. The no-drift results are reported alongside.
26. **matplotlib** was installed into `backend/.venv` for the charts and is listed in `eval/requirements.txt`, not the backend's `requirements.txt`, because the app doesn't need it.

27. **The v2 executability, constraint and time/cost metrics are operational definitions, not TDAG's.** TDAG's (ItineraryBench) scoring rules were not verified, and no 60/20/20 combined score is computed. If the team obtains the formulas, add them as separate columns.
28. **The user's budget is not an edge contract.** `BUDGET_WITHIN_LIMIT` exists but is attached to no edge, so contract satisfaction ignores the budget; v2 measures it as a user requirement. Making it a contract would change the existing benchmark definitions, so it was left as is.
29. **Execution readiness equals plan validity in the benchmark** because the simulated user approves every T2 action. The metric is still computed separately and unit-tested with tasks awaiting approval.

### Resolved
1. ~~Bump `requirements.txt` for Python 3.14?~~ **Yes** (2026-10-16) — done in Phase 3.
2. ~~Keep the OpenAI-compatible client as the single live LLM provider?~~ **Yes** (2026-10-16).
