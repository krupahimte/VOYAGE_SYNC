import { useEffect, useState } from "react";
import TripGoalForm from "./TripGoalForm";
import type { PlanStatus } from "./TripGoalForm";
import AdaptivePlanPreview from "./AdaptivePlanPreview";
import UpcomingTrips from "./UpcomingTrips";
import TripWorkspace from "./TripWorkspace";
import type { TripFormValues, TripItinerary } from "../types/trip";
import { createTrip, getHealth, getItinerary } from "../lib/tripsApi";

const LAST_TRIP_KEY = "voyagesync:lastTrip";

interface SavedTrip {
  tripId: string;
  destination: string;
  dates: string;
}

function readSavedTrip(): SavedTrip | null {
  try {
    const raw = localStorage.getItem(LAST_TRIP_KEY);
    return raw ? (JSON.parse(raw) as SavedTrip) : null;
  } catch {
    return null;
  }
}

function saveTrip(trip: SavedTrip | null) {
  try {
    if (trip) localStorage.setItem(LAST_TRIP_KEY, JSON.stringify(trip));
    else localStorage.removeItem(LAST_TRIP_KEY);
  } catch {
    /* storage unavailable: the trip simply isn't restored after a reload */
  }
}

/**
 * Primary page component.
 *
 * Desktop layout (≥ lg):
 *   Left  (2/3): hero copy → form → USP illustration
 *   Right (1/3): upcoming trips sidebar
 *
 * Mobile: single column, sidebar stacks below.
 */
