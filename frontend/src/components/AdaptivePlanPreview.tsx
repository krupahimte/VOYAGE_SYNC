import { AlertTriangle, CheckCircle, ArrowRight, Plane, Bus, Calendar } from "lucide-react";
import ItineraryPreview from "./ItineraryPreview";
import type { TripItinerary } from "../types/trip";

/**
 * Static illustration of VoyageSync's core USP:
 * a task dependency graph that selectively reruns only affected tasks
 * after a travel disruption — without touching unaffected parts of the plan.
 *
 * Before a trip is planned this is a static illustration. Once POST /trips
 * succeeds, the same card shows the generated itinerary instead.
 */
export default function AdaptivePlanPreview({ itinerary }: { itinerary?: TripItinerary | null }) {
  if (itinerary) {
    return (
      <section
        aria-labelledby="itinerary-heading"
        className="rounded-xl overflow-hidden"
        style={{ border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface)", boxShadow: "var(--shadow-card)" }}
      >
        <div
          className="px-6 py-4"
          style={{ borderBottom: "1px solid var(--color-border)", backgroundColor: "var(--color-surface-raised)" }}
        >
          <p className="section-label mb-1">Your plan</p>
          <h2
            id="itinerary-heading"
            className="text-base font-semibold"
            style={{ color: "var(--color-text-primary)" }}
          >
            {itinerary.departure_city} → {itinerary.destination}
          </h2>
          <p className="text-sm mt-1" style={{ color: "var(--color-text-secondary)" }}>
            {itinerary.departure_date} – {itinerary.return_date} · {itinerary.travelers} traveller
            {itinerary.travelers === 1 ? "" : "s"}
          </p>
        </div>
        <div className="p-6">
          <ItineraryPreview itinerary={itinerary} />
        </div>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="usp-heading"
      className="rounded-xl overflow-hidden"
      style={{ border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface)", boxShadow: "var(--shadow-card)" }}
    >
      {/* Header */}
      <div
        className="px-6 py-4"
        style={{ borderBottom: "1px solid var(--color-border)", backgroundColor: "var(--color-surface-raised)" }}
      >
        <p className="section-label mb-1">How it works</p>
        <h2
          id="usp-heading"
          className="text-base font-semibold"
          style={{ color: "var(--color-text-primary)" }}
        >
          Adaptive planning, not full replanning
        </h2>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-secondary)" }}>
          VoyageSync maps every task in your trip as a dependency graph. When a disruption hits,
          only the affected branch is regenerated — the rest of your plan stays intact.
        </p>
      </div>

      {/* Illustration */}
      <div className="p-6">
        {/* Step flow — Flight → Transfer → Day plan */}
        <div className="flex items-start gap-2 mb-6 flex-wrap sm:flex-nowrap">
          <TaskNode
            icon={<Plane size={15} strokeWidth={1.75} />}
            label="Outbound flight"
            sublabel="LHR → NRT · 12h 15m"
            status="disrupted"
          />
          <FlowArrow />
          <TaskNode
            icon={<Bus size={15} strokeWidth={1.75} />}
            label="Airport transfer"
            sublabel="Narita → Shinjuku"
            status="affected"
          />
          <FlowArrow />
          <TaskNode
            icon={<Calendar size={15} strokeWidth={1.75} />}
            label="Day 1 plan"
            sublabel="Senso-ji · Asakusa market"
            status="safe"
          />
        </div>

        {/* Disruption callout */}
        <div
          className="rounded-lg px-4 py-3 flex gap-3 items-start mb-4"
          style={{
            backgroundColor: "var(--color-warning-bg)",
            border: "1px solid var(--color-warning-border)",
          }}
          role="note"
        >
          <AlertTriangle
            size={16}
            strokeWidth={1.75}
            style={{ color: "#b87c20", marginTop: "2px", flexShrink: 0 }}
            aria-hidden="true"
          />
          <div>
            <p className="text-sm font-medium" style={{ color: "#7a5118" }}>
              Disruption detected — flight delayed by 3 hours
            </p>
            <p className="text-xs mt-0.5" style={{ color: "#9a6a28" }}>
              Transfer window is now too tight. VoyageSync is regenerating the transfer only.
            </p>
          </div>
        </div>

        {/* Resolution */}
        <div
          className="rounded-lg px-4 py-3 flex gap-3 items-start"
          style={{
            backgroundColor: "var(--color-accent-light)",
            border: "1px solid var(--color-accent-light)",
          }}
          role="note"
        >
          <CheckCircle
            size={16}
            strokeWidth={1.75}
            style={{ color: "var(--color-accent)", marginTop: "2px", flexShrink: 0 }}
            aria-hidden="true"
          />
          <div>
            <p className="text-sm font-medium" style={{ color: "var(--color-accent)" }}>
              Plan preserved — only transfer updated
            </p>
            <p className="text-xs mt-0.5" style={{ color: "var(--color-accent-mid)" }}>
              Day 1 activities and all downstream tasks remain unchanged.
              No full replanning required.
            </p>
          </div>
        </div>

        <p className="text-xs mt-4" style={{ color: "var(--color-text-muted)" }}>
          Illustration only — planning engine not yet active.
        </p>
      </div>
    </section>
  );
}

/* ── Sub-components ──────────────────────────────────────────── */

type TaskStatus = "disrupted" | "affected" | "safe";

function TaskNode({
  icon,
  label,
  sublabel,
  status,
}: {
  icon: React.ReactNode;
  label: string;
  sublabel: string;
  status: TaskStatus;
}) {
  const styles: Record<TaskStatus, { border: string; bg: string; iconColor: string; badge: string; badgeText: string }> = {
    disrupted: {
      border: "#e8c97e",
      bg: "#fdf6ec",
      iconColor: "#b87c20",
      badge: "#f5e0b0",
      badgeText: "Delayed",
    },
    affected: {
      border: "#e8c97e",
      bg: "#fdf6ec",
      iconColor: "#b87c20",
      badge: "#f5e0b0",
      badgeText: "Replanning",
    },
    safe: {
      border: "var(--color-border)",
      bg: "var(--color-surface)",
      iconColor: "var(--color-accent)",
      badge: "var(--color-accent-light)",
      badgeText: "Preserved",
    },
  };

  const s = styles[status];

  return (
    <div
      className="flex-1 rounded-lg p-3 min-w-[140px]"
      style={{ border: `1px solid ${s.border}`, backgroundColor: s.bg }}
    >
      <div className="flex items-center justify-between mb-2">
        <span style={{ color: s.iconColor }}>{icon}</span>
        <span
          className="text-xs font-medium px-2 py-0.5 rounded-full"
          style={{ backgroundColor: s.badge, color: s.iconColor }}
        >
          {s.badgeText}
        </span>
      </div>
      <p className="text-xs font-semibold" style={{ color: "var(--color-text-primary)" }}>{label}</p>
      <p className="text-xs mt-0.5" style={{ color: "var(--color-text-muted)" }}>{sublabel}</p>
    </div>
  );
}

function FlowArrow() {
  return (
    <div className="flex items-center justify-center pt-3 shrink-0 px-1">
      <ArrowRight size={14} strokeWidth={1.75} style={{ color: "var(--color-text-muted)" }} aria-hidden="true" />
    </div>
  );
}
