"""
Live updates (plan §8.1): WS /ws/trips/{trip_id}.

On connect the client gets a ``snapshot`` (node statuses and pending
approvals), then every event published for the trip:

  trip_planned · replan · node_status · approval_requested · approval_decided
  · approval_reminder · notification (T1, with undo) · undo

Each message is ``{"type", "trip_id", "at", "data"}``. The client may send
"ping" and gets ``{"type": "pong"}``. Events are delivered in-process through
``bus``; publishers may run on another thread or event loop (each subscriber
keeps its own loop), which is how the test client drives it.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session

import app.db.database as db_module
from app.core.graph import GraphStore
from app.core.risk_gate import Action, RiskTier

router = APIRouter(tags=["live"])


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, trip_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.setdefault(trip_id, set()).add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, trip_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subs.get(trip_id, set())
            for entry in [e for e in subs if e[1] is queue]:
                subs.discard(entry)

    def subscribers(self, trip_id: str) -> int:
        with self._lock:
            return len(self._subs.get(trip_id, ()))

    def publish(self, trip_id: str, kind: str, data: dict[str, Any]) -> None:
        message = {"type": kind, "trip_id": trip_id, "at": datetime.now(timezone.utc).isoformat(), "data": data}
        with self._lock:
            subs = list(self._subs.get(trip_id, ()))
        for loop, queue in subs:
            with contextlib.suppress(RuntimeError):      # subscriber's loop already closed
                loop.call_soon_threadsafe(queue.put_nowait, message)


bus = EventBus()


def node_states(store: GraphStore) -> list[dict[str, Any]]:
    return [{"id": n.id, "type": n.type.value, "status": n.status.value, "version": n.version}
            for n in store.list_nodes()]


def publish_actions(trip_id: str, actions: list[Action]) -> None:
    """Approval cards for T2, toasts (with undo) for T1. T0 actions stay in the log only."""
    for action in actions:
        if action.tier == RiskTier.T2 and action.status.value == "pending":
            bus.publish(trip_id, "approval_requested", action.to_dict())
        elif action.tier == RiskTier.T1:
            bus.publish(trip_id, "notification", action.to_dict())


@router.websocket("/ws/trips/{trip_id}")
async def trip_updates(websocket: WebSocket, trip_id: str) -> None:
    from app.db import approval_repo, graph_repo, memory_repo

    await websocket.accept()
    with Session(db_module.engine) as session:
        trip = memory_repo.get_trip(trip_id, session)
        if trip is None:
            await websocket.send_json({"type": "error", "trip_id": trip_id, "data": {"detail": "trip not found"}})
            await websocket.close(code=4404)
            return
        store = graph_repo.load_graph(trip.graph_id, session)
        pending = [a.to_dict() for a in approval_repo.list_actions(session, trip_id=trip_id, status="pending")]

    queue = bus.subscribe(trip_id)
    receiver = asyncio.create_task(websocket.receive_text())
    try:
        await websocket.send_json({"type": "snapshot", "trip_id": trip_id,
                                   "at": datetime.now(timezone.utc).isoformat(),
                                   "data": {"nodes": node_states(store), "approvals": pending}})
        getter = asyncio.create_task(queue.get())
        while True:
            done, _ = await asyncio.wait({receiver, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                await websocket.send_json(getter.result())
                getter = asyncio.create_task(queue.get())
            if receiver in done:
                if receiver.result().strip().lower() == "ping":
                    await websocket.send_json({"type": "pong", "trip_id": trip_id})
                receiver = asyncio.create_task(websocket.receive_text())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(trip_id, queue)
        for task in (receiver, locals().get("getter")):
            if task is not None and not task.done():
                task.cancel()
