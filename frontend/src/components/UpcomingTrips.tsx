import { CalendarX } from "lucide-react";

interface UpcomingTripsProps {
  submitted: boolean;
  destination?: string;
  dates?: string;
}

/**
 * Sidebar panel showing upcoming trips.
 * Pre-submit: empty state with dashed border.
 * Post-submit: shows the newly submitted trip as a pending card.
 */
export default function UpcomingTrips({ submitted, destination, dates }: UpcomingTripsProps) {
  return (
    <aside aria-labelledby="upcoming-trips-heading">
      <div className="flex items-center justify-between mb-4">
        <h2
          id="upcoming-trips-heading"
          className="text-sm font-semibold"
          style={{ color: "var(--color-text-primary)" }}
        >
          Upcoming trips
        </h2>
        <span
          className="text-xs font-medium px-2 py-0.5 rounded-full"
          style={{ backgroundColor: "var(--color-bg-alt)", color: "var(--color-text-muted)", border: "1px solid var(--color-border)" }}
        >
          {submitted ? "1" : "0"}
        </span>
      </div>

      {submitted && destination ? (
        <div
          className="rounded-lg p-4 mb-3"
          style={{ backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)", boxShadow: "var(--shadow-card)" }}
          role="status"
          aria-live="polite"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p
                className="text-sm font-semibold truncate"
                style={{ color: "var(--color-text-primary)" }}
              >
                {destination}
              </p>
              <p className="text-xs mt-0.5" style={{ color: "var(--color-text-muted)" }}>
                {dates ?? "Dates to be confirmed"}
              </p>
            </div>
            <span
              className="text-xs font-medium px-2 py-0.5 rounded-full shrink-0"
              style={{
                backgroundColor: "var(--color-accent-light)",
                color: "var(--color-accent)",
                border: "1px solid var(--color-accent-light)",
              }}
            >
              Planned
            </span>
          </div>
          <p className="text-xs mt-3 leading-relaxed" style={{ color: "var(--color-text-secondary)" }}>
            Itinerary generated from your goal. Bookings, changes and payments
            always ask for your approval first.
          </p>
        </div>
      ) : (
        <div
          className="rounded-lg p-6 flex flex-col items-center text-center"
          style={{ border: "1.5px dashed var(--color-border)" }}
          aria-label="No upcoming trips"
        >
          <CalendarX
            size={24}
            strokeWidth={1.5}
            style={{ color: "var(--color-text-muted)", marginBottom: "0.625rem" }}
            aria-hidden="true"
          />
          <p className="text-sm font-medium mb-1" style={{ color: "var(--color-text-secondary)" }}>
            No trips yet
          </p>
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            Create your first trip to see it here.
          </p>
        </div>
      )}
    </aside>
  );
}
