import { useEffect, useRef, useState } from "react";
import { api } from "../config/api";
import type { LiveMessage } from "../types/live";

export type LiveState = "connecting" | "live" | "offline";

/**
 * Subscribe to WS /ws/trips/{id}. Calls `onMessage` for every message
 * (snapshot, replan, node_status, approval_*, notification, undo) and
 * reconnects with a short back-off if the socket drops.
 */
export function useTripLive(tripId: string | null, onMessage: (msg: LiveMessage) => void): LiveState {
  const [state, setState] = useState<LiveState>("offline");
  const handler = useRef(onMessage);

  useEffect(() => {
    handler.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    if (!tripId) return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    let closed = false;

    const connect = () => {
      setState("connecting");
      socket = new WebSocket(api.live(tripId));
      socket.onopen = () => {
        attempts = 0;
        setState("live");
      };
      socket.onmessage = (ev) => {
        try {
          handler.current(JSON.parse(ev.data) as LiveMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setState("offline");
        if (!closed) retry = setTimeout(connect, Math.min(8000, 500 * 2 ** attempts++));
      };
    };
    connect();

    return () => {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, [tripId]);

  return state;
}
