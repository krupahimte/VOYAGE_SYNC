# VoyageSync: A Dependency-Aware, Risk-Tiered System for Adaptive Travel Planning with Search-Based Repair Optimization

**Ashita Patel, Krupa Himte, Risha Deshmukh, Mahima Acharekar**
Department of Computer Engineering, Dwarkadas J. Sanghvi College of Engineering, Mumbai, India

---

## Abstract

Travel itineraries coordinate transport, accommodation, transfers, activities and budgets. A disruption such as a delayed flight or an overbooked hotel usually invalidates only part of the plan, yet many planning approaches regenerate the whole itinerary or leave the repair to the traveller.

VoyageSync models an itinerary as a directed task-dependency graph whose edges carry explicit dependency contracts. It applies **contract-aware partial replanning**: starting from the disrupted task, it propagates replanning only along edges whose contracts are violated, and every consequential action passes a three-tier Risk Gate.

We extend this design with **search-based repair optimization**. Specialised agents generate alternative candidates for each affected task. Every complete repair is validated against seven hard constraints and scored by a four-term objective. A search policy selects the repair: greedy, beam search, random search, a genetic algorithm (GA), a beam-seeded GA hybrid, or exhaustive enumeration as the optimality reference.

We evaluate the system in three stages, all in a deterministic offline mock mode:

1. **v1** compares contract-aware replanning with naive downstream replanning on 160 disruption scenarios. Precision rises from 42.7 % to 99.6 % at 100 % recall, and plan stability from 55.4 % to 88.8 %.
2. **v2** extends the evaluation to operational metrics. Task preservation is 99.95 %, against 78.05 % and 59.87 % for the two baselines, at equal plan validity.
3. **v3** is a pre-registered comparison of the repair-search policies on 760 held-out scenarios from 20 unseen trips. In it:
   - beam search Pareto-dominates greedy repair (77 vs 9 scenarios, Holm-adjusted p = 8.0 × 10⁻¹⁴);
   - validation removes all 40 silent failures of the original replanner;
   - the GA does **not** outperform random search; random search is significantly better.

We therefore recommend contract-pruned beam search with width 10. It matches the exhaustive optimum in 100 % of feasible realistic scenarios and 86.7 % of feasible stress scenarios, at about 5.7 plan evaluations per repair.

**Index Terms:** adaptive travel planning, task dependency graph, partial replanning, dependency contracts, beam search, genetic algorithm, human-in-the-loop, risk-tiered autonomy.

---

## I. Introduction

A travel itinerary is a coordinated plan rather than a flat list of recommendations.
- The outbound flight's arrival time constrains the airport transfer.
- The transfer constrains when the first day's activities can begin.
- The hotel's check-in policy constrains the arrival.
- Every booked item contributes to a budget.

Planning a trip is therefore a constraint-satisfaction problem over interdependent decisions. The problem is also **dynamic**: flights are delayed or cancelled, hotels are overbooked, prices change, and weather closes outdoor venues after the plan has been made and partly booked.

A single disruption propagates through these dependencies, but usually not through all of them.
- A five-hour flight delay may require a new airport pickup and a shorter first day, while the hotel stays valid because its check-in window still holds.
- Regenerating the entire itinerary discards choices the traveller still wants, triggers unnecessary re-bookings and costs extra provider and model calls.
- Editing only the disrupted item can leave dependent items inconsistent.

The system must determine *which* tasks have become invalid and preserve the rest.

Identifying the affected tasks is only half of the repair. Once the stale tasks are known, each usually admits several replacements: another flight, a different pickup time, an alternative day plan. These choices interact, because a cheaper flight can arrive too late for the planned pickup and shorten the first day. **Choosing the replacement** is a combinatorial optimization problem that is separate from **identifying the affected tasks**. A repair should also never be committed if it violates a hard constraint; when no valid repair exists, the traveller should be told rather than handed a plan that fails on arrival.

VoyageSync addresses both problems:
- a task-dependency graph with explicit contracts;
- contract-aware partial replanning;
- a risk-tiered approval gate;
- a candidate-based repair-search layer with hard-constraint validation and explicit conflict reporting.

This paper revises and extends our earlier description of the system [1] (*our original four-page paper; see PAPER_AUDIT.md*). The hybrid search extension proposed there has since been implemented and evaluated.

**Contributions:**
1. **A dependency-aware task representation of travel plans.** A deterministic, HTN-style decomposition produces a directed acyclic graph of typed tasks, and its edges carry executable dependency contracts (Section IV).
2. **Contract-aware partial replanning.** A contract-pruned, level-ordered traversal regenerates a dependent task only when the contract on its incoming edge is violated, and preserves all others (Section IV).
3. **Risk-tiered execution.** A Risk Gate classifies every consequential action as autonomous (T0), notify (T1) or approve (T2), and never executes a T2 action without explicit approval (Section IV-E).
4. **Search-based repair optimization.** The pieces are:
   - candidate generation by the task agents;
   - a seven-constraint validator;
   - a four-term repair objective;
   - six search policies over the same contract-pruned repair space;
   - atomic commit, explicit conflict reporting and a decision log (Section V).
5. **A three-stage evaluation.** It ends in a pre-registered, held-out statistical comparison of the search policies, including negative results for the GA (Sections VI–VII).

---

## II. Related Work

> *Note on citations.* The references [2]–[10] are retained from the original paper and must be checked against their published versions before submission. Entries marked **[CITATION NEEDED]** are places where the literature should be cited but no verified reference is in our materials. Candidate references to look up are listed in PAPER_AUDIT.md §7. No reference has been added that we have not verified.

**Agentic task decomposition and travel assistants.**
- TDAG [2] decomposes complex tasks through a main agent that dynamically generates sub-agents, and evaluates travel-planning tasks with fine-grained scoring.
- ReAct [3] interleaves reasoning traces with tool-using actions.
- TravelAgent [4] focuses on personalized itinerary generation.
- Work on delegated travel agents [5] studies preference elicitation and agent behaviour for full delegation.
- Tourism recommender models [6], [7] predict suitable destinations, and information technology has long shaped tourism management [8].

These systems generate or recommend plans. They do not maintain a live dependency graph of an already-booked itinerary in order to decide which parts of it a disruption invalidates. TDAG's published scores are not VoyageSync results. A direct comparison would require TDAG's exact scoring definitions and a comparable protocol, which we have not applied.

**Hierarchical task networks and automated planning.**
- VoyageSync's decomposition is in the style of hierarchical task networks (HTN): a compound goal (*plan a trip*) is refined into primitive tasks by fixed methods. **[CITATION NEEDED: HTN planning]**
- Its dependency graph is a DAG ordered topologically. **[CITATION NEEDED: Kahn's algorithm, topological sorting]**

**Plan repair and partial replanning.**
- Classical planning distinguishes replanning from scratch from *plan repair*, which modifies an existing plan to fit changed conditions. **[CITATION NEEDED: plan repair / replanning literature]**
- Our contract-pruned traversal is a form of plan repair driven by explicit, executable edge conditions rather than by a planning-domain model.

**Search and optimization.**
- Beam search keeps a bounded set of the best partial solutions. **[CITATION NEEDED: beam search]**
- Genetic algorithms evolve candidate solutions through selection, crossover and mutation. **[CITATION NEEDED: genetic algorithms]**
- Constrained evolutionary optimization often uses Deb's feasibility rules to rank feasible solutions above infeasible ones. **[CITATION NEEDED: Deb's constraint-handling rules]**
- Tree of Thoughts explores reasoning branches proposed and evaluated by a language model. **[CITATION NEEDED: Tree of Thoughts]** Our search is deterministic and uses no language model, so we call it beam search, not Tree of Thoughts.

**Risk-aware autonomy and human-in-the-loop.** Systems that act on a user's behalf must decide which actions may run autonomously and which require consent. **[CITATION NEEDED: human-in-the-loop / adjustable autonomy]** VoyageSync makes this decision explicit through a scored, rule-overridden risk tier.

**Statistical methodology.** We use:
- the Wilcoxon signed-rank test with Pratt's zero handling **[CITATION NEEDED]**;
- the exact sign test;
- Holm's step-down correction **[CITATION NEEDED]**;
- the rank-biserial correlation **[CITATION NEEDED]**;
- cluster bootstrap confidence intervals **[CITATION NEEDED]**.

**Gap.** No system we reviewed combines all of the following for travel itineraries:
- identification of the tasks a disruption invalidates, through executable dependency contracts;
- preservation of every other task;
- search over validated candidate repairs;
- explicit reporting when no valid repair exists;
- approval gating of consequential actions.

---

## III. System Architecture

VoyageSync is a Python backend with a web frontend (Fig. 1).

