"""
Trip API endpoints (plan §8.1).

POST /trips                      — submit goal → decompose → build graph → execute
GET  /trips/{trip_id}/graph      — graph with node statuses and live contract checks
GET  /trips/{trip_id}/itinerary  — user-facing plan
POST /trips/{trip_id}/disruptions — inject a simulated event → partial re-plan
GET  /trips/{trip_id}/disruptions — handled events and their re-plans
POST /trips/{trip_id}/monitor/poll — live weather check → events → re-plan
GET  /disruptions/presets        — simulator scenarios

Planning raises T2 booking approvals and every re-plan is reviewed by the
Risk Gate (see app.api.approvals); changes are pushed on WS /ws/trips/{id}.
"""

from __future__ import annotations

import copy

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.graph import GraphStore
from app.core.models import NodeType, TaskNode
from app.core.orchestrator import Orchestrator, check_graph_contracts
from app.core.templates import BUDGET, HOTEL, weather_id
from app.core.monitor.simulator import DisruptionSimulator, preset_catalog
from app.core.risk_gate import ActionLedger
from app.db import approval_repo, disruption_repo, graph_repo, memory_repo
from app.db.database import get_session
from app.schemas.disruption import DisruptionHistoryItem, DisruptionRequest, DisruptionResponse
from app.schemas.trip import (
    DayItinerary,
    DecompositionInfo,
    EdgeSummaryResponse,
    TaskSummaryResponse,
    TripCreateRequest,
    TripExecutionResponse,
    TripGoal,
    TripGraphResponse,
    TripItineraryResponse,
)

router = APIRouter(prefix="/trips", tags=["trips"])


def get_orchestrator() -> Orchestrator:
    """Dependency hook — a fresh Orchestrator configured from settings."""
    return Orchestrator()


def _node_summary(node: TaskNode) -> TaskSummaryResponse:
    return TaskSummaryResponse(id=node.id, type=node.type, agent=node.agent, status=node.status,
                               version=node.version, output=node.output)


def _load_trip(trip_id: str, session: Session):
    trip = memory_repo.get_trip(trip_id, session)
    if trip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trip '{trip_id}' not found.")
    return trip, graph_repo.load_graph(trip.graph_id, session)


# ── POST /trips ───────────────────────────────────────────────────────────────

@router.post("", response_model=TripExecutionResponse, status_code=status.HTTP_201_CREATED,
             summary="Plan a trip from a goal")
async def create_trip(
    body: TripCreateRequest,
    session: Session = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> TripExecutionResponse:
    from app.api.live import bus, node_states, publish_actions

    goal = TripGoal.model_validate(body.model_dump(exclude={"user_id", "propose_bookings"}))
    user_context = memory_repo.get_user_context(body.user_id, session)

    ledger = ActionLedger()
    result = await orchestrator.plan_trip(goal, user_context, ledger=ledger if body.propose_bookings else None)
    if result.graph_store is None or result.scheduler_result is None or result.decomposition is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=result.error)

    decomposition = DecompositionInfo(
        source=result.decomposition.source,
        attempts=result.decomposition.attempts,
        errors=result.decomposition.errors,
        llm_calls=result.decomposition.llm_calls,
        provider=result.decomposition.provider,
    )
    graph_repo.save_graph(result.graph_store, session)
    memory_repo.record_trip(
        session=session,
        trip_id=result.trip_id,
        user_id=body.user_id,
        graph_id=result.graph_id,
        goal=result.goal,
        summary=result.plan_summary,
        status="PLANNED" if result.success else "PARTIAL",
        decomposition=decomposition.model_dump(),
    )
    approval_repo.save_ledger(ledger, session)
    bus.publish(result.trip_id, "trip_planned", {"nodes": node_states(result.graph_store)})
    publish_actions(result.trip_id, result.actions)

    sched = result.scheduler_result
    return TripExecutionResponse(
        trip_id=result.trip_id,
        graph_id=result.graph_id,
        user_id=body.user_id,
        success=result.success,
        tasks_done=sched.done,
        tasks_failed=sched.failed,
        tasks_skipped=sched.skipped,
        summary=result.plan_summary,
        decomposition=decomposition,
        contracts_total=len(result.contract_checks),
        contracts_holding=result.contracts_holding,
        usage=result.usage,
        actions=[a.to_dict() for a in result.actions],
        nodes=[_node_summary(n) for n in result.graph_store.list_nodes()],
    )


# ── GET /trips/{trip_id}/graph ────────────────────────────────────────────────

