import { ClipboardList } from "lucide-react";

interface EmptyStateProps {
  submitted: boolean;
  destination?: string;
}

/**
 * Empty state shown below the form.
 * - Before submission: explains a plan will appear here.
 * - After submission: acknowledges the request and explains the engine is not yet active.
 */
export default function EmptyState({ submitted, destination }: EmptyStateProps) {
  if (submitted) {
    return (
      <div
        className="rounded-lg p-8 text-center"
        style={{
          border: "1px solid var(--color-border)",
          backgroundColor: "var(--color-surface)",
        }}
        role="status"
        aria-live="polite"
      >
        <div
          className="inline-flex items-center justify-center w-10 h-10 rounded-full mb-4"
          style={{ backgroundColor: "var(--color-accent-light)" }}
          aria-hidden="true"
        >
          <ClipboardList
            size={20}
            strokeWidth={1.75}
            style={{ color: "var(--color-accent)" }}
          />
        </div>
        <h2
          className="text-lg font-semibold mb-2"
          style={{ color: "var(--color-text-primary)" }}
        >
          {destination
            ? `Trip to ${destination} received`
            : "Trip request received"}
        </h2>
        <p
          className="text-sm leading-relaxed max-w-sm mx-auto"
          style={{ color: "var(--color-text-secondary)" }}
        >
          The planning engine is not active yet. Your trip goal has been
          recorded — itinerary generation will be available in a future phase.
        </p>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg p-8 text-center"
      style={{ border: "1px dashed var(--color-border)" }}
      aria-label="Trip plan output area — empty"
    >
      <p
        className="text-sm"
        style={{ color: "var(--color-text-muted)" }}
      >
        Your trip plan will appear here after you create one.
      </p>
    </div>
  );
}
