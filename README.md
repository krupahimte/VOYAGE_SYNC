# VoyageSync

A dependency-aware, risk-tiered multi-agent travel planning system. A trip is a typed task graph whose edges
carry checkable contracts. After a disruption only the tasks whose contracts break are regenerated, a Risk Gate
decides what runs automatically and what needs the traveller's approval, and an evaluation harness measures how
precisely the system re-planned.

**Results** (160 scenarios, see [`eval/RESULTS.md`](eval/RESULTS.md)): compared with a full replan, it
regenerates **84 % fewer tasks** at the **same 100 % recall**, with replan precision **99.6 % vs 15.8 %**, plan
stability **88.8 % vs 31.0 %**, **95 % fewer LLM calls** and **89 % fewer API calls**. The unsafe-action rate is
**0 %** (0 of 306 T2 actions executed without approval).

---

## Project structure

```
voyageesync/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI application factory
│   │   ├── config.py        # Centralised settings (pydantic-settings)
│   │   ├── api/             # Route handlers
│   │   ├── core/            # Graph, contracts, scheduler, orchestrator, templates
│   │   ├── agents/          # Real agents (flight, hotel, itinerary, weather, budget) + mocks
│   │   ├── integrations/    # API clients: Duffel, Geoapify, Open-Meteo, Frankfurter, OSRM (+ fixtures)
│   │   ├── llm/             # Provider-agnostic LLM client (MockLLM + OpenAI-compatible)
│   │   ├── db/              # SQLModel engine, graph + user-memory repositories
│   │   └── schemas/         # Pydantic request/response schemas
│   ├── tests/
│   └── requirements.txt
├── eval/                    # Evaluation harness: scenarios (YAML), oracle, baselines, benchmark, results
├── frontend/
│   ├── src/
│   │   ├── components/      # React UI components
│   │   ├── config/          # API base URL configuration
│   │   └── types/           # Shared TypeScript types
│   ├── package.json
│   └── vite.config.ts
├── .env.example
├── .gitignore
└── README.md
```

---

## Prerequisites

- Python 3.11+ (tested on 3.14)
- Node 18+
- npm 9+

---

## Backend setup (PowerShell)

```powershell
# 1. Navigate to the backend directory
cd backend

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy env example (edit values if needed; mock mode needs no keys)
Copy-Item ..\.env.example .env

# 5. Start the development server
uvicorn app.main:app --reload --port 8000
```

The API will be available at http://localhost:8000.
Interactive docs: http://localhost:8000/docs.

---

## Backend tests (PowerShell)

```powershell
# From the backend/ directory
.venv\Scripts\python.exe -m pytest tests/ -v
```

---

## Frontend setup (PowerShell)

```powershell
# 1. Navigate to the frontend directory
cd frontend

# 2. Copy env example
Copy-Item .env.example .env

# 3. Install dependencies
npm install

# 4. Start the development server
npm run dev
```

The app will be available at http://localhost:5173.

---

## Mock vs live mode

`VOYAGESYNC_MODE=mock` (the default) runs every agent on recorded or synthetic data and a deterministic mock
LLM: no keys and no network. The tests, the demo and the benchmark all use it. `VOYAGESYNC_MODE=live` uses
Open-Meteo, Frankfurter and OSRM (no keys needed), plus Duffel (test mode) and Geoapify when their keys are set
in `.env`, and any OpenAI-compatible LLM when `VOYAGESYNC_LLM_API_KEY` is set. A missing key or a failing call
falls back to mock data with a warning. Bookings always go through our own sandbox after approval.

---

## Run the benchmark (PowerShell)

```powershell
# from the repo root; extra packages for the charts
backend\.venv\Scripts\python.exe -m pip install -r eval\requirements.txt
# (re)generate the 160 scenarios from trips.yaml × disruptions.yaml
backend\.venv\Scripts\python.exe eval\generate_scenarios.py
# run ours, B1, B2 and the 3 ablations, with and without market drift (~10 s)
backend\.venv\Scripts\python.exe eval\run_benchmark.py
```

Outputs go to `eval/results/`: `results.csv`, `results_no_drift.csv`, `summary.json`, `summary.md` and the
charts (`*.png`). The write-up is [`eval/RESULTS.md`](eval/RESULTS.md).

The seven-metric evaluation (executability, constraint satisfaction, time & cost efficiency, latency, task
preservation, replanning scope, recovery success) writes to `eval/results_v2/` only and never touches the files above:

```powershell
backend\.venv\Scripts\python.exe eval\run_benchmark.py --metrics v2
```

Report: [`eval/results_v2/METRICS_REPORT.md`](eval/results_v2/METRICS_REPORT.md).

---

## Frontend production build (PowerShell)

```powershell
cd frontend
npm run build
```

Build output is written to `frontend/dist/`.

---

## Environment variables

### Backend (`.env` in `backend/`)