@router.get("/{trip_id}/graph", response_model=TripGraphResponse, summary="Current trip graph")
def get_trip_graph(trip_id: str, session: Session = Depends(get_session)) -> TripGraphResponse:
    trip, store = _load_trip(trip_id, session)
    return TripGraphResponse(
        trip_id=trip_id,
        graph_id=trip.graph_id,
        nodes=[_node_summary(n) for n in _ordered_nodes(store)],
        edges=[
            EdgeSummaryResponse(source_id=c.source_id, destination_id=c.destination_id, contract=c.contract,
                                description=c.description, holds=c.holds, explanation=c.explanation)
            for c in check_graph_contracts(store)
        ],
    )


def _ordered_nodes(store: GraphStore) -> list[TaskNode]:
    return [store.get_node(nid) for nid in store.validate()]


# ── GET /trips/{trip_id}/itinerary ────────────────────────────────────────────

@router.get("/{trip_id}/itinerary", response_model=TripItineraryResponse, summary="User-facing plan")
def get_trip_itinerary(trip_id: str, session: Session = Depends(get_session)) -> TripItineraryResponse:
    trip, store = _load_trip(trip_id, session)
    goal = TripGoal.model_validate(trip.get_goal())
    nodes = {n.id: n for n in store.list_nodes()}

    def with_meta(node: TaskNode) -> dict:
        return {"node_id": node.id, "status": node.status.value, "version": node.version, **(node.output or {})}

    flights = sorted((n for n in nodes.values() if n.type == NodeType.FLIGHT),
                     key=lambda n: (n.output or {}).get("departure_time") or "")
    transfers = sorted((n for n in nodes.values() if n.type == NodeType.TRANSFER),
                       key=lambda n: (n.output or {}).get("pickup_time") or "")
    day_nodes = sorted((n for n in nodes.values() if n.type == NodeType.DAY_PLAN),
                       key=lambda n: n.inputs.get("day", 0))

    days = []
    for node in day_nodes:
        out = node.output or {}
        day = int(node.inputs.get("day", 0))
        weather = nodes.get(weather_id(day))
        days.append(DayItinerary(
            day=day,
            date=out.get("date") or node.inputs.get("date", ""),
            theme=out.get("theme") or node.inputs.get("theme", ""),
            weather=weather.output if weather else None,
            activities=out.get("activities", []),
            cost=out.get("cost", 0.0),
        ))

    return TripItineraryResponse(
        trip_id=trip_id,
        destination=goal.destination,
        departure_city=goal.departure_city,
        departure_date=goal.departure_date.isoformat(),
        return_date=goal.return_date.isoformat(),
        travelers=goal.travelers,
        currency=goal.currency,
        summary=trip.summary,
        flights=[with_meta(n) for n in flights],
        stay=with_meta(nodes[HOTEL]) if HOTEL in nodes else None,
        transfers=[with_meta(n) for n in transfers],
        days=days,
        budget=with_meta(nodes[BUDGET]) if BUDGET in nodes else None,
    )


# ── Disruptions (Phase 5) ─────────────────────────────────────────────────────

presets_router = APIRouter(prefix="/disruptions", tags=["disruptions"])


@presets_router.get("/presets", summary="Preset disruption scenarios for the simulator")
def list_presets() -> list[dict]:
    return preset_catalog()


def _to_response(trip_id: str, outcome: dict) -> DisruptionResponse:
    replan = outcome.get("replan") or {}
    return DisruptionResponse(
        trip_id=trip_id,
        status=outcome["status"],
        message=outcome["message"],
        event=outcome["event"],
        broken_edges=outcome.get("broken_edges", []),
        stale=replan.get("stale", []),
        regenerated=replan.get("regenerated", []),
        changed=replan.get("changed", []),
        latency_ms=outcome.get("latency_ms", 0.0),
        contracts_total=replan.get("contracts_total", 0),
        contracts_holding=replan.get("contracts_holding", 0),
        replan=outcome.get("replan"),
        actions=outcome.get("actions", []),
        metrics=outcome.get("metrics"),
    )


@router.post("/{trip_id}/disruptions", response_model=DisruptionResponse,
             summary="Inject a disruption and re-plan (contract PROPAGATE or a baseline)")
