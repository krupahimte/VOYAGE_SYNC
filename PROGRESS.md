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

## Hybrid (BFS + beam search + GA), Phase 0 — Baseline freeze ✅ (2026-11-01)

**Tests:** 386 passed, 0 failed (378 before + 8 in `test_baseline_freeze.py`). No application code changed.

The hybrid work follows the reviewed plan (review decisions R1–R12, accepted 2026-11-01). Phase 0 fixes what must
not move before any agent is refactored.

### What was built
- `eval/golden.py` → `eval/golden/e1_snapshot.json`: **E1 golden snapshot**. Covers the 10 planned trips and
  160 scenarios × drift on/off, as the benchmark's "ours" runs them (handle the disruption, then the simulated user
  approves). For each run it records:
  - the oracle set;
  - the stale, regenerated, changed and failed lists;
  - the Risk Gate actions;
  - every node's status and a hash of its output.
  - Only `booking_ref`, `booked_at` and `approval_id` are left out. Two runs in separate processes differ in these
    fields alone (random or wall-clock); with them excluded, the runs are identical.
  - A deliberate change to the flight ranker gives over 1,000 differences, so the check is sensitive.
- `eval/manifest.py` → `eval/baseline_manifest.json`: sha256 of the 19 protected files (`eval/results/`,
  `eval/results_v2/`, `eval/RESULTS.md`, `eval/scenarios/`). Text is hashed with LF endings (`core.autocrlf` is on).
  It was recorded from the committed state (no diff against HEAD). New v3 scenario files go in `eval/scenarios_v3/`,
  so the check stays strict.
- `eval/preregistration.yaml`: **draft**. Covers:
  - claims H1–H4 and when the GA counts as justified;
  - the arms (E1, E1g, E2, E3, E4, RS, EXH) and the primary metric;
  - the ground-truth definition;
  - the objective, hard constraints, evaluation budget, parameter grids, seed rule, trip-level split and statistics.
  - It is frozen, with its hash, before the held-out run.
- `run_benchmark.py` already had `--out`, so the audit's "no `--out` option" was wrong; nothing to add.

## Hybrid, Phase 1 — Candidate generation ✅ (2026-11-01)

**Tests:** 448 passed, 0 failed (386 before + 62 in `test_candidates.py`). The E1 golden snapshot is unchanged. A full
benchmark run into a scratch folder matches `eval/results/results*.csv` on every column except `latency_ms`, for
all 1,920 rows and all 6 strategies, LLM and API call counts included.

### What was built
- `BaseAgent.candidates(node, resolved_inputs, frozen_context, k)` (additive):
  - returns up to k outputs, best first, in a fixed order;
  - option 0 is what `run` returns;
  - the default is `[run()]`, used for weather (facts) and the budget (derived).
- `distinct(results, k)` drops repeated outputs and failed options. If every option fails, the first failure is
  returned.
