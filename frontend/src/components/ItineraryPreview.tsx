import { Plane, Bus, Hotel, Calendar, Wallet, CloudSun, Trees } from "lucide-react";
import type { TripItinerary } from "../types/trip";

/**
 * The generated itinerary, rendered inside the AdaptivePlanPreview card.
 * Uses the same tokens and card styles as the rest of the planner.
 */
export default function ItineraryPreview({ itinerary }: { itinerary: TripItinerary }) {
  const { currency } = itinerary;
  const money = (n: unknown) =>
    typeof n === "number"
      ? n.toLocaleString(undefined, { style: "currency", currency, maximumFractionDigits: 0 })
      : "—";
  const time = (iso: unknown) => (typeof iso === "string" ? iso.slice(11, 16) : "—");
  const budget = itinerary.budget;

  return (
    <div className="space-y-5">
      {/* Flights + transfers */}
      <div>
        <p className="section-label mb-2">Getting there</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {itinerary.flights.map((f) => (
            <ItemCard
              key={f.node_id}
              icon={<Plane size={15} strokeWidth={1.75} />}
              title={`${f.origin} → ${f.destination}`}
              lines={[
                `${f.airline} ${f.flight_number} · ${f.departure_date}`,
                `Departs ${time(f.departure_time)} · arrives ${time(f.arrival_time)}`,
              ]}
              aside={money(f.cost)}
              badge={bookingBadge(f)}
            />
          ))}
          {itinerary.transfers.map((t) => (
            <ItemCard
              key={t.node_id}
              icon={<Bus size={15} strokeWidth={1.75} />}
              title={t.direction === "departure" ? "Transfer to airport" : "Airport transfer"}
              lines={[
                `${t.pickup_location} → ${t.dropoff_location}`,
                `Pickup ${time(t.pickup_time)} · ${t.duration_minutes} min`,
              ]}
              aside={money(t.cost)}
            />
          ))}
        </div>
      </div>

      {/* Stay */}
      {itinerary.stay && (
        <div>
          <p className="section-label mb-2">Stay</p>
          <ItemCard
            icon={<Hotel size={15} strokeWidth={1.75} />}
            title={String(itinerary.stay.hotel_name)}
            lines={[
              `${itinerary.stay.check_in} → ${itinerary.stay.check_out} · ${itinerary.stay.nights} nights`,
              `${itinerary.stay.room_type} · check-in from ${time(itinerary.stay.checkin_time)}`,
              ...(itinerary.stay.address ? [String(itinerary.stay.address)] : []),
            ]}
            aside={money(itinerary.stay.cost)}
            badge={bookingBadge(itinerary.stay)}
          />
        </div>
      )}

      {/* Days */}
      <div>
        <p className="section-label mb-2">Day by day</p>
        <ol className="space-y-2" role="list">
          {itinerary.days.map((d) => (
            <li
              key={d.day}
              className="rounded-lg p-3"
              style={{ border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface)" }}
            >
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <p className="text-xs font-semibold flex items-center gap-1.5" style={{ color: "var(--color-text-primary)" }}>
                  <Calendar size={13} strokeWidth={1.75} style={{ color: "var(--color-accent)" }} aria-hidden="true" />
                  Day {d.day} · {d.date} — {d.theme}
                </p>
                {d.weather?.condition && (
                  <span className="text-xs flex items-center gap-1" style={{ color: "var(--color-text-muted)" }}>
                    <CloudSun size={12} strokeWidth={1.75} aria-hidden="true" />
                    {d.weather.condition.replace("_", " ")}
                    {typeof d.weather.temperature_high_c === "number" && ` · ${d.weather.temperature_high_c}°C`}
                  </span>
                )}
              </div>
              {d.activities.length === 0 ? (
                <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>No activities scheduled.</p>
              ) : (
                <ul className="space-y-0.5" role="list">
                  {d.activities.map((a) => (
                    <li key={a.start} className="text-xs flex items-start gap-2" style={{ color: "var(--color-text-secondary)" }}>
                      <span className="tabular-nums w-10 shrink-0" style={{ color: "var(--color-text-muted)" }}>{a.time}</span>
                      <span className="min-w-0 flex-1">
                        {a.name}
                        {a.description && a.description !== `${a.time} — ${a.name}.` && (
                          <span className="block" style={{ color: "var(--color-text-muted)" }}>{a.description}</span>
                        )}
                      </span>
                      {a.outdoor && (
                        <Trees size={11} strokeWidth={1.75} aria-label="Outdoor" className="mt-0.5" style={{ color: "var(--color-text-muted)" }} />
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      </div>

      {/* Data sources */}
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        Data: {dataSources(itinerary)}
      </p>

      {/* Budget */}
      {budget && (
        <div
          className="rounded-lg px-4 py-3 flex gap-3 items-start"
          style={
            budget.within_budget
              ? { backgroundColor: "var(--color-accent-light)", border: "1px solid var(--color-accent-light)" }
              : { backgroundColor: "var(--color-warning-bg)", border: "1px solid var(--color-warning-border)" }
          }
          role="note"
        >
          <Wallet
            size={16}
            strokeWidth={1.75}
            style={{ color: budget.within_budget ? "var(--color-accent)" : "#b87c20", marginTop: "2px", flexShrink: 0 }}
            aria-hidden="true"
          />
          <div>
            <p className="text-sm font-medium" style={{ color: budget.within_budget ? "var(--color-accent)" : "#7a5118" }}>
              {money(budget.total_cost)} of {money(budget.budget_limit)}
              {budget.within_budget ? " — within budget" : " — over budget"}
            </p>
            <p className="text-xs mt-0.5" style={{ color: budget.within_budget ? "var(--color-accent-mid)" : "#9a6a28" }}>
              {String(budget.explanation ?? "")}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function dataSources(it: TripItinerary): string {
  const label = (s: unknown) =>
    String(s ?? "unknown")
      .replace("recorded:", "recorded ")
      .replace("open-meteo-archive-proxy", "Open-Meteo (same dates last year)")
      .replace("open-meteo-archive", "Open-Meteo archive")
      .replace("open-meteo-forecast", "Open-Meteo forecast")
      .replace("duffel-test", "Duffel test mode")
      .replace("synthetic", "mock");
  const parts = [
    `flights ${label(it.flights[0]?.source)}`,
    `hotel ${label(it.stay?.source)}`,
    `weather ${label(it.days[0]?.weather?.source)}`,
    `routes ${label(it.transfers[0]?.source)}`,
  ];
  if (it.budget?.fx_source) parts.push(`FX ${label(it.budget.fx_source)} (${it.budget.fx_date})`);
  return parts.join(" · ");
}

/** Risk Gate state of a bookable item (flight or stay). */
function bookingBadge(item: Record<string, any>): { label: string; tone: "pending" | "ok" | "muted" } | undefined {
  const delayed = item.status === "delayed" ? ` · delayed ${Math.round(item.delay_minutes / 6) / 10} h` : "";
  switch (item.booking_status) {
    case "awaiting_approval":
      return { label: `Awaiting approval${delayed}`, tone: "pending" };
    case "booked":
      return { label: `Booked · ${item.booking_ref}${delayed}`, tone: delayed ? "pending" : "ok" };
    case "rejected":
      return { label: "Not booked", tone: "muted" };
    default:
      return item.status === "delayed" ? { label: `Delayed ${Math.round(item.delay_minutes / 6) / 10} h`, tone: "pending" } : undefined;
  }
}

const BADGE_STYLES = {
  pending: { backgroundColor: "var(--color-warning-bg)", color: "#9a6a28" },
  ok: { backgroundColor: "var(--color-accent-light)", color: "var(--color-accent)" },
  muted: { backgroundColor: "var(--color-bg-alt)", color: "var(--color-text-muted)" },
} as const;

function ItemCard({
  icon,
  title,
  lines,
  aside,
  badge,
}: {
  icon: React.ReactNode;
  title: string;
  lines: string[];
  aside: string;
  badge?: { label: string; tone: keyof typeof BADGE_STYLES };
}) {
  return (
    <div
      className="rounded-lg p-3 flex gap-3 items-start"
      style={{ border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface)" }}
    >
      <span className="mt-0.5" style={{ color: "var(--color-accent)" }}>{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold" style={{ color: "var(--color-text-primary)" }}>{title}</p>
        {lines.map((l) => (
          <p key={l} className="text-xs mt-0.5" style={{ color: "var(--color-text-muted)" }}>{l}</p>
        ))}
        {badge && (
          <span className="inline-block text-xs font-medium mt-1.5 px-2 py-0.5 rounded-full" style={BADGE_STYLES[badge.tone]}>
            {badge.label}
          </span>
        )}
      </div>
      <span className="text-xs font-medium tabular-nums shrink-0" style={{ color: "var(--color-text-secondary)" }}>
        {aside}
      </span>
    </div>
  );
}