**Backend stack:**
- FastAPI, with Pydantic models and Uvicorn.
- SQLite through SQLModel/SQLAlchemy for persistence. This replaces the PostgreSQL store described in [1].
- FastAPI WebSockets, which push live trip updates to the client.

**Frontend stack:** React with TypeScript, built with Vite and styled with Tailwind CSS. It renders the task graph with React Flow and uses Lucide icons.

**LLM client:**
- An LLM client abstraction offers a provider-agnostic, OpenAI-compatible chat-completions client.
- A deterministic **MockLLM** is the default offline mode.

**Integrations:** live provider integrations exist for:
- flights (Duffel);
- places and hotels (Geoapify);
- weather and geocoding (Open-Meteo);
- currency (Frankfurter);
- routing (OSRM).

They have not been validated in this work: credentials and live connectivity were not verified, and all results are in mock mode.

> **[FIGURE 1 — placement: start of Section III]**
> *Caption:* **Fig. 1.** VoyageSync architecture.
> - The React frontend talks to the FastAPI backend over REST and a WebSocket.
> - The **Orchestrator** decomposes a trip goal into a task graph. The **Scheduler** executes it through the **Agent Registry** (six worker agents) over the **Provider bundle** (mock or live).
> - On a disruption, the **Disruption Monitor** applies the event and calls the **Replanner**. The Replanner either propagates contracts (E1, B1, B2) or invokes the **Repair Search** module (candidate generation → validator → objective → search policy → atomic commit or conflict).
> - Every consequential change passes the **Risk Gate** (T0/T1/T2) and its action ledger.
> - Graphs, disruptions and approvals are persisted in **SQLite**.
>
> *Draw only these components:* there is no authentication service and no background scheduler.

**A. Agents.** Six specialised worker agents produce task outputs: Flight, Hotel, Transfer, Day Plan, Weather and Budget.
- They are Python classes that share one interface (`BaseAgent`).
- They are registered by task type in an agent registry, read their inputs from the shared graph state, and call the provider bundle.
- They are **not** six independent LLMs. In mock mode, the only model-like component is the template-based MockLLM. It is used for the decomposition request and for day-plan activity descriptions.

**B. Task-dependency graph.** A trip is a DAG $G=(T,E)$ of typed tasks (Section IV-A). Each edge $(t_i,t_j)\in E$ means $t_j$ depends on $t_i$, and it carries a **contract**: a named, executable condition over the two tasks' outputs, such as *transfer after flight* or *stay covers day*. The graph store validates acyclicity and provides a deterministic topological order.

**C. Scheduler, Replanner and Repair Search.**
- The Scheduler executes tasks in dependency order with bounded concurrency and retries.
- The Replanner handles a disruption with one of the strategies in Table VII.
- The repair-search module (Section V) implements every strategy that chooses among candidate repairs.

**D. Risk Gate.** Every action with consequences is classified before it takes effect (Section IV-E). T2 actions wait in an approval queue, which the frontend shows as approval cards.

**E. Persistence and decision retrieval.** Trip graphs, disruption records and approval actions are stored in SQLite. The decision log of any repair can be retrieved through `GET /trips/{trip_id}/disruptions/{event_id}/decision` (Section V-I).

---

## IV. Dependency-Aware Planning and Partial Replanning

This section describes the replanning architecture that was already implemented in [1] and evaluated in v1 and v2. Section V describes the new extension.

### A. HTN-style decomposition

A trip request contains origin, destination, dates, travellers, budget, currency, interests and preferences. It is decomposed into a fixed task set: `propose_template_tasks()` refines *PlanTrip* into

$$T = \{\text{flight}_{out}, \text{flight}_{ret}, \text{stay}, \text{transfer}_{in}, \text{transfer}_{out}\} \cup \{\text{weather}_d, \text{dayplan}_d\}_{d=1}^{D} \cup \{\text{budget}\},$$

where $D$ is the number of trip days and each day plan is themed on one of the traveller's interests.
- In mock mode, the MockLLM returns this template plan, so decomposition is deterministic and rule-based. It is HTN-style, but no language model decomposes the trip.
- A proposal from any model is validated against the expected task structure, and the system falls back to the template on failure.
- Task inputs and dependency edges are always derived deterministically from the goal, never from a model.

### B. Dependency graph and contracts

Edges are created by fixed templates. Each one carries one of nine contract types:

| Contract | From → to | Condition |
|---|---|---|
| `FLIGHT_BEFORE_CHECKIN` | outbound flight → stay | the arrival fits the stay's check-in |
| `TRANSFER_AFTER_FLIGHT` | outbound flight → arrival transfer | the pickup comes after the arrival |
| `TRANSFER_SERVES_HOTEL` | stay → transfers | the transfers go to and from the booked hotel |
| `TRANSFER_BEFORE_DAY_START` | arrival transfer → day 1 | the drop-off comes before the day plan starts |
| `STAY_COVERS_DAY` | stay → each day | the stay covers that day |
| `WEATHER_SUITS_PLAN` | weather → each day | the plan suits the forecast |
| `TRANSFER_BEFORE_DEPARTURE` | return flight → departure transfer | the pickup leaves enough buffer before the flight |
| `DAY_ENDS_BEFORE_PICKUP` | last day → departure transfer | the last day ends before the pickup |
| `BUDGET_ITEM_CURRENT` | each cost item → budget | the budget reflects that item's current cost |

A contract evaluates to *holds*, *violated* or *not evaluable* (an invalid input).

> **[FIGURE 2 — placement: Section IV-B]**
> *Caption:* **Fig. 2.** Task-dependency graph of a three-day trip, with the contract type on each edge. Flights, stay and transfers form the backbone. Each day plan depends on the stay, its day's weather and, on day 1, the arrival transfer. The departure transfer depends on the return flight, the stay and the last day plan. Every cost-bearing task feeds the budget.

### C. Topological order and depth

The graph store computes a deterministic topological order with **Kahn's algorithm**:
1. Compute in-degrees.
2. Repeatedly remove zero-in-degree nodes.
3. Report a cycle if any node remains.

Task depth is the **longest-path depth** from the sources, computed in topological order:

$$\operatorname{depth}(t) = \begin{cases} 0 & \text{if } t \text{ has no parent} \\ 1 + \max_{(p,t)\in E} \operatorname{depth}(p) & \text{otherwise.} \end{cases}$$

Because every parent of $t$ has a smaller depth, processing tasks level by level settles all parents before their child is considered.

### D. Contract-aware partial replanning

After a disruption changes task $t_0$ (for example, a delayed arrival time), the **contract** strategy (E1) proceeds as follows.

1. **Check outgoing contracts.** Evaluate the contracts on the outgoing edges of $t_0$. A child becomes **stale** only if the contract on its edge is violated. If the contract holds, the child is preserved.
2. **Regenerate level by level.** Process depth levels in increasing order. At each level, regenerate the stale tasks with their agents. Each agent receives *frozen context*: its own previous output and the outputs of neighbours that are not stale.
3. **Re-check and stop.** Re-check the outgoing contracts of each regenerated task against its new output. A violation marks the next child stale. Propagation stops wherever contracts hold.

This is a **contract-pruned, BFS-style traversal** in depth order. It is *not* a breadth-first search that marks every descendant: a descendant is reached only through a chain of violated contracts. Because of the depth ordering, each task is regenerated at most once.

Two baselines use the same interface:
- **B1 (full replan):** regenerates every task except the disruption facts, without frozen context.
- **B2 (naive downstream):** marks every descendant of $t_0$ stale without checking contracts. The descendants are found by depth-first traversal.

> **[FIGURE 3 — placement: Section IV-D]**
> *Caption:* **Fig. 3.** Contract-aware partial replanning after a five-hour outbound delay.
> - Marked stale: `TRANSFER_AFTER_FLIGHT` is violated, so the arrival transfer is regenerated; its new drop-off violates `TRANSFER_BEFORE_DAY_START`, so day 1 is regenerated.
> - Preserved: `FLIGHT_BEFORE_CHECKIN` still holds, so the stay is preserved and nothing downstream of it is touched.
> - B2, for comparison, would mark every descendant of the flight stale.

### E. Risk-tiered execution

Every proposed action is assigned a tier.

| Tier | Meaning | Examples |
|---|---|---|
| **T0, autonomous** | Executes at once | search, compare, weather, draft plans |
| **T1, notify** | Executes, then the traveller is notified, with an undo option | small, reversible changes such as rescheduling a pending transfer |
| **T2, approve** | Pauses until the traveller decides explicitly | book, change, cancel, pay, any spending, non-refundable items, and the repair-conflict decision of Section V-H |