- Each agent was split into "find and rank" and "build the output". `run` builds the top-ranked option, so its
  provider calls are unchanged:

  | Agent | Options | Extra provider calls |
  |---|---|---|
  | Flight | the valid offers, in `_ranker` order (a stable sort, so option 0 is `min`'s pick) | none |
  | Hotel | today's pick first (nearest to the previous hotel, else the stable-hash pick), then by distance or in place-ID order from that pick | one booking quote per option |
  | Transfer | the same route at other pickup timings: offset after arrival 30 (the node's own), then 15/45/60 min; airport buffer before departure 180, then 150/210/240 min. All inside the `TRANSFER_AFTER_FLIGHT` and `TRANSFER_BEFORE_DEPARTURE` windows. | none (one route lookup) |
  | Day plan | the picker seed shifted by 0–5 from one places search, with duplicates dropped. Kept activities stay kept in every variant. | none |
- **No LLM calls while generating options.**
  - Day-plan options carry `pending_descriptions` (indexes of the new activities) and `descriptions_source:
    "pending"`.
  - `DayPlanAgent.complete_descriptions` writes the descriptions (one LLM call) for the option that is committed.
  - `run` is that plus variant 0, so its output is unchanged.
  - One difference: a day plan that fails output validation no longer makes the description call first. No day
    plan failed validation in any benchmark run, so the counts did not change.

### Effective number of options (10 trips, k = 3 flight/hotel/day plan, 4 transfer)
- At planning time: every node reaches k (flights 3, hotels 3, transfers 4, day plans 3).
- With the frozen context of an *undisrupted* plan, day plans have exactly 1 option, because every activity is
  still feasible and is kept. Day-plan variants appear only where a disruption forces slots to be refilled.
- The effective k under real disruptions is measured in Phase 3. It bounds the candidate space, and with it
  whether a GA can be justified.

## Hybrid, Phase 2 — View, validator and objective ✅ (2026-11-01)

**Tests:** 477 passed, 0 failed (448 before + 29 in `test_repair_constraints.py`). No existing module changed, and the
E1 golden snapshot and the manifest are unchanged.

### What was built (`app/core/repair/`, new package; the Replanner does not use it)
- `view.py`, `GraphView`: a read-only overlay of candidate outputs on a `GraphStore`.
  - It answers `get_node`, `list_nodes`, `list_edges` and `validate` like the store. `Scheduler._resolve_inputs`,
    `check_edge` and the metrics therefore work on it unchanged.
  - `with_output` returns a new view; a test checks the store's `to_dict()` is unchanged afterwards.
- `constraints.py`, `validate_plan(graph, reference, region, goal)` returns a `ValidationReport` with seven checks
  (R6):
  - every contract holds and could be evaluated (`check_edge` + `contract_evaluable`);
  - repaired outputs pass their schemas (`validate_output`);
  - every task outside the region is exactly equal to the reference;
  - dates and party size are met (`user_requirements`); the budget stays soft;
  - excluded offers and hotels are not reused;
  - no repaired day plan is empty, unless it is day 1 and the arrival leaves no start time that day;
  - the graph is still acyclic.
- `objective.py`, the four terms on fixed scales, written into `eval/preregistration.yaml` (`objective.scales`)
  before any dev run:
  - **disruption**: mean per-task change over the user-visible tasks except the disrupted one (pickup shift / 180
    min; the share of a day's activities not kept; 1 for another flight or hotel);
  - **money**: Δcost / budget, plus the new overrun / budget again;
  - **activity time lost**: per-day losses, not offset by gains on other days, ÷ planned minutes;
  - **approvals**: booked items whose offer changed ÷ bookable items.
  - `fitness = −Σ w·term` with equal weights. `deb_key` orders plans feasible first, then by fewer violations,
    then by higher fitness.

### Checked against E1 (every scenario, drift off)
- The validator's contract failures equal `check_graph_contracts` on all 160 repaired plans.
- **No task outside E1's stale set changed in any scenario**, so the frozen check matches what E1 does.
- Exactly the 10 ten-hour-delay scenarios are infeasible, on contracts only. These become conflicts for E2–E4 in
  Phase 4.
- **The empty-day rule needed a correction.** My first version flagged `jaipur…outbound_delay_480`: drop-off at
  22:55, so the earliest start is 23:55. The agent rounds that up to the next quarter hour (00:00, the next day), so
  the day truly has no start time left. The rule now mirrors that rounding, and the pre-registration states it.

## Hybrid, Phase 3 — Repair search: greedy (E1g), exhaustive (EXH) and atomic commit ✅ (2026-11-01)

**Tests:** 496 passed, 0 failed (477 before + 19 in `test_repair_search.py`). The E1 golden snapshot and the manifest are
unchanged.

### What was built
- `app/core/repair/search.py`:
  - `RepairSearch` runs E1's contract-pruned BFS inside every branch:
    - tasks are taken in longest-path depth order;
    - a broken incoming contract prunes an option;
    - a changed output whose outgoing contract to a frozen child breaks adds that child to the region.
  - The disrupted task under a facts event is fixed (R7). The budget is an ordinary region task with one option, so
    money is exact.
  - Agents get E1's frozen context: their previous output plus the neighbours that were not stale when their depth
    level began.
  - `OptionGenerator` caches options by (task, k, resolved inputs, frozen context). An agent exception counts as "no
    options", never a crash.
  - `EvalBudget` counts plan evaluations (the budget), partial evaluations, option generations and cache hits. A
    wall-clock timeout is only a safety net and is flagged when hit.
  - Policies:
    - `rank`: the first surviving option, used to check parity with E1;
    - `greedy` (E1g): the best partial fitness, ties to the lower rank;
    - `exhaustive` (EXH): every combination, ranked by `deb_key`, up to `max_exhaustive`.
- `app/core/repair/commit.py`:
  - the plan is written to a deep copy; day plans get their descriptions; facts are kept or dropped as in E1's
    `_reapply_facts`;
  - the copy is re-validated, then replaces the store (`GraphStore.restore_from`, a new additive method). Any failure
    leaves the store untouched.
- Wiring:
  - `Strategy.GREEDY` / `Strategy.EXHAUSTIVE` in the Replanner, dispatched to `_repair`. `ReplanResult.repair` is a
    new optional summary field. `Replanner(repair_params=…)` is optional.
  - The monitor's no-effect shortcut also applies to these strategies.
  - The API accepts `strategy: greedy | exhaustive`.
  - With no feasible plan, nothing is committed and `replan.repair.feasible` is false. The status stays `replanned`
    until Phase 4 adds conflicts.

### Checks
- **Parity with E1.** The `rank` policy reproduces E1 on every scenario, drift on and off:
  - the same region and byte-identical outputs, descriptions included, on all 260 repairable runs;
  - the same 40 no-effect runs;
  - the 20 ten-hour-delay runs give "no feasible plan" instead of a committed broken plan.
- **Exhaustive vs. the others** (drift off, 140 scenarios with an effect): no policy ever beats exhaustive, and all
  three are feasible on the same 130.

### Search-space measurements (drift off, k = 3/3/4/3)

| Kind | n | complete plans mean / max | greedy = optimum | rank (E1) = optimum |
|---|---|---|---|---|
| outbound delay | 50 (10 infeasible) | 5.4 / 12 | 39 / 40 | 19 / 40 |
| return delay | 10 | 4 / 4 | 10 | 10 |
| outbound cancellation | 10 | 8.2 / 13 | 8 | 8 |
| return cancellation | 10 | 8 / 8 | 4 | 0 |
| hotel overbooked | 10 | 48 / 48 | 5 | 5 |
| storm (mid / last day) | 20 | 2.0–2.4 / 3 | 20 | 20 |
| price spikes | 30 | 1 / 1 | 30 | 30 |

- **The realistic option space is tiny.** At most 48 complete plans; the exhaustive search takes at most 0.06 s and
  85 option generations. Day plans add almost nothing: with frozen context they keep their activities, so they
  have 1 option.
- **There is real headroom over greedy.** E1g misses the optimum in 14 of 130 feasible scenarios (mean gap 0.008,
  max 0.32 fitness). E1's own ranking misses it more often (outbound delays, return cancellations).
- **Implication for the paper, as pre-registered.** With at most 48 plans, a beam search or plain enumeration
  reaches the optimum cheaply. A GA cannot be justified on the realistic set: S ≤ P·G holds on every scenario. Only
  the stress set (Phase 9: more offers, combined disruptions) can test H2 and H3.
- **Unexplained one-off.** One API test run took 329 s; three re-runs took 1.3–1.7 s, and a single request takes
  0.12 s. Not reproduced, no cause found.

## Hybrid, Phase 4 — Conflicts, the DECIDE action and the validation flag ✅ (2026-11-01)

**Tests:** 517 passed, 0 failed (496 before + 21 in `test_conflicts.py`). The E1 golden snapshot and the manifest are
unchanged. A fresh legacy benchmark run matches `eval/results/*.csv` on every column except `latency_ms`, all 1,920
rows.

### What was built
- **Conflicts.** A search strategy that cannot commit a feasible plan gets `status: "conflict"`, commits nothing, and
  attaches a conflict report in `replan.repair.conflict`. This covers both "no feasible plan" and a failed commit.
  The report contains:
  - the reason;
  - the violations;
  - a **best-effort plan**: `search.best_effort`, E1's behaviour made explicit. It never prunes and takes the option
    with the fewest broken incoming contracts, so every violation is named. Its region outputs come with
    descriptions already written;
  - the options `accept_best_effort` (approve) and `keep_current` (reject);
  - a digest of every task's output.
- **`ActionKind.DECIDE`**, T2 by its own rule. It is not in `ALWAYS_T2` / `SPEND_KINDS` and spends nothing.
  - `RiskGate.raise_decision` adds the card *without marking any task*. A booking card's AWAITING_APPROVAL stamp
    would have overwritten a booked, delayed flight.
  - Approving runs `commit.accept_best_effort`, which refuses unless:
    - the graph's digest is unchanged since the conflict, and
    - the written plan violates *exactly* the violations the user was shown.
    It then runs `review_replan` for follow-up booking cards and records `violations_accepted`.
  - A changed graph supersedes the card (409 through the API). Rejecting keeps the plan.
  - The "no Risk Gate" ablation auto-accepts the card, and `unsafe_actions` counts that as unsafe.
- **Validation flag on every replan.** `ReplanResult.validation` holds the seven hard checks on what the replan left,
  plus `silent_failure`: something was regenerated, the plan is infeasible, and no conflict was raised. Statuses
  are unchanged (R12). Monitor latency now also covers this validation, which matches the paper's definition ("to
  validation of the revised itinerary"). Latency is excluded from parity.
- **Wiring.** The Orchestrator raises the DECIDE card on a conflict. The API saves the graph and ledger on a conflict
  too (the event itself is real). `DisruptionResponse.status` accepts `conflict`. The frontend does not yet know the
  `conflict` status or the DECIDE card (P3 UI phase).
- **Refactor (additive):** `commit.write_plan` is shared by a normal commit and an accepted best-effort plan.
  `RepairSearch.choose(..., relaxed=)`, `_expand(..., relaxed=)` and `skip` were added for best effort.

### Checks
- All 10 ten-hour-delay scenarios, under greedy and exhaustive, give a `conflict`:
  - nothing is committed (every output equals the graph after the event) and no task is marked;
  - exactly one pending T2 DECIDE card, whose violations name `FLIGHT_BEFORE_CHECKIN`.
- **E1 on the same scenarios stays `replanned`, with `silent_failure: true`.** That is 10 of 160 per drift setting:
  the measurable difference H4 pre-registered.
- Accepting the best-effort plan commits exactly E1's region and outputs, booking stamps included. So "best effort"
  is E1's plan, now explicitly chosen by the user instead of silently applied.
- API round trip, approve and reject: the decision is made on the graph reloaded from SQLite (the digest survives
  the round trip), the accepted plan still reports the broken cutoff, and the conflict appears in the disruption
  history.

## Hybrid, Phase 5 — Ground truth v3 and the labelling protocol ✅ (2026-11-01)

**Tests:** 531 passed, 0 failed (517 before + 14 in `test_ground_truth.py`). No application code changed. The E1
golden snapshot and the manifest are unchanged.

### What was built
- `eval/ground_truth.py` → `eval/scenarios_v3/ground_truth.yaml` (sha256 in `ground_truth.sha256` and in the
  pre-registration). It is computed before any E2–E4 code exists.
  - **How:** for every scenario × drift setting, the exhaustive search enumerates every complete repair in the option
    space and keeps those that pass the hard constraints. It starts from the same booked trip as the benchmark and
    uses the same drift seed.
  - **Necessity set:** the intersection of their repair regions.
  - **Min scope:** the smallest region; all minimal regions are recorded, and the union is kept for reference.
  - **Conflicts:** `feasible: false`, with the necessity set and min scope null.
  - The legacy oracle's set A is stored beside it.
  - The file records the option space (k, pickup timings, day-plan variants) and the sha256 of the 21 source files
    that define it.
  - `load_verified` refuses a file whose hash changed, or whose option-space sources changed.
  - `--rehash` re-stamps the source hashes only if a full recompute reproduces the stored ground truth exactly
    (needed when Phase 6 edits `search.py`); the new sha must be recorded as a deviation.
  - A test checks that the pre-registration's recorded sha matches the file.
- Scope metrics (pre-registered secondary metrics): `necessity_coverage` (|R∩N|/|N|), `excess_scope`
  (|R| − min scope) and `scope_optimality` (min scope / |R|). Each is None when undefined, never 0 or 1.
- Team labelling (R2): `eval/labels_v3.py` and `eval/scenarios_v3/labels/`:
  - `LABELLING_PROTOCOL.md`: two blind annotators, the exact question, the acceptability rules, adjudication, and
    "mismatches are reported, never tuned away";
  - `briefs.md`: each trip *before* the disruption, its dependencies and the alternatives; a test checks it contains
    no ground-truth fields;
  - a blank `annotator_template.csv` with 48 scenarios / 495 rows (weather and the delayed flight are not asked);
  - `agree` (Cohen's κ and the disagreements) and `compare` (adjudicated labels vs the necessity set).
  - **The labels themselves are for the team to produce. None exist yet.**

### Findings
- The ground truth is reproducible: a recompute in a fresh process is identical. It is the same with drift on and
  off in every scenario.
- At most 48 feasible repairs per scenario, mean 6.5.
- The 10 conflicts are exactly the ten-hour delays.
- **The legacy oracle's policy bias, measured:**
  - A equals the necessity set in 147 of the 150 feasible scenarios.
  - In the other 3 (`outbound_delay_180` on Santorini, Goa and Lisbon), A also contains `day_1_plan` and
    `budget_check`. E1's fixed 30-min pickup forces those two, but a 15-min pickup keeps both valid.
  - So the bias the review predicted is real but small on this benchmark.
  - The necessity set is defined over a larger option space than E1 uses. That is intended, and stated as a
    limitation.

## Hybrid, Phase 6 — Beam search (E2) and random search (RS) ✅ (2026-11-01)

**Tests:** 549 passed, 0 failed (531 before + 18 in `test_beam_random.py`). The E1 golden snapshot, the manifest
and the v3 ground truth all still verify. `search.py` was not edited, so the option-space hashes are unchanged and
no rehash was needed.

### What was built (`app/core/repair/policies.py`, new)
- **`beam(search, width)` (E2).**
  - Each state in the beam takes its next region task in depth order and branches over that task's surviving
    options.
  - The best `width` children by partial fitness are kept, ties broken by the choices made, so the search is
    deterministic.
  - Complete states are validated and scored as they appear.
  - It uses `RepairSearch`'s steps unchanged, so the pruning, frozen tasks and region rule are E1g's and EXH's.
  - It is named beam search, not ToT: no LLM is involved.
- **`decode(search, pick)`.** The GA's repair decoder, built now so random search shares it exactly: the first
  option at or after the picked index (wrapping) that survives the contract checks; None at a dead end.
- **`random_search(search, seed)` (RS).**
  - Random repairs through the decoder, until the budget is spent.
  - Each distinct repair costs one evaluation; a dead-end draw costs one too.
  - It stops early after 200 draws in a row that found nothing new, or after budget × 20 draws, and records which
    rule stopped it.
- **`PolicyParams(beam_width=5, seed=0, max_draw_factor=20, max_stall_draws=200)`.**
  - Passed through `repair(..., policy_params=)` and `Replanner(policy_params=)`.
  - `Strategy.BEAM` / `Strategy.RANDOM` are added; the API accepts `beam` and `random`.
  - Conflicts work as in Phase 4.
- **Pre-registration.**
  - It now records the beam ranking and RS's decoder and stopping rule.
  - It states what the budget counts: complete repairs, plus dead-end draws for RS/GA.
  - It lists what is reported alongside: beam's partial evaluations, option generations, cache hits and API calls.
    So beam's extra work is visible rather than hidden by the unit.

### Invariants checked (every scenario with an effect, drift off)
- Beam of width 1 = E1g (same best plan, fitness and choices).
- Unbounded beam = exhaustive (the same set of complete plans and the same optimum).
- Widths 1, 3, 5 and 10 never beat exhaustive, and agree with it on feasibility.
- The decoder at index 0 equals the `rank` policy (first surviving option).
- RS: the same seed gives the same plans and different seeds give different ones; plans are unique; the budget is
  never exceeded; it never beats exhaustive.
  - With budget 500 it finds all 48 plans of a hotel-overbooked space.
  - On a conflict scenario it spends the budget on dead ends.

### Not measured, on purpose
- **How well each beam width or RS performs is not measured here.** Picking a width from these scenarios would be
  tuning on data that may become held-out (R9: tune on dev only).
- **Protocol risk for Phase 9.** Phase 3 already reported greedy-vs-exhaustive numbers on all 160 current scenarios.
  To keep held-out results unseen, the held-out set should consist of the **new** trip templates only, and the 10
  existing trips should go to dev. This must be decided before the split is frozen.

### Fixed during this phase
- Random search first drew until budget × 20 draws even after covering a small space: 25 s for one 48-plan
  scenario, hours for the full experiment. The stall rule (200 draws in a row with nothing new) brought it to well
  under a second, and the 48-plan space is still fully covered.

## Hybrid, Phase 7 — Genetic algorithm (E3) and the hybrid (E4) ✅ (2026-11-01)

**Tests:** 594 passed, 0 failed (549 before + 45 in `test_ga_hybrid.py`). The E1 golden snapshot, the manifest and the
v3 ground truth all still verify; `search.py` is untouched.

### What was built (`app/core/repair/policies.py`)
- **`ga(search, GAParams, seed, seeds)` (E3).**
  - **Chromosome:** one option rank per task the disruption can reach (the target if it is re-planned, plus its
    descendants).
  - **Decoding:** the shared repair decoder. Genes outside the decoded region have no effect.
  - **Selection:** a tournament (3) on the Deb key; dead ends rank last.
  - **Variation:** uniform crossover (0.8) and per-gene mutation (1/L).
  - **Elitism (2):** the best distinct chromosomes survive unchanged, so the best key never gets worse.
  - **First population:** option 0 everywhere (E1's choices), any seeds, then random chromosomes.
  - **Stopping:** the generation limit (budget ÷ population), 10 generations with no improvement, or the budget.
  - A `GATrace` records, per generation, the best key, the feasible share, the mean feasible fitness and the
    evaluations used, plus the stopping reason. It is returned in `replan.repair.ga_trace`.
- **`hybrid(search, width, GAParams, seed)` (E4).** Beam search, then the GA seeded with up to population/3 of the
  beam's best feasible plans (`encode`). Both stages share **one** budget, and the result is the best plan either
  found.
- **`_Evaluator`, shared by RS, the GA and the hybrid.** A distinct repair is validated and scored once; a distinct
  dead end (keyed by its decoding path) is charged once; repeats are free.
  - This changes RS from one charge per dead-end draw.
  - The Phase 6 test was updated deliberately, and the rule is in the pre-registration.
  - `decode` now also returns that path.
- **Wiring:** `Strategy.GA` and `Strategy.HYBRID`; the API accepts `ga` and `hybrid`; `PolicyParams.ga` holds the GA
  settings. The full design is in the pre-registration (`parameters.ga`, `parameters.hybrid`).

### Invariants checked
On one scenario of each disruption kind, at its most severe level, on Santorini and Tokyo (18 scenarios, including
the ten-hour-delay conflict), seeds 0 and 1:
- The budget is never exceeded, for the hybrid's beam and GA together too.
- The best key never gets worse across generations.
- The GA is feasible exactly when exhaustive is, never beats exhaustive, and never does worse than E1's rank policy.
- Plans are unique.
- The hybrid never does worse than its own beam, never beats exhaustive, and seeds at least one plan when the beam
  found any.
- The same seed gives an identical run (plans and trace), and different seeds give different runs.
- `encode` → `decode` reproduces every one of the 48 exhaustive plans of a hotel-overbooked space, and every beam
  seed.

### Not measured, on purpose
- **Whether the GA adds value (H2, H3) is not measured here.** That is the pre-registered experiment.
- Phase 3 already suggests the realistic space is too small for a GA to matter. The stress set (Phase 9) is where it
  can be tested.

### Fixed during this phase
- The test sample first picked the "most severe" level by comparing facts as strings (a 90-min delay beat a 600-min
  one). It now compares the numbers, and the conflict scenario is in the sample.

## Hybrid, Phase 8 — Decision log and API ✅ (2026-11-01)

**Tests:** 607 passed, 0 failed (594 before + 13 in `test_decision_log.py`). The E1 golden snapshot, the manifest and
the v3 ground truth all still verify.

### What was built
- **`app/core/repair/decision.py`.**
  - `search_log` is built by every search strategy right after the search runs. It records:
    - the affected tasks, each with the contract that made it stale;
    - the alternatives: complete and feasible plan counts, the 10 best plans with their choices, terms, fitness and
      violations, and pruned branches;
    - **every option of every task on the chosen path**, with its label, whether it was chosen, and the broken
      contract if it was rejected. These are read from the option cache: a test checks that no agent is called again;
    - the selected plan and the search budget.
  - `decision_log` assembles the full log when it is read. It combines the disruption record (written once, never
    updated) with the Risk Gate actions raised for that event, with their *current* decisions.
  - It covers the paper's §VIII fields for **every** strategy: the disruption, affected tasks, alternatives, selected
    repair, validation, approvals, plus any conflict and the costs. E1, B1 and B2 say that no alternatives were
    compared.
- **Per-stage cost.** `repair()` meters wall-clock time, LLM calls and provider calls per stage: `search`, then
  `commit` or `conflict`. This shows that the search makes no LLM calls; the committed day plan's descriptions are
  the only ones.
- **API:** `GET /trips/{id}/disruptions/{event_id}/decision` (404 for an unknown event).
- **Deviation from the Phase 8 plan:** no separate `ReplanDecisionRecord` table. The disruption record is already the
  append-only trace and every gate action carries its `event_id`. A second copy could drift out of sync, so the log
  is assembled from those two instead. `Action.decision_id` was not needed either.

### Pre-existing bug found and fixed
- **Every disruption of a trip had the same event ID.** The endpoint builds a fresh `DisruptionSimulator(seed=0)` per
  request, and a fresh simulator repeats its IDs.
- The consequence: Risk Gate actions from different disruptions shared one `event_id`, so "the actions of this
  disruption" was ambiguous. This was the case before Phase 8 too.
- The endpoint now appends the trip's disruption count (`evt_sim_0_…-2`), which is still deterministic. No test
  depended on the old IDs, and the benchmark does not use this path.

### Checks
- On four disruption kinds (exhaustive search): exactly one chosen option per task, with no rejection reason; the
  selected plan is the first considered one; every affected task has a reason.
- Stages: a feasible repair has `search` (0 LLM calls) and `commit` (≥ 1, the descriptions). A conflict has `search`
  and `conflict`.
- All seven strategies (contract, full, naive, greedy, beam, ga, hybrid) produce the paper's fields; only the
  search strategies have compared alternatives.
- API: repair → conflict → approval of the DECIDE card. The log shows the card `pending`, then `executed` /
  `approved` with who decided. The two disruptions have different IDs, and the E1 log lists its three affected
  tasks.

## Hybrid, Phase 9 — Scenario sets, stress profile, harness, dev runs and the freeze

### Team decisions (2026-11-01, recorded under `deviations`)
- **Split:** dev = the 10 existing templates, which were already explored in Phases 3 and 5. Held-out = 20 **new**
  templates (`eval/scenarios_v3/trips_heldout.yaml`), never run by any strategy before the freeze. This replaces the
  pre-registered stratified 9/21.
- **Stress set:** a larger option space plus combined two-event disruptions, because the realistic space (≤ 48
  plans) is below the 500-evaluation budget.

### What was built
- `eval/scenario_set_v3.py`: four scenario files (dev / held-out × realistic / stress) with 160 / 220 / 320 / 440
  scenarios. Dev-realistic is exactly the legacy 160 (tested).
  - The stress catalogue has the 16 single levels plus 6 combined disruptions per trip (`disruptions_stress.yaml`).
  - Stress IDs carry `__stress__`.
- **Stress option profile** (`STRESS_PROFILE`), off by default:
  - `MockFlights.offer_count` (3 → 8). The extra offers are all dearer, so planning still books the same flight
    (tested).
  - `MockPlaces.extra_hotels` (+5 guesthouses).
  - Two more pickup timings per direction. The default k = 4 still reaches the same first four.
  - Search k for stress = 8 / 8 / 6 / 3.
- **Combined disruptions in the search:** `RepairSearch(..., also=)`.
  - Every invalidated target joins the region and every facts target expands and is fixed.
  - The objective excludes all targets, and the GA's genes reach from all of them.
  - A single event is unchanged: the ground truth recomputed exactly, so its source hashes were re-stamped with
    `--rehash` and the new sha recorded as a deviation. E1's golden snapshot is unchanged.
- **Ground truths for all four sets**, with no truncation anywhere:
  - dev-stress: 220 scenarios; 20–21 per drift setting have more than 500 complete repairs (max 3,912);
  - held-out realistic and held-out stress: generated before the freeze, and only their integrity was inspected.
- **`eval/stats.py`** (standard library only):
  - exact / normal Wilcoxon signed-rank with Pratt zeros;
  - exact sign test;
  - Holm adjustment;
  - rank-biserial effect size;
  - trip-cluster bootstrap CIs;
  - Pareto comparison.
- **`eval/experiments.py`:** every arm on every scenario × drift (× seed), scored identically: validation, conflict,
  silent failure, the four terms, scope vs the ground truth, requirement satisfaction, legacy P/R, and cost. Outputs
  go to `eval/results_v3/`, never to `results/` or `results_v2/`. The held-out split refuses to run unless the
  pre-registration is frozen and its self-excluding digest verifies.
- **`eval/report_v3.py`:** the pre-registered H1–H4 tests, Holm, the GA verdict, secondary analyses and per-arm
  tables.
- **`eval/freeze_preregistration.py`:** fills in the trip lists, tuned widths and all ground-truth hashes, refuses
  unless every ground truth verifies, sets `frozen`, and records the digest. It is tested on a copy.
- **Pre-registration additions, written before any result was read:**
  - the tuning rules (W* and the H3-matched E2′);
  - the exact test for each hypothesis, and the Holm family;
  - drift on as the primary setting;
  - the "large space" secondary subset.
- **Bug fixed:** `stats.wilcoxon` returned no `zeros` field when every difference was zero, so the report crashed
  on all-ties. A synthetic-data test found it.

### Tuning and dev results (tuning data only; not evidence for any claim)
- **Beam width (pre-registered rule):** W* = **10**. Mean feasible fitness by width: 1: −0.3248, 3: −0.3211,
  5: −0.3227, 10: −0.3195. Fitness is not monotone in width.
- **H3-matched E2′ = width 50:** the largest width whose dev-stress evaluations (23.7) do not exceed E4's (33.1).
- **Full dev run:** 15,960 runs (9 arms, 380 scenarios, 5 seeds for E3/E4/RS, drift on and off), none timed out.
  Results are in `eval/results_v3/dev/full/` and the analysis in `eval/results_v3/dev/ANALYSIS_DEV.md`.
- **Dev verdicts (drift on):**
  - **H1 holds:** E2 dominates E1g 43 times vs 5 (Holm p 8e-8).
  - **H2 does not hold:** E3 vs RS is 0 better / 4 worse / 206 tied.
  - **H3 does not hold:** E4 vs E2′ is 5 / 1 / 204, exact p 0.094, Holm 0.19.
  - **H4 holds:** E1 has 20 silent failures (the ten-hour delays), E2–E4 have none.
  - **GA not justified on dev**, including the large-space subset.
- **Two report bugs found on dev and fixed before the freeze** (recorded as deviations):
  - seed means of equal fitness differed by about 1e-17 and entered the Wilcoxon test as random-sign differences;
    differences are now rounded to 1e-9;
  - Holm p-values were rounded to 6 decimals and printed as 0.
- **One criterion changed before the freeze** (recorded as a deviation): H2/H3 now require "rank-biserial > 0"
  instead of "median Δ > 0". About 97 % of dev pairs tie exactly, so the median was always 0 and the hypotheses could
  never hold. Both rules give the same dev verdict.
- **Precision/recall:** filled only for E1/B1/B2 on the realistic catalogue, labelled legacy, to keep Table I
  reproducible. No v3 test or verdict uses them.
- **Checks:** 628 tests pass; the E1 golden snapshot, the manifest and all four ground truths verify.
- **Frozen (2026-11-02, on the team's go-ahead):** `eval/preregistration.yaml` is `status: frozen`, sha256
  `d2bd96c9808a15423fe408182cc9d50da4b4d13bb49b5fdae36748636bf57184`. The digest is computed with its own field
  blanked.
  - `frozen_values` records W* = 10, E2′ = 50 and the sha256 of all four ground truths. The split lists the 10 dev
    and 20 held-out trips.
  - `experiments.py --split heldout` now verifies this digest before it runs. Any later change must go under
    `deviations`.
  - The freeze test now turns its copy back into a draft first, because the real file is frozen. All 628 tests
    pass, and the E1 golden snapshot and the manifest are unchanged.
- **Next (Phase 10):** the single held-out run, then the held-out analysis.

## Hybrid, Phase 10 — The held-out run and final v3 results (2026-11-02)

### Run
- **One logical run against the frozen pre-registration** (digest `d2bd96c9…7184`, verified by every manifest).
  - Split into 16 processes: realistic/stress × drift on/off × 4 groups of 5 trips. `--seeds 20 --beam-width 10`.
  - The launcher is `eval/results_v3/heldout/RUN_COMMANDS.sh`. On dev, splitting by trip reproduced the
    single-process rows exactly (308/308).
  - E2′ (width 50) ran as 4 more processes.
- **Two launches.** In the first, the trip-group array was named `GROUPS`, a read-only bash built-in. Every main process
  ran 0 scenarios, so no held-out main result existed or was seen; the empty outputs were deleted. The E2′ processes
  don't use the array and are kept from that launch. The second launch ran the main processes only.
- **The machine slept during the run.** The logs jump by about 11 h. This affects latency and some safety timeouts.

### Verification
- **Complete:** 100,320 main rows plus 1,520 E2′ rows.
  - 760 scenarios per drift condition (320 realistic, 440 stress), all 20 held-out trips, 66 rows per scenario.
  - No missing or duplicate scenarios, arms or seeds.
  - Ground truths verify against the frozen hashes. The manifest and the E1 golden snapshot are unchanged.
- **62 runs hit the search safety timeout.** By arm: RS 33, E4 17, E3 11, E2 1; 0 on dev. All are flagged
  `timed_out/truncated` and kept.
  - 58 of the 60 feasible ones equal their other seeds' fitness. 2 ended infeasible, both in drift off.
  - Excluding the 22 affected drift-on scenarios leaves every verdict unchanged.
- **Legacy Table I:** the legacy benchmark still reproduces all 1,920 rows (every column except latency).

### Held-out verdicts (drift on, Holm over 6 tests; `eval/results_v3/heldout/ANALYSIS_HELDOUT.md`)
- **H1 holds:** E2@10 dominates E1g 77 times, is dominated 9 times (Holm p 8.0e-14).
- **H2 does not hold, and is significant the other way:** E3 vs RS is 3 better / 14 worse / 403 tied
  (r −0.645, Holm p 0.0105).
- **H3 holds:** E4@10 vs E2@50 is 11 / 0 / 409 (r +1.0, mean Δ +0.0042 [0.0007, 0.0084], Holm p 0.0020).
  - Caveat: on held-out, E4 used 32.0 full evaluations and E2′ 23.6. The width was matched on dev.
- **H4 holds:** E1 has 40 silent failures (`outbound_delay_600` in both catalogues); E2, E3 and E4 have 0
  (Holm p 9.1e-12 each).
- **GA not justified** by the pre-registered rule.
- **Drift off gives the same verdicts.**
- **Recommended system arm:** E2 at W* = 10.
- **Final report:** `eval/results_v3/HELDOUT_FINAL_REPORT.md`. Supplementary analyses are in
  `eval/results_v3/heldout/supplementary/`: dominance vs E1, optimality gap to EXH, timeout sensitivity and drift-off
  tests.

### Deviations found after the freeze (not in the frozen file, which stays unchanged)
- **D1 — safety timeout 60 s, not 2 s.** `eval/experiments.py:145` passes `timeout_s=60` to every search arm except
  EXH, while the pre-registration says `wall_clock_timeout_s: 2`.
  - It applied equally to dev and held-out. It was missed until the held-out analysis.
  - Under 2 s, thousands of runs would have been cut short in this parallel run.
- **D2 — the main run was launched twice** (see Run). No result was seen from the first launch.
- **D3 — the frozen pre-registration and the results are not committed**, as instructed. There is no third-party
  timestamp of the freeze.
- **D4 — pre-registered items not produced:**
  - not done: the weight sensitivity analysis (×0.5 / ×1.5), `api_requests_unique`, the preference term and the
    manual labels;
  - not produced by `report_v3.py`: dominance vs E1 (named as the primary metric) and the optimality gap to EXH. Both
    are computed as supplementary.
- **Next:** state D1–D4 in the paper. Decide whether to run the weight sensitivity analysis and the manual labelling
  before submission.

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
