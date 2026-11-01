import { Zap } from "lucide-react";
import type { DisruptionPreset, DisruptionResult, Strategy } from "../types/live";

const STRATEGIES: Array<{ value: Strategy; label: string; hint: string }> = [
  { value: "contract", label: "Ours", hint: "contract-checked partial replan" },
  { value: "naive", label: "B2 naive", hint: "every descendant is redone" },
  { value: "full", label: "B1 full", hint: "the whole plan is redone" },
];

/** "Simulate disruption": preset scenarios from the Disruption Simulator. */
export default function DisruptionPanel({
  presets,
  strategy,
  onStrategy,
  compare,
  onCompare,
  busy,
  onSimulate,
  result,
}: {
  presets: DisruptionPreset[];
  strategy: Strategy;
  onStrategy: (s: Strategy) => void;
  compare: boolean;
  onCompare: (v: boolean) => void;
  busy: string | null;
  onSimulate: (scenario: string) => void;
  result: DisruptionResult | null;
}) {
  return (
    <section className="card p-5" aria-labelledby="disrupt-heading">
      <div className="flex items-center gap-2 mb-1">
        <Zap size={15} strokeWidth={1.75} style={{ color: "#b87c20" }} aria-hidden="true" />
        <h3 id="disrupt-heading" className="text-sm font-semibold">Simulate disruption</h3>
      </div>
      <p className="text-xs mb-4" style={{ color: "var(--color-text-muted)" }}>
        Inject a scripted event. Only tasks whose contracts break are regenerated.
      </p>

      <p className="form-label">Re-planning strategy</p>
      <div
        className="flex gap-1 p-1 rounded-lg mb-1"
        style={{ backgroundColor: "var(--color-bg-alt)" }}
        role="radiogroup"
        aria-label="Re-planning strategy"
      >
        {STRATEGIES.map((s) => (
          <button
            key={s.value}
            type="button"
            role="radio"
            aria-checked={strategy === s.value}
            className={`seg-option ${strategy === s.value ? "active" : ""}`}
            onClick={() => onStrategy(s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>
      <p className="text-xs mb-3" style={{ color: "var(--color-text-muted)" }}>
        {STRATEGIES.find((s) => s.value === strategy)?.hint}
      </p>

      <label className="flex items-center gap-2 text-xs mb-4 cursor-pointer" style={{ color: "var(--color-text-secondary)" }}>
        <input type="checkbox" checked={compare} onChange={(e) => onCompare(e.target.checked)} />
        Measure against the oracle and the B1 full-replan baseline
      </label>

      <div className="grid grid-cols-2 gap-2">
        {presets.map((p) => (
          <button
            key={p.name}
            type="button"
            className="chip justify-start text-left"
            style={{ whiteSpace: "normal", borderRadius: 8, minHeight: 36 }}
            disabled={busy !== null}
            onClick={() => onSimulate(p.name)}
            aria-label={p.description}
            title={`${p.type} → ${p.target_node}`}
          >
            {busy === p.name ? "Re-planning…" : p.description}
          </button>
        ))}
      </div>

      {result && (
        <div
          className="rounded-lg px-3 py-2.5 mt-4 text-xs"
          style={
            result.status === "replanned"
              ? { backgroundColor: "var(--color-warning-bg)", border: "1px solid var(--color-warning-border)", color: "#7a5118" }
              : { backgroundColor: "var(--color-bg-alt)", border: "1px solid var(--color-border)", color: "var(--color-text-secondary)" }
          }
          role="status"
          aria-live="polite"
        >
          <p className="font-medium">
            {result.status === "replanned" ? "Re-planned" : result.status === "no_effect" ? "No effect" : "Ignored"} ·{" "}
            {result.message}
          </p>
          {result.status === "replanned" && (
            <p className="mt-1">
              Stale: {result.stale.join(", ") || "none"} · {result.regenerated.length} regenerated ·{" "}
              {result.contracts_holding}/{result.contracts_total} contracts hold · {Math.round(result.latency_ms)} ms
            </p>
          )}
        </div>
      )}
    </section>
  );
}