| Variable | Default | Description |
|---|---|---|
| `DEBUG` | `false` | Enable SQLAlchemy SQL echo |
| `DATABASE_URL` | `sqlite:///./voyagesync.db` | SQLModel connection string |
| `VOYAGESYNC_MODE` | `mock` | `mock` = deterministic mocks, no keys/network; `live` = real providers (missing keys fall back to mock with a warning) |
| `VOYAGESYNC_LLM_API_KEY` | _(empty)_ | Key for any OpenAI-compatible chat-completions API |
| `VOYAGESYNC_LLM_MODEL` | `gpt-4o-mini` | Model name |
| `VOYAGESYNC_LLM_BASE_URL` | `https://api.openai.com/v1` | API base URL (OpenAI, Gemini OpenAI endpoint, Groq, …) |
| `VOYAGESYNC_LLM_MAX_ATTEMPTS` | `3` | LLM decomposition attempts before the template-graph fallback |
| `DUFFEL_ACCESS_TOKEN` | _(empty)_ | Duffel **test-mode** token for flight search (live mode) |
| `GEOAPIFY_API_KEY` | _(empty)_ | Geoapify Places key for hotels and attractions (live mode) |
| `VOYAGESYNC_HTTP_TIMEOUT_S` | `20` | Timeout for external API calls |
| `VOYAGESYNC_RISK_SPEND_THRESHOLD` | `250` | Risk Gate: spend (trip currency) above which the cost term of the risk score saturates; any spend is T2 anyway |
| `VOYAGESYNC_APPROVAL_REMIND_MINUTES` | `30` | Risk Gate: an unanswered approval is held and the user reminded after this long (never auto-executed) |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Allowed CORS origins |

No API keys are required in mock mode. Never commit `.env`.

In live mode, Open-Meteo (weather, geocoding), Frankfurter (FX) and OSRM (routing) need no key.
Duffel and Geoapify are used only when their key is set; otherwise, or when a live call fails,
the agent logs a warning and uses mock data.

### Recording API fixtures

Mock mode replays recorded responses from `backend/app/integrations/fixtures/`. To refresh them
(keyless APIs always; Duffel and Geoapify when keys are set):

```powershell
cd backend
.venv\Scripts\python.exe scripts\record_fixtures.py
```


### Frontend (`frontend/.env`)

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend API base URL |

---

## Try a disruption (backend running on port 8000)

```powershell
# plan the demo trip
$trip = Invoke-RestMethod -Method Post -Uri http://localhost:8000/trips -ContentType 'application/json' -Body '{"destination":"Santorini","departure_city":"Mumbai","departure_date":"2027-06-14","return_date":"2027-06-19","travelers":1,"budget":4000,"currency":"USD","travel_style":"luxury","interests":["relaxation","food"]}'
# outbound flight delayed 5 h → only the transfer, day 1 and the budget are regenerated
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/trips/$($trip.trip_id)/disruptions" -ContentType 'application/json' -Body '{"scenario":"delay_5h"}'
```

Other scenarios: `GET /disruptions/presets`. Add `"strategy": "full"` (B1) or `"naive"` (B2) to compare
with the baselines, or `"use_frozen_context": false` for the ablation.

## Approve bookings (Risk Gate)

Planning proposes booking both flights and the hotel as **T2** actions; nothing is booked until you approve.

```powershell
# pending approval cards (option, price, what changes, why)
$cards = Invoke-RestMethod "http://localhost:8000/approvals?trip_id=$($trip.trip_id)"
# approve one (or "reject")
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/approvals/$($cards[0].action_id)" -ContentType 'application/json' -Body '{"decision":"approve"}'
# all gate actions of the trip: T0 drafts, T1 notifications (undo with POST /actions/{id}/undo), T2 approvals
Invoke-RestMethod "http://localhost:8000/trips/$($trip.trip_id)/actions"
```

Live updates (node status, replans, approvals, T1 notifications) stream on `ws://localhost:8000/ws/trips/{trip_id}`.

## Run the demo in the browser

With both servers running, open http://localhost:5173:

1. **Plan:** fill in the form (Santorini, from Mumbai, 14–19 Jun 2027, 4000 USD, Luxury, Food + Relaxation) and
   click **Create trip**. The task graph builds and three approval cards appear.
2. **Approve:** click **Approve all**. Sandbox bookings are confirmed (toasts show the booking refs).
3. **Disrupt:** under *Simulate disruption*, click **Outbound flight delayed 5 h**.
4. **Watch:** the flight turns red, three nodes turn amber then green, and the hotel stays grey. A toast says
   "Rebooked pickup for 19:30 (T1)" with **Undo**. Hover an arrow to see its contract.
5. **Escalate:** click **Outbound flight cancelled**. An approval card shows the replacement flight and the price
   difference; nothing is booked until you approve.
6. **Measure:** the *Replan metrics* panel compares this replan with a full replan (B1): precision, recall,
   stability, latency and cost.

The last trip is restored after a page reload. Switch the strategy to *B2 naive* or *B1 full* to see the
baselines re-plan the real trip.

## Current status — complete (Phases 0–8)

| Component | Status |
|---|---|
| Task Dependency Graph, contracts, scheduler | ✅ Phases 1–2 |
| Orchestrator (LLM decomposition, retry, template fallback) | ✅ Phase 3 |
| `POST /trips`, `GET /trips/{id}/graph`, `GET /trips/{id}/itinerary` | ✅ Phase 3 |
| User profile & memory | ✅ Phase 3 |
| Create-trip button → itinerary preview | ✅ Phase 3 |
| Real agents (Duffel, Geoapify, OSRM, Open-Meteo, Frankfurter) behind mock mode | ✅ Phase 4 |
| Disruption Monitor + contract-checked partial re-planning (+ B1/B2 baselines) | ✅ Phase 5 |
| Risk Gate (T0/T1/T2), approvals API, T1 undo, WebSocket live updates, sandbox booking | ✅ Phase 6 |
| React Flow demo UI: graph, disruption panel, approval cards, T1 toasts with undo, metrics vs B1 | ✅ Phase 7 |
| Evaluation harness: 160 scenarios, oracle (71/71 hand labels agree), B1/B2, 3 ablations, market drift, CSV, charts | ✅ Phase 8 |

See `PROGRESS.md` for details.
