import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Radio } from "lucide-react";
import TripGraph, { GraphLegend } from "./TripGraph";
import DisruptionPanel from "./DisruptionPanel";
import ApprovalCards from "./ApprovalCards";
import MetricsPanel from "./MetricsPanel";
import Toasts, { type Toast } from "./Toasts";
import {
  decideApproval,
  getActions,
  getApprovals,
  getGraph,
  getItinerary,
  getPresets,
  injectDisruption,
  undoAction,
} from "../lib/tripsApi";
import { useTripLive } from "../lib/useTripLive";
import type { TripItinerary } from "../types/trip";
import type {
  DisruptionPreset,
  DisruptionResult,
  GateAction,
  LiveMessage,
  NodeHighlight,
  ReplanMetrics,
  Strategy,
  TripGraph as TripGraphData,
} from "../types/live";

const STALE_PHASE_MS = 1400;

/**
 * The live demo for one trip: React Flow graph, disruption simulator,
 * approval cards, T1 toasts with undo, Risk Gate log and replan metrics.
 * Stays in sync with the backend over WS /ws/trips/{id}.
 */
export default function TripWorkspace({
  tripId,
  onItinerary,
}: {
  tripId: string;
  onItinerary: (it: TripItinerary) => void;
}) {
  const [graph, setGraph] = useState<TripGraphData | null>(null);
  const [approvals, setApprovals] = useState<GateAction[]>([]);
  const [actions, setActions] = useState<GateAction[]>([]);
  const [presets, setPresets] = useState<DisruptionPreset[]>([]);
  const [strategy, setStrategy] = useState<Strategy>("contract");
  const [compare, setCompare] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<DisruptionResult | null>(null);
  const [metrics, setMetrics] = useState<ReplanMetrics | null>(null);
  const [highlight, setHighlight] = useState<Record<string, NodeHighlight>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [error, setError] = useState("");
  const toasted = useRef(new Set<string>());
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const holdRefresh = useRef(false);
  const errorSeq = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [g, a, log, it] = await Promise.all([
        getGraph(tripId), getApprovals(tripId), getActions(tripId), getItinerary(tripId),
      ]);
      setGraph(g);
      setApprovals(a);
      setActions(log);
      onItinerary(it);
      setError("");
    } catch (err) {
      setError(`Could not load the trip: ${(err as Error).message}`);
    }
  }, [tripId, onItinerary]);

  const scheduleRefresh = useCallback(() => {
    if (holdRefresh.current) return;
    clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(refresh, 250);
  }, [refresh]);

  // The parent remounts this component (key={tripId}) for another trip, so state starts fresh.
  useEffect(() => {
    refreshTimer.current = setTimeout(refresh, 0);
    getPresets().then(setPresets).catch(() => setPresets([]));
    return () => clearTimeout(refreshTimer.current);
  }, [tripId, refresh]);

  const pushToast = useCallback((toast: Toast) => {
    if (toasted.current.has(toast.id)) return;
    toasted.current.add(toast.id);
    setToasts((t) => [...t.slice(-3), toast]);
  }, []);
  const dismiss = useCallback((id: string) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const notifyT1 = useCallback(
    (a: GateAction) =>
      pushToast({
        id: a.action_id,
        title: `${a.title} (T1)`,
        body: a.why ? `Because ${a.why}. Done automatically: you can undo it.` : undefined,
        tone: "info",
        undoActionId: a.undoable ? a.action_id : undefined,
      }),
    [pushToast],
  );

  const onLive = useCallback(
    (msg: LiveMessage) => {
      if (msg.type === "notification") notifyT1(msg.data as unknown as GateAction);
      if (msg.type === "approval_reminder") {
        const a = msg.data as unknown as GateAction;
        pushToast({ id: `${a.action_id}-r${a.reminders}`, title: "Approval still pending", body: `${a.title}: the offer is held.`, tone: "warning" });
      }
      if (msg.type !== "snapshot" && msg.type !== "pong") scheduleRefresh();
    },
    [notifyT1, pushToast, scheduleRefresh],
  );
  const live = useTripLive(tripId, onLive);

  async function simulate(scenario: string) {
    setBusy(scenario);
    setError("");
    holdRefresh.current = true;
    try {
      const res = await injectDisruption(tripId, scenario, strategy, compare);
      setResult(res);
      setMetrics(res.metrics);
      const target = res.event.target_node;
      if (res.status === "replanned") {
        // Demo step 4: stale nodes turn amber first, then the regenerated ones turn green.
        const stalePhase: Record<string, NodeHighlight> = { [target]: "disrupted" };
        for (const id of res.stale) if (id !== target) stalePhase[id] = "stale";
        setHighlight(stalePhase);
        await new Promise((r) => setTimeout(r, STALE_PHASE_MS));
        const done: Record<string, NodeHighlight> = { ...stalePhase };
        for (const id of res.regenerated) done[id] = "regenerated";
        if (!res.stale.includes(target)) done[target] = "disrupted";
        setHighlight(done);
      } else {
        setHighlight(res.status === "no_effect" ? { [target]: "disrupted" } : {});
      }
      holdRefresh.current = false;
      await refresh();
      res.actions.filter((a) => a.tier === "T1").forEach(notifyT1);
      const t2 = res.actions.filter((a) => a.tier === "T2" && a.status === "pending");
      if (t2.length) {
        pushToast({ id: `t2-${res.event.event_id}-${t2[0].action_id}`, title: "Approval needed", body: t2.map((a) => a.title).join(" · "), tone: "warning" });
      }
    } catch (err) {
      setError(`Disruption failed: ${(err as Error).message}`);
    } finally {
      holdRefresh.current = false;
      setBusy(null);
    }
  }

  async function decide(actionId: string, decision: "approve" | "reject"): Promise<string | null> {
    try {
      const out = await decideApproval(actionId, decision);
      const ref = out.node.output?.booking_ref;
      pushToast({
        id: `${actionId}-${decision}`,
        title: decision === "approve" ? `Booked ${ref ?? ""}`.trim() : "Rejected: nothing booked",
        body: out.action.title,
        tone: decision === "approve" ? "success" : "info",
      });
      await refresh();
      return null;
    } catch (err) {
      await refresh();
      return (err as Error).message;
    }
  }

  async function undo(actionId: string, toastId: string) {
    dismiss(toastId);
    try {
      const out = await undoAction(actionId);
      const broken = out.broken_edges.length;
      const pickup = out.node.output?.pickup_time;
      pushToast({
        id: `${actionId}-undo`,
        title: typeof pickup === "string" ? `Undone: pickup back to ${pickup.slice(11, 16)}` : `Undone: ${out.action.title}`,
        body: broken ? `${broken} contract(s) now broken (${out.broken_edges.map((e) => e.join(" → ")).join(", ")}).` : "All contracts hold.",
        tone: broken ? "warning" : "success",
      });
      setHighlight({});
      await refresh();
    } catch (err) {
      errorSeq.current += 1;
      pushToast({ id: `${actionId}-undo-err-${errorSeq.current}`, title: "Undo not possible", body: (err as Error).message, tone: "warning" });
    }
  }

  const selectedNode = graph?.nodes.find((n) => n.id === selected) ?? null;
  const recent = actions.slice(-8).reverse();

  return (
    <section id="live-plan" aria-labelledby="live-heading" className="space-y-5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <p className="section-label mb-1">Live plan</p>
          <h2 id="live-heading" className="text-lg font-semibold">Task dependency graph</h2>
        </div>
        <span
          className="text-xs font-medium px-2.5 py-1 rounded-full flex items-center gap-1.5"
          style={
            live === "live"
              ? { backgroundColor: "var(--color-accent-light)", color: "var(--color-accent)" }
              : { backgroundColor: "var(--color-bg-alt)", color: "var(--color-text-muted)" }
          }
          title={`WebSocket ${live}`}
        >
          <Radio size={12} aria-hidden="true" /> {live === "live" ? "Live updates" : live === "connecting" ? "Connecting…" : "Offline"} ·{" "}
          trip {tripId}
        </span>
      </div>

      {error && <p className="text-sm" style={{ color: "var(--color-danger)" }} role="alert">{error}</p>}

      <div className="card p-4">
        {graph ? (
          <TripGraph key={tripId} graph={graph} highlight={highlight} selectedId={selected} onSelect={setSelected} />
        ) : (
          <p className="text-sm p-6" style={{ color: "var(--color-text-muted)" }}>Loading graph…</p>
        )}
        <div className="mt-3 flex flex-col gap-2">
          <GraphLegend />
          {selectedNode && (
            <div className="rounded-lg p-3 text-xs" style={{ backgroundColor: "var(--color-surface-raised)", border: "1px solid var(--color-border)" }}>
              <p className="font-semibold" style={{ color: "var(--color-text-primary)" }}>
                {selectedNode.id} · {selectedNode.type} · {selectedNode.status} · version {selectedNode.version}
              </p>
              <p className="mt-1" style={{ color: "var(--color-text-secondary)" }}>
                {Object.entries(selectedNode.output ?? {})
                  .filter(([, v]) => ["string", "number", "boolean"].includes(typeof v))
                  .slice(0, 12)
                  .map(([k, v]) => `${k}: ${String(v)}`)
                  .join(" · ")}
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2 items-start">
        <div className="space-y-5">
          <DisruptionPanel
            presets={presets}
            strategy={strategy}
            onStrategy={setStrategy}
            compare={compare}
            onCompare={setCompare}
            busy={busy}
            onSimulate={simulate}
            result={result}
          />
          <MetricsPanel metrics={metrics} />
        </div>
        <div className="space-y-5">
          <ApprovalCards approvals={approvals} onDecide={decide} />
          <section className="card p-5" aria-labelledby="gate-log-heading">
            <div className="flex items-center gap-2 mb-3">
              <Activity size={15} strokeWidth={1.75} style={{ color: "var(--color-accent)" }} aria-hidden="true" />
              <h3 id="gate-log-heading" className="text-sm font-semibold">Risk Gate log</h3>
            </div>
            {recent.length === 0 ? (
              <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>No actions yet.</p>
            ) : (
              <ul className="space-y-1.5" role="list">
                {recent.map((a) => (
                  <li key={a.action_id} className="text-xs flex items-center gap-2">
                    <TierBadge tier={a.tier} />
                    <span className="min-w-0 flex-1 truncate" style={{ color: "var(--color-text-secondary)" }} title={a.reasons.join("; ")}>
                      {a.title}
                    </span>
                    {a.undoable ? (
                      <button type="button" className="chip" style={{ padding: "0.1rem 0.6rem" }} onClick={() => undo(a.action_id, "")}>
                        Undo
                      </button>
                    ) : (
                      <span style={{ color: "var(--color-text-muted)" }}>{a.status}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-xs mt-3" style={{ color: "var(--color-text-muted)" }}>
              T0 runs automatically · T1 runs and notifies (undo) · T2 waits for approval.
            </p>
          </section>
        </div>
      </div>

      <Toasts toasts={toasts} onDismiss={dismiss} onUndo={undo} />
    </section>
  );
}

function TierBadge({ tier }: { tier: string }) {
  const style =
    tier === "T2"
      ? { background: "#eef2f9", color: "#4f6a9a" }
      : tier === "T1"
        ? { background: "var(--color-warning-bg)", color: "#9a6a28" }
        : { background: "var(--color-bg-alt)", color: "var(--color-text-muted)" };
  return (
    <span className="font-semibold shrink-0" style={{ ...style, fontSize: 10, padding: "1px 6px", borderRadius: 8 }}>
      {tier}
    </span>
  );
}
