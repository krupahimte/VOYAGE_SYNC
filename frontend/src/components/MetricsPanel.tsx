import { BarChart3 } from "lucide-react";
import type { ReplanMetrics, StrategyMetrics } from "../types/live";

const STRATEGY_LABEL: Record<string, string> = { contract: "Ours", naive: "B2 naive", full: "B1 full" };

const pct = (n: number) => `${Math.round(n * 100)}%`;

const ROWS: Array<{ label: string; hint: string; value: (m: StrategyMetrics) => string; better?: "high" | "low"; raw?: (m: StrategyMetrics) => number }> = [
  { label: "Tasks regenerated", hint: "|R|", value: (m) => String(m.regenerated_count), better: "low", raw: (m) => m.regenerated_count },
  { label: "Replan precision", hint: "|R∩A| / |R|", value: (m) => pct(m.precision), better: "high", raw: (m) => m.precision },
  { label: "Replan recall", hint: "|R∩A| / |A|", value: (m) => pct(m.recall), better: "high", raw: (m) => m.recall },
  { label: "Replan F1", hint: "harmonic mean", value: (m) => pct(m.f1), better: "high", raw: (m) => m.f1 },
  { label: "Plan stability", hint: "visible items unchanged", value: (m) => pct(m.stability), better: "high", raw: (m) => m.stability },
  { label: "Constraints satisfied", hint: "contracts holding", value: (m) => `${m.contracts_holding}/${m.contracts_total}` },
  { label: "Recovery latency", hint: "event → valid plan", value: (m) => `${Math.round(m.latency_ms)} ms` },
  { label: "Cost of replan", hint: "LLM + API calls", value: (m) => `${m.llm_calls} LLM · ${m.api_calls} API` },
];

/** This replan's precision, recall and stability next to the B1 full-replan baseline. */
export default function MetricsPanel({ metrics }: { metrics: ReplanMetrics | null }) {
  return (
    <section className="card p-5" aria-labelledby="metrics-heading">
      <div className="flex items-center gap-2 mb-1">
        <BarChart3 size={15} strokeWidth={1.75} style={{ color: "var(--color-accent)" }} aria-hidden="true" />
        <h3 id="metrics-heading" className="text-sm font-semibold">Replan metrics</h3>
      </div>
      {!metrics ? (
        <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
          Simulate a disruption with measuring switched on to compare this replan with a full replan (B1).
        </p>
      ) : (
        <>
          <p className="text-xs mb-3" style={{ color: "var(--color-text-muted)" }}>
            Oracle affected set A ({metrics.oracle.affected.length}): {metrics.oracle.affected.join(", ") || "none"}
          </p>
          <table className="w-full text-xs" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ color: "var(--color-text-muted)" }}>
                <th className="text-left font-medium pb-2">Metric</th>
                <th className="text-right font-medium pb-2">{STRATEGY_LABEL[metrics.replan.strategy] ?? metrics.replan.strategy}</th>
                <th className="text-right font-medium pb-2">B1 full</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((r) => {
                const a = r.raw?.(metrics.replan);
                const b = r.raw?.(metrics.b1);
                const wins = r.better && a !== undefined && b !== undefined && a !== b
                  ? (r.better === "high" ? a > b : a < b)
                  : false;
                return (
                  <tr key={r.label} style={{ borderTop: "1px solid var(--color-border-subtle)" }}>
                    <td className="py-1.5" title={r.hint} style={{ color: "var(--color-text-secondary)" }}>{r.label}</td>
                    <td className="py-1.5 text-right tabular-nums font-semibold" style={{ color: wins ? "var(--color-accent)" : "var(--color-text-primary)" }}>
                      {r.value(metrics.replan)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums" style={{ color: "var(--color-text-secondary)" }}>{r.value(metrics.b1)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-xs mt-3" style={{ color: "var(--color-text-muted)" }}>
            One run on this trip; B1 runs on a copy and never touches your plan or bookings. The benchmark over 100+
            scenarios comes from the evaluation harness.
          </p>
        </>
      )}
    </section>
  );
}