async def inject_disruption(
    trip_id: str,
    body: DisruptionRequest,
    session: Session = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> DisruptionResponse:
    trip, store = _load_trip(trip_id, session)
    simulator = DisruptionSimulator(seed=body.seed)
    try:
        event = (simulator.preset(body.scenario) if body.scenario
                 else simulator.make(body.type, body.target_node, **body.new_facts))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    # A fresh simulator with the same seed repeats its IDs, so number them per trip: the decision log
    # and the Risk Gate actions are linked to their disruption by this ID.
    count = len(disruption_repo.list_disruptions(trip_id, session))
    event = event.model_copy(update={"event_id": f"{event.event_id}-{count + 1}"})

    ledger = approval_repo.load_ledger(trip_id, session)
    before = copy.deepcopy(store) if body.compare_baseline else None
    outcome = await orchestrator.handle_disruption(store, event, strategy=body.strategy,
                                                   use_frozen_context=body.use_frozen_context,
                                                   context={"trip_id": trip_id}, ledger=ledger)
    metrics = None
    if before is not None and outcome.status in ("replanned", "no_effect"):
        from app.core.metrics import compare_with_baseline

        metrics = await compare_with_baseline(orchestrator, before, event, outcome, store)
    data = _record_outcome(trip_id, body.strategy, outcome, store, ledger, session, metrics=metrics)
    return _to_response(trip_id, data)


def _record_outcome(trip_id: str, strategy: str, outcome, store: GraphStore, ledger: ActionLedger,
                    session: Session, *, metrics: dict | None = None) -> dict:
    """Persist a handled disruption (graph, gate actions, trace) and push it to live clients."""
    from app.api.live import bus, node_states, publish_actions

    if outcome.status in ("replanned", "no_effect", "conflict"):     # a conflict still applied the event
        graph_repo.save_graph(store, session)
        approval_repo.save_ledger(ledger, session)
    data = outcome.to_dict()
    if metrics is not None:
        data["metrics"] = metrics
    disruption_repo.record_disruption(session, trip_id=trip_id, strategy=strategy, outcome=data)
    replan = data.get("replan") or {}
    bus.publish(trip_id, "replan", {"status": data["status"], "message": data["message"], "event": data["event"],
                                    "stale": replan.get("stale", []), "regenerated": replan.get("regenerated", []),
                                    "changed": replan.get("changed", []), "latency_ms": data["latency_ms"],
                                    "metrics": metrics})
    bus.publish(trip_id, "node_status", {"nodes": node_states(store)})
    publish_actions(trip_id, outcome.actions)
    return data


@router.get("/{trip_id}/disruptions", response_model=list[DisruptionHistoryItem],
            summary="Disruptions handled for this trip")
def get_disruptions(trip_id: str, session: Session = Depends(get_session)) -> list[DisruptionHistoryItem]:
    _load_trip(trip_id, session)
    return [DisruptionHistoryItem(id=r.id, event_id=r.event_id, event_type=r.event_type, target_node=r.target_node,
                                  status=r.status, strategy=r.strategy, created_at=r.created_at.isoformat(),
                                  outcome=r.get_outcome())
            for r in disruption_repo.list_disruptions(trip_id, session)]


@router.get("/{trip_id}/disruptions/{event_id}/decision",
            summary="Decision log of one disruption: affected tasks, alternatives, selected repair, "
                    "validation and approvals")
def get_decision_log(trip_id: str, event_id: str, session: Session = Depends(get_session)) -> dict:
    from app.core.repair.decision import decision_log

    _load_trip(trip_id, session)
    record = disruption_repo.get_disruption(trip_id, event_id, session)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No disruption '{event_id}' for trip '{trip_id}'.")
    actions = [a.to_dict() for a in approval_repo.list_actions(session, trip_id=trip_id) if a.event_id == event_id]
    return decision_log(trip_id, record.strategy, record.created_at.isoformat(), record.get_outcome(), actions)


@router.post("/{trip_id}/monitor/poll", response_model=list[DisruptionResponse],
             summary="Poll live sources (weather forecast) and handle any new disruption")
async def poll_monitor(
    trip_id: str,
    session: Session = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> list[DisruptionResponse]:
    _, store = _load_trip(trip_id, session)
    providers = getattr(orchestrator.registry, "providers", None)
    if providers is None:
        return []
    from app.core.monitor.monitor import DisruptionMonitor
    from app.core.replanner import Replanner

    monitor = DisruptionMonitor(Replanner(orchestrator.registry))
    ledger = approval_repo.load_ledger(trip_id, session)
    responses = []
    for event in await monitor.poll_weather(store, providers):
        outcome = await orchestrator.handle_disruption(store, event, context={"trip_id": trip_id}, ledger=ledger)
        responses.append(_to_response(trip_id, _record_outcome(trip_id, "contract", outcome, store, ledger, session)))
    return responses