export default function TripPlanner() {
  const [submitted, setSubmitted] = useState(false);
  const [lastValues, setLastValues] = useState<TripFormValues | null>(null);
  const [status, setStatus] = useState<PlanStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [itinerary, setItinerary] = useState<TripItinerary | null>(null);
  const [engineMode, setEngineMode] = useState<string | null>(null);
  const [tripId, setTripId] = useState<string | null>(null);
  const [savedTrip, setSavedTrip] = useState<SavedTrip | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setEngineMode(h.mode ?? "mock"))
      .catch(() => setEngineMode(null));
  }, []);

  // Restore the last trip after a page reload (the backend keeps the graph).
  useEffect(() => {
    const saved = readSavedTrip();
    if (!saved) return;
    getItinerary(saved.tripId)
      .then((plan) => {
        setItinerary(plan);
        setTripId(saved.tripId);
        setSavedTrip(saved);
        setSubmitted(true);
      })
      .catch(() => saveTrip(null));
  }, []);

  async function handleSubmit(values: TripFormValues) {
    setLastValues(values);
    setStatus("loading");
    setStatusMessage("");
    try {
      const created = await createTrip(values);
      const plan = await getItinerary(created.trip_id);
      setItinerary(plan);
      setTripId(created.trip_id);
      const saved = {
        tripId: created.trip_id,
        destination: values.destination,
        dates: `${values.departureDate} – ${values.returnDate}`,
      };
      saveTrip(saved);
      setSavedTrip(saved);
      setSubmitted(true);
      setEngineMode((m) => m ?? "mock");
      setStatus("success");
      const source = created.decomposition.source === "llm" ? "" : " (template fallback)";
      setStatusMessage(
        `Plan ready${source}: ${created.nodes.length} tasks, ` +
          `${created.contracts_holding}/${created.contracts_total} constraints satisfied. ` +
          "Bookings are waiting for your approval below.",
      );
      setTimeout(() => document.getElementById("live-plan")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } catch (err) {
      setStatus("error");
      setStatusMessage(
        err instanceof TypeError
          ? "Could not reach the planning engine. Is the backend running on port 8000?"
          : `Planning failed: ${(err as Error).message}`,
      );
    }
  }

  const online = engineMode !== null;


  return (
    <>
      {/* ── Hero ──────────────────────────────────────────────────── */}
      <div
        style={{ backgroundColor: "var(--color-bg)", borderBottom: "1px solid var(--color-border)" }}
      >
        <div className="max-w-6xl mx-auto px-5 sm:px-8 pt-12 pb-10">
          <div className="max-w-2xl">
            <p
              className="section-label mb-3"
              aria-hidden="true"
            >
              Multi-agent travel planning
            </p>
            <h1
              className="text-3xl sm:text-4xl font-semibold tracking-tight mb-4"
              style={{ color: "var(--color-text-primary)", letterSpacing: "-0.02em" }}
            >
              Every journey deserves a&nbsp;better plan.
            </h1>
            <p
              className="text-base sm:text-lg leading-relaxed"
              style={{ color: "var(--color-text-secondary)", maxWidth: "42rem" }}
            >
              VoyageSync creates adaptable travel plans backed by a dependency graph.
              When disruptions happen — a delayed flight, a closed venue — only the
              affected tasks are regenerated. The rest of your trip stays intact.
            </p>
          </div>
        </div>
      </div>

      {/* ── Main content ───────────────────────────────────────────── */}
      <div className="max-w-6xl mx-auto px-5 sm:px-8 py-10">
        <div className="flex flex-col lg:flex-row gap-8 items-start">

          {/* ── Left column — form + USP ───────────────────────────── */}
          <div className="flex-1 min-w-0">

            {/* Form section header */}
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-lg font-semibold" style={{ color: "var(--color-text-primary)" }}>
                Create a new trip
              </h2>
              <span
                className="text-xs font-medium px-2.5 py-1 rounded-full"
                style={
                  online
                    ? {
                        backgroundColor: "var(--color-accent-light)",
                        color: "var(--color-accent)",
                        border: "1px solid var(--color-accent-light)",
                      }
                    : {
                        backgroundColor: "var(--color-warning-bg)",
                        color: "#8a6320",
                        border: "1px solid var(--color-warning-border)",
                      }
                }
              >
                {online ? `Engine online · ${engineMode} mode` : "Engine offline"}
              </span>
            </div>

            <TripGoalForm onSubmit={handleSubmit} status={status} statusMessage={statusMessage} />

            {/* ── Itinerary, or the USP illustration before planning ── */}
            <div className="mt-8" id="planning">
              <AdaptivePlanPreview itinerary={itinerary} />
            </div>
          </div>

          {/* ── Right sidebar ─────────────────────────────────────── */}
          <div className="w-full lg:w-72 xl:w-80 shrink-0" id="trips">
            <div
              className="rounded-xl p-5 sticky top-20"
              style={{
                backgroundColor: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                boxShadow: "var(--shadow-card)",
              }}
            >
              <UpcomingTrips
                submitted={submitted}
                destination={lastValues?.destination ?? savedTrip?.destination}
                dates={lastValues ? `${lastValues.departureDate} – ${lastValues.returnDate}` : savedTrip?.dates}
              />

              {/* Divider */}
              <div
                className="my-4"
                style={{ borderTop: "1px solid var(--color-border-subtle)" }}
                role="separator"
              />

              {/* Phase status card */}
              <div>
                <p className="section-label mb-3">System status</p>
                <ul className="space-y-2" role="list">
                  <StatusRow label="Health endpoint" status={online ? "ok" : "offline"} />
                  <StatusRow label="Planning engine" status={online ? "ok" : "offline"} />
                  <StatusRow label="Flight agents" status={online ? "ok" : "offline"} />
                  <StatusRow label="Hotel agents" status={online ? "ok" : "offline"} />
                  <StatusRow label="Orchestrator" status={online ? "ok" : "offline"} />
                </ul>
                <p className="text-xs mt-3" style={{ color: "var(--color-text-muted)" }}>
                  {online
                    ? engineMode === "mock"
                      ? "Mock mode: agents use recorded API responses and deterministic sample data."
                      : "Live mode: agents without API keys fall back to mock data."
                    : "Start the backend to enable planning."}
                </p>
              </div>
            </div>
          </div>

        </div>

        {/* ── Live demo: graph, disruptions, approvals, metrics ───── */}
        {tripId && (
          <div className="mt-10">
            <TripWorkspace key={tripId} tripId={tripId} onItinerary={setItinerary} />
          </div>
        )}
      </div>
    </>
  );
}

/* ── Status indicator row ─────────────────────────────────────── */
function StatusRow({ label, status }: { label: string; status: "ok" | "offline" }) {
  return (
    <li className="flex items-center justify-between">
      <span className="text-xs" style={{ color: "var(--color-text-secondary)" }}>{label}</span>
      <span className="flex items-center gap-1.5 text-xs font-medium" style={{ color: status === "ok" ? "var(--color-accent)" : "var(--color-text-muted)" }}>
        <span
          className="inline-block w-1.5 h-1.5 rounded-full"
          style={{ backgroundColor: status === "ok" ? "var(--color-accent)" : "var(--color-text-muted)" }}
          aria-hidden="true"
        />
        {status === "ok" ? "Online" : "Offline"}
      </span>
    </li>
  );
}