**Hard rules come first.** Book, change, cancel and pay are always T2, and money is never spent in T0 or T1.

**Otherwise a risk score decides:**

$$r = 0.4\,\text{irreversibility} + 0.3\,\min\!\left(1, \tfrac{\text{spend}}{250}\right) + 0.2\,\text{visibility} + 0.1\,(1-\text{confidence}),$$

with T1 for $r \ge 0.15$ and T2 for $r \ge 0.6$. The default spend threshold of 250 is configurable.

**A T2 action never executes without explicit approval.** It does not run after a timeout (the offer is held and the user reminded), after a retry, or after a replan. A pending approval whose offer or price has changed is superseded. Execution is guarded twice:
1. the gate re-checks that the node still carries the approved offer at the approved price;
2. the booking provider refuses any call without a signed, single-use approval grant.

Bookings in the current implementation are made with a sandbox booking provider: a mock provider that issues confirmation references. No payment data is handled, and redirecting the traveller to a vendor checkout, as described in [1], is not implemented.

> **[FIGURE 5 — placement: Section IV-E]**
> *Caption:* **Fig. 5.** Risk Gate decision process.
> 1. Hard rules (book, change, cancel, pay, spending, non-refundable, repair conflict) assign T2 directly.
> 2. Otherwise, the risk score $r$ assigns T0 ($r<0.15$), T1 ($0.15\le r<0.6$) or T2 ($r\ge0.6$).
> 3. T0 executes; T1 executes and notifies, with an undo option; T2 waits for approve or reject. Approval issues a single-use grant to the executor.

---

## V. Search-Based Repair Optimization

The contract strategy of Section IV-D always takes each agent's first choice. The repair-search extension keeps E1's contract-pruned region rule unchanged. On top of it, the system chooses **among candidate repairs**, validates every candidate, and reports when no valid repair exists.

> **[FIGURE 4 — placement: start of Section V]**
> *Caption:* **Fig. 4.** The candidate-based repair workflow:
> 1. a disruption event is applied;
> 2. the affected tasks are identified by contract checks (the region rule);
> 3. the agents generate $k$ candidates per region task;
> 4. a search policy assembles complete repairs;
> 5. the seven hard constraints are validated and the four-term objective scored;
> 6. the best feasible repair is committed atomically, or a conflict with a best-effort plan is raised as a T2 decision;
> 7. the decision log is recorded.

### A. Workflow

1. **Disruption detection.** The Disruption Monitor applies the event (a delay, cancellation, overbooking, storm or price change) to the graph. Delays and storms are *facts*: the disrupted task is not re-decided. Cancellations and overbookings require a new choice.
2. **Affected tasks.** The repair begins from the disrupted task and the same contract-pruned region rule as E1.
3. **Candidate generation.** For each region task, the task's agent returns up to $k$ distinct candidates through `candidates(node, inputs, frozen_context, k)`:
   - alternative flight offers, ranked as in E1;
   - alternative hotels;
   - alternative pickup timings for transfers;
   - day-plan variants.

   Option 0 is always E1's choice. Candidates are cached by task, $k$, resolved inputs and frozen context.
4. **Repair search space.** A *search state* is a partial repair: the outputs chosen so far, the current region, and the region tasks still to decide, processed in depth order. Choosing an option for a task:
   - **prunes** the option if one of its incoming contracts with a settled parent is violated;
   - **adds a child to the region** if the option changes the task's output and breaks an outgoing contract to a frozen child (E1's rule). If that child is a disruption fact that may not be re-decided, the option is pruned instead.

   Tasks outside the region are never assigned an output, so they stay frozen by construction.
5. **Validation:** see Section V-B.
6. **Objective evaluation:** see Section V-C.
7. **Search:** see Section V-D.
8. **Conflict handling:** see Section V-H.
9. **Approval and atomic commit:** see Section V-G.
10. **Decision logging:** see Section V-I.

### B. Hard constraints

A complete repair is **feasible** only if all seven checks pass. No search policy relaxes them.

| # | Constraint | Requirement |
|---|---|---|
| C1 | Contracts | every edge contract holds and is evaluable |
| C2 | Schemas | every repaired output passes its agent's output schema |
| C3 | Frozen tasks | every task outside the repair region is exactly as before |
| C4 | Requirements | the trip dates and party size are respected |
| C5 | Exclusions | no cancelled offer and no overbooked hotel is reused |
| C6 | Activities | every repaired day plan contains an activity, unless the arrival makes that impossible (day 1, when the drop-off plus 60 minutes, rounded up to the next quarter hour, is past midnight) |
| C7 | DAG | the graph remains acyclic |

The budget is deliberately a **soft** constraint: it is a term of the objective, not a feasibility condition.

### C. Objective function

Let $P$ be the plan before the repair and $P'$ the repaired plan. Each term is scaled so that one unit is comparable: a whole plan, or one whole budget.

**Disruption.** The mean change over the user-visible tasks $V$ (flights, stay, transfers, day plans), excluding the disrupted task(s):

$$\text{disr} = \frac{1}{|V|}\sum_{v\in V} \delta(v), \qquad \delta(v)=\begin{cases}0 & \text{unchanged}\\ 1 & \text{another flight or hotel}\\ \min\!\left(1,\tfrac{|\Delta\,\text{pickup}|}{180\text{ min}}\right) & \text{transfer, same hotel}\\ 1-\tfrac{|\text{kept activities}|}{|\text{activities before}|} & \text{day plan}\end{cases}$$

**Money.** The cost change relative to the budget limit $L$, with new overspend counted twice:

$$\text{money} = \frac{C' - C}{L} + 1.0\cdot\frac{\max\!\big(0,\ \max(0,C'-L) - \max(0,C-L)\big)}{L}.$$

**Activity time lost.** Over all day plans $d$, with planned activity minutes $m_d$:

$$\text{atl} = \frac{\sum_d \max(0,\ m_d - m'_d)}{\sum_d m_d}.$$

**Approvals.** The share of booked flights and stays whose offer changes, each of which needs a new T2 approval:

$$\text{appr} = \frac{|\{b \in B : \text{booked}(b),\ \text{offer}'(b)\neq\text{offer}(b)\}|}{|B|}.$$

**Fitness** uses equal weights, fixed before any experiment:

$$f(P') = -\left(w_1\,\text{disr} + w_2\,\text{money} + w_3\,\text{atl} + w_4\,\text{appr}\right), \qquad w_i = 1.$$

**Ranking.** Plans are ranked by Deb's feasibility rule: any feasible plan first, then fewer violations, then higher fitness.

### D. Search policies

All policies share the same:
- candidate generator;
- region rule;
- validator;
- objective;
- budget of **500 full plan evaluations**, where one evaluation is a complete repair validated and scored.

Beam search also performs *partial evaluations* (partial fitness used for ranking); these are reported but not counted in the budget.

| Arm | Policy | Description |
|---|---|---|
| E1 | Contract | Original replanner (Section IV-D): option 0 everywhere. It receives a validation flag only. |
| E1g | Greedy | At each region task, takes the option with the best partial fitness (beam width 1) |
| E2 | Beam search | Keeps the $W$ best partial repairs by partial fitness and branches each over its next task's surviving options. Ties are broken by the choices made, so it is deterministic. $W^*=10$. |
| E2′ | Wider beam | E2 with $W=50$, the width matched to E4's evaluation count on the development split (used in H3) |
| RS | Random search | Uniform random repairs through the same decoder as the GA. Stops at the budget, after 20 × the budget in draws, or after 200 consecutive draws with no new repair. |
| E3 | Genetic algorithm | See Section V-E |
| E4 | Hybrid | Beam search ($W=10$) seeds the GA; see Section V-F |
| EXH | Exhaustive | Depth-first enumeration of every combination that survives the contract checks; the optimality reference |

**Why not Tree of Thoughts.** The original proposal [1] considered Tree of Thoughts for candidate exploration. The implemented search is deterministic: candidates come from the agents and are ranked by the objective, with no language model proposing or evaluating branches. It is therefore beam search, and we do not claim Tree of Thoughts.

### E. Genetic algorithm (E3)

The GA parameters were fixed in the pre-registration and not tuned.

**Representation:**
- **Chromosome:** one gene, an option index, per task the disruption can reach (the target, if it is re-decided, and its descendants).
- **Decoder:** shared with RS. For each region task in depth order, it takes the first option at or after the gene's index (wrapping around) that survives the contract checks. Genes of tasks outside the decoded region have no effect. A decoding with no surviving option is a *dead end*.

**Parameters:**

| Parameter | Value |
|---|---|
| Population | 30 |
| Generations | at most budget / population |
| Tournament size | 3 (on the Deb key; dead ends rank last) |
| Crossover | uniform, $p_c = 0.8$ |
| Mutation | per gene, a new random index with $p_m = 1/L$ |
| Elitism | the 2 best distinct chromosomes survive unchanged |
| Patience | stops after 10 generations without improvement |
| Initial population | option 0 everywhere (E1's choices), then random chromosomes |
| Dead-end charge | each distinct dead end costs one evaluation |

**Algorithm 1: GA repair search (as implemented)**
```
input: search space S, budget B, population N, tournament k, elitism e, patience q, seeds (hybrid only)
L ← number of genes; G ← ⌊B / N⌋
pop ← [option 0 everywhere] + seeds (at most N − 1) + random chromosomes, up to N
best ← none; stall ← 0
for g in 0 … G − 1:
    score every chromosome in pop by decoding it        // each distinct plan or dead end costs one evaluation
    if the best Deb key in pop beats best: best ← it; stall ← 0  else: stall ← stall + 1
    stop if the budget is spent, or stall ≥ q, or g = G − 1
    next ← the e best distinct chromosomes of pop
    while |next| < N:
        a ← tournament(pop, k); b ← tournament(pop, k)
        child ← uniform_crossover(a, b) with probability p_c, otherwise a copy of a
        replace each gene of child by a random option index with probability 1/L
        next.append(child)
    pop ← next
return the best feasible plan among all evaluated plans; if there is none, a conflict
```

### F. Beam + GA hybrid (E4)

1. Beam search ($W=10$) runs first.
2. The encodings of its best feasible plans, at most population / 3, seed the GA's initial population.
3. The beam's evaluations count against the same 500-evaluation budget.

The result is the best plan found by either stage.

### G. Atomic commit and approval

The chosen repair is written to a **deep copy** of the graph:
1. Region tasks are marked stale and then completed with their chosen outputs. A new day plan receives its activity descriptions at this point, which is the one MockLLM call per new plan.
2. The copy is validated again against all seven constraints.
3. Only if it passes does it replace the stored graph. On any failure, the stored graph is left exactly as it was.

Changed booked items then pass the Risk Gate as T2 change actions.

### H. Conflict reporting

When no feasible repair exists, or the commit fails, nothing is committed. The repair returns a **conflict** containing:
- the violated constraints;
- a best-effort plan (the best plan under the Deb key).

The conflict is raised as a T2 **DECIDE** action. The traveller may accept the best-effort plan explicitly; it is applied only if its content is unchanged since the decision was raised. Or the traveller may reject it and keep the current plan.

The original replanner (E1) does not report conflicts. Its result receives a validation flag, and an infeasible E1 plan committed without a flag is counted as a **silent failure**.

### I. Decision log

Each repair produces a decision log, retrievable per disruption. It records:
- the disruption;
- the affected tasks and violated contracts;
- the options considered, with their labels, up to 10 per task;
- the search statistics (evaluations, stopping reason);
- the selected repair and its validation result;
- the conflict, if any;
- the related approval actions.

---

## VI. Experimental Methodology

All experiments run **offline in deterministic mock mode**:
- mock providers;
- the template-based MockLLM;
- seeded market drift (with drift on, each re-search moves prices by up to ±8 % and makes an offer, hotel or place unavailable with probability 15 %, seeded per scenario so every strategy faces the same market).

No real LLM or network call is made; the runner forces mock mode. MockLLM "calls" are invocations of template code, and API calls are calls to mock providers.

The three stages answer different research questions and use different data, so their results are reported separately (Table VIII).

### A. v1: original baseline evaluation

**Question.** Does contract-aware partial replanning regenerate only the tasks that a disruption invalidates?

**Scenarios.**
- 10 trip templates: 2 domestic (India) and 8 international, 3–7 nights, 1–5 travellers, 5 currencies, budget to luxury.
- 16 disruptions per trip: outbound delays of 15 min to 10 h (6 levels), 2 return delays, outbound and return cancellations, an overbooked hotel, storms mid-stay and on the last day, hotel price rises of +10 % and +30 %, and a return fare rise of +60 %.
- 160 scenarios in total.
- Each trip is planned once and booked by a simulated user. Every strategy then replans an identical copy.

**Strategies.**
- Contract-aware replanning (*proposed*, E1).
- B1, full replan.
- B2, naive downstream.
- Three ablations: no contracts, no frozen context, no Risk Gate.

**Ground truth.**
- An oracle applies the event with full knowledge and drift off, then repeatedly regenerates the shallowest broken tasks until all contracts hold. Its regenerated set is $A$.
- 71 scenarios carry hand labels written from the domain rules before the oracle was run. All 71 agree with the oracle.

**Metrics.** For the set $R$ of tasks a strategy regenerates:

$$\text{Precision}=\frac{|R\cap A|}{|R|},\qquad \text{Recall}=\frac{|R\cap A|}{|A|},\qquad F_1=\frac{2PR}{P+R}.$$

- **Plan stability:** the share of user-visible items (flights, stay, transfers, day plans) the traveller sees unchanged, excluding the disrupted item.
- Average tasks regenerated, and LLM and API calls per replan.

### B. v2: expanded operational evaluation

**Question.** Beyond scope, does the replanner keep plans executable, satisfy constraints and user requirements, preserve unaffected tasks, and recover, and at what cost?

**Setup.**
- The same 160 scenarios and strategies as v1, with drift on (primary) and off.
- 960 runs per drift setting (160 scenarios × 6 strategies), of which 900 were replanned and 60 had no effect: 150 and 10 per strategy.
- A fresh run reproduced the v1 result files on every column except latency (0 mismatches).

**Notation.**
- $V$: all tasks of the trip.
- $t$: the disrupted task.
- $A$: the oracle's affected set.
- $R$: the tasks a strategy regenerated.
- $U = V\setminus A\setminus\{t\}$: the unaffected tasks.
- N/A: a zero denominator, or nothing checkable. Such runs are excluded from means, not imputed.

**Metric definitions** (as implemented in `eval/metrics_v2.py`):
1. **Executability.** Operationally defined; this is *not* TDAG's metric. The run-level checks are:
   - E1: every task is done or awaiting approval, with an output;
   - E2: every output passes its agent's validator;
   - E3: every contract is evaluable;
   - E4: every contract holds.

   A *plan-valid* run passes E1–E4. An *execution-ready* run is plan-valid with every task done. The task-level variants divide valid (respectively done) tasks by $|V|$.
2. **Contract satisfaction** is $\frac{\#\text{contracts holding}}{\#\text{contracts}}$. **User-requirement satisfaction** is $\frac{\#\text{met}}{\#\text{checkable}}$ over four requirements:
   - dates;
   - party size (rooms × 2 ≥ travellers);
   - budget (total ≤ budget);
   - interests (each day after arrival themed on a requested interest).

   **Requirement regression** is the share of runs where a requirement that held before the event fails after it.
3. **Resource usage** counts MockLLM invocations and provider API calls per replan. **Travel cost change** is $\frac{C_{after}-C_{before}}{L}$. **Activity-time retention** is $\frac{\sum m_{after}}{\sum m_{before}}$ over the day plans in $A$; it is N/A when $A$ contains no day plan.
4. **Replanning latency:** wall-clock time from the event to a valid plan (median and p95), in mock mode.
5. **Task Preservation Rate:** $\text{TPR}=\frac{|\{u\in U: u \text{ unchanged}\}|}{|U|}$.
6. **Replanning scope:** $\frac{|R|}{|V|}$.
7. **Contract recovery:** every contract holds and no task failed. **Strict recovery:** plan-valid and no requirement regression.

### C. v3: repair-search evaluation

**Question.** Given the contract-pruned repair region, which search over candidate repairs selects better valid repairs, and does validation prevent silent failures?

**Arms.** E1, E1g, E2 ($W=10$), E2′ ($W=50$), E3, E4, RS and EXH (Section V-D). The legacy B1 and B2 were also run and are reported in the analysis files.

**Split** (by trip). The dev split and the held-out split share no trip.
- **Development (dev):** the 10 v1/v2 trips, used only to tune the beam width and the H3 matched width. It is not evidence for any claim.
- **Held-out:** 20 new trip templates, never run before the frozen analysis.

**Catalogues.**
- **Realistic:** the 16 v1 disruptions per trip, with options per task of FLIGHT 3, STAY 3, TRANSFER 4, DAY_PLAN 3. This gives 320 held-out scenarios.
- **Stress:** the 16 disruptions plus 6 combined two-event disruptions per trip, over a larger option space (8 flight offers, 5 extra hotels, $k$ = 8 / 8 / 6 / 3). This gives 440 held-out scenarios.

  The stress catalogue exists because the realistic option space has at most 48 complete repairs, which is below the evaluation budget. A GA could not be meaningfully tested there.

**Seeds and drift.**
- 20 seeds per scenario for E3, E4 and RS, with $\text{seed}=\text{sha256}(0\,|\,\text{scenario}\,|\,\text{run})$. The other arms are deterministic.
- Drift on is the primary setting; drift off is secondary.

**Ground truth for scope.** Computed by exhaustive enumeration before any search run, and hash-protected:
- the *necessity set* $N$: the intersection of the repair regions of all feasible repairs;
- the *minimum scope*: the smallest feasible region.

From it:

$$\text{coverage}=\frac{|R\cap N|}{|N|},\qquad \text{excess}=|R|-\min|R_{\text{feasible}}|,\qquad \text{scope optimality}=\frac{\min|R_{\text{feasible}}|}{|R|}.$$

**Pre-registration.** Before the held-out run, a pre-registration fixed the following:
- the hypotheses, arms, budget, seeds, tests and decision rules;
- the tuned values ($W^*=10$, E2′ $=50$);
- the ground-truth hashes.

It was frozen with a SHA-256 digest that every held-out run verified. The held-out analysis was run once. Deviations are listed in Section IX.

**Tuning on dev.**
- $W^*$ is the smallest width in {1, 3, 5, 10} whose mean feasible dev fitness is within $10^{-6}$ of the best width's. It came out as 10.
- E2′ is the widest beam in {1, 3, 5, 10, 20, 50, 100} whose mean dev-stress evaluations do not exceed E4's. It came out as 50.

**Hypotheses** (held-out, drift on):
- **H1:** E2 Pareto-dominates E1g on the four objective terms more often than the reverse (both catalogues; scenarios where both are feasible).
- **H2:** E3 reaches higher fitness than RS at an equal evaluation budget (stress catalogue).
- **H3:** E4 reaches higher fitness than E2′ (stress catalogue).
- **H4:** E2, E3 and E4 commit no infeasible plan without a flag, while E1 does (both catalogues).
- **GA justified** if and only if H2 and H3 both hold.

**Statistics.**
- The unit of analysis is the scenario (seed means for the stochastic arms), paired across arms.
- **H1** and **H4** use an exact two-sided sign test: on dominates vs dominated for H1, and on scenarios where exactly one of the two arms fails silently for H4.
- **H2** and **H3** use an exact Wilcoxon signed-rank test with Pratt's zero handling on per-scenario fitness differences. Differences are rounded to $10^{-9}$, so floating-point noise counts as a tie.
- Effect size is the rank-biserial correlation $r$.
- Confidence intervals of mean differences are 95 % bootstraps (10,000 resamples) that resample whole trips.
- p-values are Holm-adjusted over the family {H1, H2, H3, H4 (E1 vs E2), H4 (E1 vs E3), H4 (E1 vs E4)}.
- H2 and H3 hold only if $r>0$ and the Holm-adjusted $p<0.05$.

**Secondary and supplementary analyses** (not in the Holm family):
- **Pre-registered secondary:** drift off; the large-space subset (more than 500 complete repairs).
- **Supplementary, added after the run:** the gap to EXH; Pareto dominance against E1; per-catalogue tests; exclusion of the scenarios that contain a timed-out run; Clopper–Pearson intervals.

> **[FIGURE 6 — placement: Section VI-C]**
> *Caption:* **Fig. 6.** v3 evaluation workflow.
> 1. The 10 dev trips go to tuning ($W^*$, E2′), and the values are frozen in the pre-registration (SHA-256).
> 2. The 20 held-out trips × 2 catalogues give 760 scenarios. Each is run by 9 arms (plus E2′), with 20 seeds for the stochastic arms.
> 3. One analysis run with `report_v3.py` produces H1–H4 with the Holm correction.
> 4. Supplementary analyses are kept separate.

---

## VII. Experimental Results

### A. v1: contract-aware replanning against the baselines

**TABLE I. v1 benchmark** (mock mode, 160 scenarios, drift on). ↑ = higher is better, ↓ = lower is better.

| Metric | Proposed (E1) | B2 naive downstream | B1 full replan |
|---|---:|---:|---:|
| Precision ↑ | **99.6 %** | 42.7 % | 15.8 % |
| Recall ↑ | 100.0 % | 100.0 % | 100.0 % |
| F1 ↑ | **99.7 %** | 52.6 % | 25.2 % |
| Plan stability ↑ | **88.8 %** | 55.4 % | 31.0 % |
| Average tasks regenerated ↓ | **2.27** | 6.19 | 14.39 |
| LLM (MockLLM) calls per replan ↓ | **0.22** | 2.89 | 4.49 |
| API calls per replan ↓ | **1.64** | 5.69 | 14.26 |

*Notes.*
- Precision and recall are measured against the contract oracle (71 of 71 hand labels agree).
- LLM calls are MockLLM invocations.
- Source: `eval/RESULTS.md`, `eval/results/summary.json`. A re-run in the v3 phase reproduced all 1,920 result rows exactly, except latency.

Table I shows the core benefit of contract checking. All three strategies regenerate every truly affected task (recall 100 %), but contract-aware replanning regenerates almost nothing else (precision 99.6 %).
- **Tasks regenerated:** 2.27 against 6.19 for B2, a reduction of $(6.19-2.27)/6.19 = 63.3\,\%$.
- **API calls:** 1.64 against 5.69, a reduction of 71.2 %.
- **Plan stability:** 88.8 %, against 55.4 % for B2 (+33.4 percentage points).

These are descriptive results from a single deterministic run, without significance tests.

### B. v2: operational quality

**TABLE II. v2 operational metrics** (mock mode, 160 scenarios, drift on). Means over the 160 runs of each strategy. *n* = runs where the metric is defined.

| Metric | Proposed (E1) | B2 naive | B1 full |
|---|---:|---:|---:|
| Plan-valid runs ↑ | 93.8 % | 93.8 % | 93.8 % |
| Execution-ready runs ↑ | 93.8 % | 93.8 % | 93.8 % |
| Task-level plan validity ↑ | 100.0 % | 100.0 % | 100.0 % |
| Task-level execution readiness ↑ | 100.0 % | 100.0 % | 100.0 % |
| Contract satisfaction ↑ | 99.8 % | 99.8 % | 99.8 % |
| User-requirement satisfaction ↑ | 88.9 % | **89.7 %** | 89.4 % |
| Requirement regression ↓ | 5.0 % | **4.4 %** | 5.0 % |
| MockLLM calls per replan ↓ | **0.22** | 2.89 | 4.49 |
| API calls per replan ↓ | **1.64** | 5.69 | 14.26 |
| Travel cost change (share of budget) ↓ | 3.07 % | **2.46 %** | 3.02 % |
| Within budget after replan ↑ | 55.6 % | **58.8 %** | 57.5 % |
| Activity-time retention ↑ (n = 71; N/A = 89) | 58.6 % | 58.6 % | 58.5 % |
| Replanning latency, median / p95 ↓ (mock) | **0.0009 / 0.0032 s** | 0.0029 / 0.0076 s | 0.0072 / 0.0144 s |
| Task Preservation Rate, unaffected tasks ↑ | **99.95 %** | 78.05 % | 59.87 % |
| Task Preservation Rate, unaffected user-visible ↑ | **99.91 %** | 65.18 % | 38.14 % |
| Replanning scope ↓ | **15.3 %** | 41.0 % | 94.5 % |
| Contract recovery ↑ | 93.8 % | 93.8 % | 93.8 % |
| Strict recovery ↑ | 88.8 % | **89.4 %** | 88.8 % |

*Notes.*
- The 10 runs that are not plan-valid in every strategy are the 10-hour delays (arrival after the check-in cutoff).
- Four of the 10 trips exceed their budget before any disruption, which lowers user-requirement satisfaction equally for all strategies.
- Latency is mock-mode wall-clock time and not comparable with live providers.
- Source: `eval/results_v2/METRICS_REPORT.md`.

**Where contract-aware replanning helps.**
- **Preservation:** it preserves 99.95 % of unaffected tasks, against 78.05 % (B2) and 59.87 % (B1).
- **Scope:** it touches 15.3 % of the plan, against 41.0 % and 94.5 %.
- **Cost:** it needs 3.5× (B2) to 8.7× (B1) fewer API calls.

**Where it does not.**
- All three strategies are equally valid and recover equally often (93.8 %). Contract-aware replanning does not make more plans executable; it keeps them executable with far less change.
- On requirement satisfaction, requirement regression, travel cost change, within-budget rate and strict recovery, B2 is slightly better. Regenerating more tasks sometimes happens to find a cheaper combination.

This trade-off motivates v3: choosing *better* replacements inside the small, contract-pruned region.

### C. v3: repair-search policies on the held-out benchmark

**Completeness.**
- 100,320 main result rows: 760 scenarios × 2 drift settings × 66 rows per scenario (6 deterministic arms × 1 run + 3 stochastic arms × 20 seeds).
- 1,520 E2′ rows.
- No missing or duplicate scenarios, seeds or arms.
- All runs verify the frozen pre-registration digest and the ground-truth hashes.

All held-out values below are for drift on, the primary setting.

**TABLE III-A. v3 validity, safety and plan quality** (held-out, drift on, 760 scenarios = 320 realistic + 440 stress). Rates are over all scenarios. Objective terms are means over feasible runs (seed means for E3, E4 and RS).

| Metric | E1 | E1g | E2 ($W$=10) | E2′ ($W$=50) | E3 GA | E4 hybrid | RS | EXH |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Feasible (all 7 hard constraints) ↑ | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % | 94.7 % |
| Silent failures ↓ | **5.3 % (40)** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Conflicts reported | 0 | 5.3 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % | 5.3 % |
| User-requirement satisfaction ↑ | 0.864 | 0.864 | 0.865 | 0.865 | 0.865 | 0.865 | 0.865 | 0.865 |
| Fitness ↑ | −0.336 | −0.331 | −0.316 | −0.307 | −0.305 | −0.305 | −0.305 | −0.305 |
| Disruption ↓ | 0.101 | 0.096 | 0.103 | 0.102 | 0.103 | 0.103 | 0.103 | 0.103 |
| Money (cost change / budget) ↓ | 0.103 | 0.103 | 0.080 | 0.073 | 0.069 | 0.069 | 0.069 | 0.069 |
| Activity-time retention $(1-\text{atl})$ ↑ | 0.988 | 0.988 | 0.987 | 0.988 | 0.987 | 0.987 | 0.987 | 0.987 |
| Re-approvals ↓ | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 | 0.120 |
| Contract satisfaction (share) | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Requirement regression | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Task Preservation Rate | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Contract / strict recovery | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

*Notes.*
- N/A means the metric is not recorded in v3. Contract validity is part of "feasible" (C1).
- The v3 activity-time retention covers all days, whereas v2's covers affected day plans only. The v3 money term includes an overrun penalty. **These values are not comparable with Table II.**
- All 40 infeasible scenarios are the 10-hour outbound delay on every held-out trip in both catalogues; no feasible repair exists there.
- Source: `eval/results_v3/heldout/ANALYSIS_HELDOUT.md` and the supplementary JSON.

**TABLE III-B. v3 replanning scope and optimality** (held-out, drift on). [S] = supplementary analysis.

| Metric | E1 | E1g | E2 ($W$=10) | E2′ ($W$=50) | E3 GA | E4 hybrid | RS | EXH |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Tasks regenerated (scenarios with an effect) | 3.35 | 3.04 | 3.11 | 3.07 | 3.07 | 3.07 | 3.07 | 3.07 |
| Necessity coverage ↑ | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Excess scope (tasks) ↓ | 0.014 | 0.000 | 0.061 | 0.025 | 0.024 | 0.024 | 0.024 | 0.024 |
| Scope optimality ↑ | 0.996 | 1.000 | 0.984 | 0.994 | 0.994 | 0.994 | 0.994 | 0.994 |
| [S] Matches the EXH optimum, realistic (n = 300) ↑ | 74.7 % | 90.3 % | **100 %** | 100 % | 100 % | 100 % | 100 % | ref. |
| [S] Matches the EXH optimum, stress (n = 420) ↑ | 57.1 % | 72.4 % | **86.7 %** | 97.4 % | 96.7 % | 99.8 % | 99.0 % | ref. |
| [S] Matches the EXH optimum, pooled (n = 720) ↑ | 64.4 % | 79.9 % | **92.2 %** | 98.5 % | 98.1 % | 99.9 % | 99.4 % | ref. |
| [S] Mean fitness gap to EXH ↓ | 0.0316 | 0.0262 | 0.0112 | 0.0025 | 0.00005 | 0.00004 | 0.00001 | 0 |

*Note.* EXH was never truncated on held-out; the largest exhaustive run needed 3,432 evaluations.

**TABLE III-C. v3 computational cost** (held-out, drift on; means per scenario, or per run for the stochastic arms).

| Metric | E1 | E1g | E2 ($W$=10) | E2′ ($W$=50) | E3 GA | E4 hybrid | RS | EXH |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full plan evaluations ↓ | N/A | 1.0 | 5.7 | 16.8 | 20.0 | 23.0 | 61.9 | 128.7 |
| Partial evaluations (not budgeted) | N/A | 8.3 | 41.8 | 118.5 | 0 | 41.8 | 0 | 0 |
| Mock API calls ↓ | 2.39 | 3.34 | 11.83 | 17.04 | 16.26 | 15.57 | 21.39 | 22.08 |
| MockLLM calls ↓ | 0.23 | 0.23 | 0.23 | 0.24 | 0.24 | 0.24 | 0.24 | 0.24 |
| Latency, median / p95 (ms, mock)ᵃ | 5.0 / 16.1 | 10.4 / 40.0 | 18.7 / 116.2 | 8.3 / 179.4 | 332.2 / 1,272.9 | 340.4 / 1,169.0 | 247.2 / 3,512.9 | 16.9 / 801.5 |
| Runs that reached the 60 s safety timeout | 0 | 0 | 0 | 0 | 8 | 7 | 15 | 0 |

ᵃ Latency was measured with 16 processes running in parallel and a machine sleep during the run. E2′ ran in a separate, less loaded launch. **Latency is not comparable across arms.**

On the stress catalogue alone, the full evaluations are:

| Arm | Full evaluations (stress) |
|---|---:|
| E2 | 6.75 |
| E2′ | 23.61 |
| E3 | 29.81 |
| E4 | 32.03 |
| RS | 100.31 |
| EXH | 213.91 |

**Reading Tables III-A to III-C.**
- **Same feasibility.** Every arm finds a feasible plan in the same 720 scenarios. The arms differ in what they do with the remaining 40 (silent commit vs explicit conflict) and in the quality of the repair they choose.
- **Quality.** Searching improves fitness from −0.336 (E1) and −0.331 (greedy) to −0.316 (beam, $W$=10) and −0.305 (the strong arms). The gain lies almost entirely in the money term, while disruption, activity time and re-approvals stay practically unchanged.
- **Scope.** All search arms keep necessity coverage at 1.0 and stay within 0.06 tasks of the minimum feasible scope.
- **Optimality.** Beam search with $W$=10 is optimal on every feasible realistic scenario and on 86.7 % of the feasible stress scenarios, at 5.7 full evaluations on average.

**TABLE IV. v3 pre-registered hypothesis tests** (held-out, drift on; Holm over 6 tests).

| | Comparison | n (decisive) | Result | Effect size [95 % CI] | 95 % CI of the mean Δfitness | p | p (Holm) | Verdict |
|---|---|---:|---|---|---|---:|---:|---|
| H1 | E2 ($W$=10) vs E1g, both catalogues | 720 (86) | 77 dominate, 9 dominated (573 equal, 61 incomparable) | Share won 0.895 [0.811, 0.951]ᵇ | N/A | 1.34 × 10⁻¹⁴ | 8.05 × 10⁻¹⁴ | **Supported** |
| H2 | E3 vs RS, stress | 420 (17) | 3 better, 14 worse, 403 tied | $r=-0.645$ | [−0.000195, 0.000000] | 0.0105 | 0.0105 | **Not supported** (significant in favour of RS) |
| H3 | E4 vs E2′, stress | 420 (11) | 11 better, 0 worse, 409 tied | $r=+1.00$ | [+0.000687, +0.008395] | 9.77 × 10⁻⁴ | 1.95 × 10⁻³ | **Supported, with a caveat** |
| H4 | E1 vs each of E2, E3, E4 | 760 (40) | 40 vs 0 silent failures | Share 1.00 [0.912, 1.00]ᵇ | N/A | 1.82 × 10⁻¹² | 9.09 × 10⁻¹² | **Supported** |

**GA justified (H2 ∧ H3): no.**

*Notes.*
- The H4 p-values apply to each of the three comparisons.
- ᵇ Clopper–Pearson interval (supplementary).
- Means (SD) over the paired feasible scenarios:
  - H1: E2 −0.3158 (0.3398), E1g −0.3308 (0.3693).
  - H2: E3 −0.359992 (0.3354), RS −0.359918 (0.3353).
  - H3: E4 −0.359969 (0.3354), E2′ −0.364155 (0.3448). Mean Δ = +0.004186.
- Source: `eval/results_v3/heldout/analysis_heldout.json`.

**H1: beam search beats greedy repair.**
- Of the 86 scenarios in which one arm's repair is strictly better on all four objective terms, beam search wins 77 (89.5 %).
- Per catalogue (supplementary, unadjusted): realistic 11 vs 0 ($p = 9.8\times10^{-4}$), stress 66 vs 9 ($p = 7.7\times10^{-12}$).

**H2: the GA does not beat random search.** The opposite is significant: random search reaches higher fitness in 14 of the 17 non-tied stress scenarios ($r=-0.645$, Holm $p=0.0105$). Under the pre-registered criterion, the GA is not justified.

**H3: the hybrid beats the wider beam, with a caveat.**
- The hybrid is never worse than E2′ and is better in 11 of 420 stress scenarios.
- It used more full evaluations than E2′ on held-out (32.0 vs 23.6 on stress). We discuss this in Sections VIII and IX.

**H4: validation removes silent failures.** E1 commits an infeasible plan without a flag in 40 scenarios. E2, E3 and E4 commit none and report a conflict in each case.

**TABLE V. v3 robustness checks** (held-out; not part of the Holm family).

| Analysis | Type | H1 | H2 | H3 | H4 |
|---|---|---|---|---|---|
| Drift off | pre-registered secondary | 78 vs 10, Holm $2.0\times10^{-13}$ ✓ | 2 / 25 / 393, $r=-0.85$, Holm $9.8\times10^{-6}$ ✗ | 13 / 1 / 405, $r=+0.86$, Holm $3.7\times10^{-4}$ ✓ | 40 vs 0 ✓ |
| Large option spaces (32 scenarios with more than 500 complete repairs) | pre-registered secondary, unadjusted | N/A | 3 / 2 / 27, $p=0.75$ ✗ | 6 / 0 / 26, mean Δ +0.022 [0.003, 0.052], $p=0.031$ | N/A |
| Excluding the 22 scenarios that contain a timed-out run | supplementary | 71 vs 9, Holm $2.6\times10^{-12}$ ✓ | 3 / 13 / 386, Holm 0.018 ✗ | 11 / 0 / 391, Holm 0.0020 ✓ | 40 vs 0 ✓ |

✓ = the verdict is the same as in the primary analysis, and the hypothesis holds; ✗ = the verdict is the same, and the hypothesis does not hold. Better / worse / tied counts are given as a / b / c.

**No verdict changes** under drift off or when the scenarios containing a timed-out run are excluded.

**TABLE VI. v3 development vs held-out.** The dev split (10 trips, 5 seeds, 160 + 220 scenarios) is tuning data only.

| | Dev result (Holm p) | Dev verdict | Held-out result (Holm p) | Held-out verdict |
|---|---|---|---|---|
| H1 | 43 vs 5 ($8.2\times10^{-8}$) | holds | 77 vs 9 ($8.0\times10^{-14}$) | holds |
| H2 | 0 / 4 / 206, $r=-1.0$ (0.19) | does not hold | 3 / 14 / 403, $r=-0.645$ (0.0105) | does not hold |
| H3 | 5 / 1 / 204, $r=+0.67$ (0.19) | does not hold | 11 / 0 / 409, $r=+1.0$ (0.0020) | holds |
| H4 | 20 vs 0 ($9.5\times10^{-6}$) | holds | 40 vs 0 ($9.1\times10^{-12}$) | holds |
| GA justified | no | | no | |

Stress-catalogue fitness, dev → held-out:

| Arm | Dev | Held-out |
|---|---:|---:|
| E1 | −0.4030 | −0.4071 |
| E1g | −0.3937 | −0.4000 |
| E2 | −0.3882 | −0.3791 |
| E2′ | −0.3663 | −0.3642 |
| E3 | −0.3590 | −0.3600 |
| E4 | −0.3590 | −0.3600 |
| RS | −0.3589 | −0.3599 |
| EXH | −0.3589 | −0.3599 |

The direction of every effect, and the ordering of the arms, generalise from dev to held-out.
- H3 reaches significance only on held-out. The dev split had 6 non-tied scenarios, against 11 on held-out.
- The reverse effect in H2 likewise becomes significant only with the larger held-out sample.

**TABLE VII. Strategies and the evaluation stages that use them.**

| Strategy | Region rule | Choice among options | Validation | v1 | v2 | v3 |
|---|---|---|---|:-:|:-:|:-:|
| E1 (contract) | contract-pruned | option 0 | flag only | ✓ | ✓ | ✓ |
| B1 (full) | everything | option 0 | — | ✓ | ✓ | ✓ᶜ |
| B2 (naive) | all descendants (DFS) | option 0 | — | ✓ | ✓ | ✓ᶜ |
| E1g, E2, E2′, E3, E4, RS, EXH | contract-pruned per branch | search | 7 hard constraints, with conflicts reported | | | ✓ |

ᶜ Run on held-out and reported in the analysis files; not part of the hypotheses.

**TABLE VIII. The three evaluation stages.**

| | v1 | v2 | v3 |
|---|---|---|---|
| Question | Does it regenerate only what is needed? | Is the result operationally sound, and at what cost? | Which search selects better valid repairs, and does validation prevent silent failures? |
| Data | 160 scenarios, 10 trips | the same 160 | dev: the same 10 trips; **held-out: 20 new trips, 760 scenarios** |
| Statistics | descriptive | descriptive | pre-registered tests, Holm, bootstrap CIs, 20 seeds |

---

## VIII. Discussion

**Contract checks and preservation (v1, v2).**
- Contract-aware replanning preserves almost every unaffected task (TPR 99.95 %) because it only crosses an edge whose contract is violated. Naive replanning regenerates every descendant, so even tasks whose constraints still hold are re-decided. Under market drift, a re-decided task often comes back different: another flight, another hotel, another schedule.
- A smaller replanning scope (15.3 % of the plan, against 41.0 % and 94.5 %) therefore means fewer changes for the traveller, fewer re-approvals and fewer provider calls.
- It does not make more plans executable: all strategies reach 93.8 %. Nor does it guarantee the best repair. B2 scored slightly better on cost and requirement metrics, because regenerating more tasks occasionally finds a cheaper combination.
- *Identifying* the affected tasks is therefore necessary but not sufficient. The choice *within* the affected region matters too.

**Candidate generation and validation (v3).**
- Generating several candidates per affected task, and validating each complete repair against the hard constraints, separates two outcomes that E1 conflates: "the best valid repair" and "no valid repair exists".
- Within the same contract-pruned region, search improves the repair mainly by finding cheaper valid combinations. The money term falls from 0.103 to between 0.080 and 0.069 of the budget.
- The scope and the necessity coverage stay essentially those of E1.

**Why beam search beats greedy repair.**
- Repair choices interact through the dependency graph. The locally best option for an upstream task, such as a flight, can force worse downstream choices: a later pickup or a shorter first day. Greedy repair commits to that first choice.
- Beam search keeps ten partial repairs alive and can recover. The result is 77 Pareto improvements against 9 losses (H1).
- The cost is moderate: 5.7 full evaluations and 41.8 partial evaluations per repair, against 1 and 8.3 for greedy.

**Why random search beating the GA matters.**
- H2 is a negative result, and we report it as one. Under the same evaluation cap, the GA's selection, crossover and mutation do not find better repairs than undirected sampling; random search is significantly better.
- The data point to the GA's pre-registered stopping rule. Patience of 10 generations ends the GA after about 30 evaluations on stress, while random search continues to about 100.
- The GA parameters were fixed in advance and not tuned. A different configuration could behave differently, but this study gives no evidence that it would.
- In option spaces of this size, a GA adds overhead without benefit. By our pre-registered rule, it is not justified.

**Why the hybrid's gain needs caution.**
- The hybrid beats the wider beam in 11 of 420 stress scenarios and never loses (H3). But the effect is small: a mean of +0.0042 fitness units, with 409 ties, and one scenario contributing 0.49 of the summed difference of 1.76.
- The hybrid also used more full evaluations than the wider beam on held-out (32.0 vs 23.6).
- Since random search does at least as well as the GA (H2), the hybrid's gain is best explained by *additional search after the beam*, not by the genetic operators. We do not read H3 as evidence for the GA.

**Why zero silent failures matters.**
- In the 40 scenarios where no valid repair exists (a 10-hour delay that makes the hotel unreachable before its check-in cutoff), the original replanner committed a plan that violates a hard constraint and presented it as valid.
- Every search arm instead reports a conflict, with the violated constraints and a best-effort plan, as a T2 decision.
- For a traveller, this is the difference between a plan that fails on arrival and a clear request to decide.
- Validation does not change *how often* a trip can be repaired (94.7 % for every arm). It changes whether infeasibility is visible.

**Why width 10 is the practical choice.**
- $W=10$ was selected by the pre-registered rule on the dev split and then frozen.
- On held-out, it is optimal on every feasible realistic scenario and on 86.7 % of the feasible stress scenarios (92.2 % pooled), at about a third of the evaluations of $W=50$.
- It is deterministic, so there is no seed variance, and it never failed silently.
- A wider beam ($W=50$: 98.5 % optimal) trades extra evaluations for quality on larger option spaces.

**Where exhaustive search remains useful.**
- In this benchmark, exhaustive enumeration was never truncated and needed 128.7 evaluations on average (at most 3,432). It is a practical optimality reference, and a viable policy whenever the option space is known to be small.
- Beam search remains preferable when the option space, and with it the number of provider calls, grows: EXH needed 22.1 API calls against beam's 11.8.

**What the results do not establish.** They do not establish:
- performance with real LLMs or live providers;
- robustness to the objective weights (the sensitivity analysis is not complete);
- agreement with human judgements of repair quality;
- benefit in option spaces beyond exhaustive reach;
- any effect on travellers, since there is no user study.

---

## IX. Threats to Validity and Limitations

1. **Mock-mode evaluation.**
   - Every experiment uses deterministic mock providers, a template-based MockLLM and simulated market drift.
   - API and LLM counts are calls to these mocks.
   - The results do not establish real-world LLM behaviour, live provider volatility or real latency.
   - Latency was measured offline. In v3 it was measured under 16 parallel processes and a machine sleep, so it is not comparable across arms.
2. **Timeout deviation from the pre-registration.**
   - The pre-registration specified a 2 s wall-clock safety timeout per search, but the runner used 60 s for every search arm except EXH. This applied equally to dev and held-out, and it was found during the held-out analysis.
   - 62 of 100,320 held-out runs reached the 60 s limit:

     | Setting | E2 | E3 | E4 | RS |
     |---|---:|---:|---:|---:|
     | Realistic, drift on | — | 4 | — | — |
     | Realistic, drift off | 1 | — | 2 | 1 |
     | Stress, drift on | — | 4 | 7 | 15 |
     | Stress, drift off | — | 3 | 8 | 17 |
     | **Total** | 1 | 11 | 17 | 33 |

     30 of them are in the primary drift-on analysis.
   - All were kept and flagged, as pre-registered. Of the 60 feasible timed-out runs, 58 reached the same fitness as the other seeds; 2 ended infeasible, both in drift off.
   - Excluding the 22 drift-on scenarios that contain a timed-out run changes no verdict (Table V).
   - A related, non-binding difference: the exhaustive arm's enumeration cap was 20,000 (realistic) and 100,000 (stress) complete plans, against the pre-registered 10,000. No run came near either value (maximum 3,432).
3. **H3 evaluation imbalance.**
   - E2′ ($W=50$) was matched to the hybrid's evaluation count on the dev split.
   - On held-out, the hybrid used 32.0 full evaluations on stress against 23.6 for E2′. E2′, in turn, used more partial evaluations (187.7 against 60.1 on stress), which the budget does not count.
   - The H3 improvement cannot be attributed to the GA.
4. **Incomplete supplementary analyses.**
   - The pre-registered weight sensitivity analysis (each weight × 0.5 and × 1.5) was started and stopped, and is **not reported**. We make no claim of robustness to the objective weights.
   - The pre-registered manual labelling (two annotators, Cohen's κ) has not been carried out. We make no claim of human validation.
   - The pre-registered metric `api_requests_unique` was not recorded, and the preference term of the objective is not measured.
5. **Pre-registration provenance.**
   - The frozen pre-registration has a self-verifying SHA-256 digest but was not committed to version control, so its freeze time has no independent timestamp.
   - The main held-out run was launched twice. The first launch ran no scenarios, because of a launcher error, and produced no data.
6. **Synthetic scenarios.**
   - The trips, disruptions, contracts, option spaces and stress catalogue were designed by the authors.
   - The ground truth (necessity set, minimum scope) is defined relative to our own contracts and option space.
   - The 760 held-out scenarios come from 20 trips. The CIs resample whole trips, but the sign and Wilcoxon tests treat scenarios as independent, as pre-registered, which may understate the uncertainty.
   - H2 and H3 rest on 17 and 11 non-tied scenarios.
   - The H4 evidence comes from one disruption type.
7. **Scalability.** Only 32 held-out scenarios have more complete repairs than the evaluation budget, and exhaustive search stays tractable throughout. The behaviour of the search policies on substantially larger option spaces remains to be established.
8. **No user study.** No usability, satisfaction or acceptance study has been conducted.
9. **Integrations and deployment.**
   - The live integrations (Duffel, Geoapify, Open-Meteo, Frankfurter, OSRM) and the OpenAI-compatible client are implemented, but they were not validated in this work.
   - Authentication and a background scheduler are not implemented.
   - Production deployment and security have not been evaluated.
   - The frontend shows T2 approval cards but has no dedicated view for repair conflicts.
   - The application's default replanning strategy is still the contract strategy (E1). Beam search is available through the API's strategy parameter.
10. **Scope of the comparison with prior work.** No direct comparison with TDAG or other systems was performed.

---

## X. Conclusion and Future Work

VoyageSync treats a travel itinerary as a dependency graph of typed tasks with executable contracts. It uses this structure to repair plans after disruptions.

**Contract-aware partial replanning** identifies the tasks a disruption invalidates and preserves the rest:
- 99.6 % precision at 100 % recall in v1;
- 99.95 % task preservation in v2;
- 3.5 to 8.7 times fewer provider calls than the naive and full baselines.

The **Risk Gate** keeps every consequential action behind an explicit approval.

The **search-based repair layer** adds four elements:
- candidate generation by the agents;
- seven hard constraints and a four-term objective;
- a choice of search policy;
- atomic commit, explicit conflict reporting and a decision log.

In a pre-registered, held-out evaluation on 20 unseen trips:
- beam search improves on greedy repair (H1);
- validation turns every silent failure into an explicit decision (H4);
- a genetic algorithm does not outperform random search, so it is not justified (H2);
- a beam-seeded GA's small gain over a wider beam comes with more evaluations (H3).

We recommend **contract-pruned beam search with width 10, combined with hard-constraint validation and conflict reporting**. It is deterministic, matches the exhaustive optimum on all feasible realistic and 86.7 % of feasible stress scenarios, and needs about 5.7 plan evaluations per repair.

**Future work:**
- complete the objective-weight sensitivity analysis and the human labelling of repair quality;
- generate candidates with real LLMs, and validate the live provider integrations;
- build larger benchmarks whose option spaces exceed exhaustive reach;
- add frontend support for conflict decisions and make beam search the default strategy;
- carry out user studies of repair acceptance.

---

## References

> To be verified against the published versions before submission. [1] is our own earlier paper. [2]–[10] are retained unchanged from it; their details, including authors, venues and years, have not been re-checked. See PAPER_AUDIT.md §7 for the further references the **[CITATION NEEDED]** markers require.

[1] A. Patel, K. Himte, R. Deshmukh, and M. Acharekar, "VoyageSync: A Dependency-Aware, Risk-Tiered Hybrid System for Adaptive Travel Planning," original four-page paper (unpublished manuscript; venue to be completed).
[2] Y. Wang, Z. Wu, J. Yao, and J. Su, "TDAG: A Multi-Agent Framework based on Dynamic Task Decomposition and Agent Generation," *Neural Networks*, 2025.
[3] S. Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," *ICLR*, 2023.
[4] Chen et al., "TravelAgent: An AI Assistant for Personalized Travel Planning," 2024.
[5] Jiang et al., "Towards Full Delegation: Designing Ideal Agentic Behaviors for Travel Planning," 2024.
[6] "Tourist Destination Recommendations Using Deep Learning," IEEE, 2025.
[7] A. Banerjee, A. Satish, and F. N. Aisyah, "Synth TRIPs: Knowledge-Grounded Framework for Personalized Tourism Recommenders," 2025.
[8] D. Buhalis and R. Law, "Progress in information technology and tourism management: 20 years on and 10 years after the Internet," *Tourism Management*, vol. 29, no. 4, pp. 609–623, 2008.
[9] LangChain Documentation, 2024.
[10] Amadeus for Developers Documentation, 2024.

> [9] and [10] are cited in the original paper but are not used in this revision. VoyageSync uses neither LangChain nor Amadeus. Remove them, unless they are cited elsewhere.
