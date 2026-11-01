import { useState } from "react";
import { BellRing, ShieldCheck } from "lucide-react";
import type { GateAction } from "../types/live";

const fmtMoney = (n: number | null | undefined, currency: string) =>
  typeof n === "number"
    ? n.toLocaleString(undefined, { style: "currency", currency: currency || "USD", maximumFractionDigits: 0 })
    : "—";

function fmtValue(v: unknown, currency: string): string {
  if (typeof v === "number") return fmtMoney(v, currency);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v)) return `${v.slice(5, 10)} ${v.slice(11, 16)}`;
  return v == null ? "—" : String(v);
}

/** T2 approval cards: option, old → new price and the difference, what changes, why. */
export default function ApprovalCards({
  approvals,
  onDecide,
}: {
  approvals: GateAction[];
  onDecide: (actionId: string, decision: "approve" | "reject") => Promise<string | null>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  async function decide(id: string, decision: "approve" | "reject") {
    setBusy(id);
    const error = await onDecide(id, decision);
    setErrors((e) => ({ ...e, [id]: error ?? "" }));
    setBusy(null);
  }

  async function approveAll() {
    for (const a of approvals) await decide(a.action_id, "approve");
  }

  return (
    <section className="card p-5" aria-labelledby="approvals-heading">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <ShieldCheck size={15} strokeWidth={1.75} style={{ color: "var(--color-accent)" }} aria-hidden="true" />
          <h3 id="approvals-heading" className="text-sm font-semibold">Approvals</h3>
          <span
            className="text-xs font-medium px-2 py-0.5 rounded-full"
            style={{ backgroundColor: "var(--color-bg-alt)", color: "var(--color-text-muted)", border: "1px solid var(--color-border)" }}
          >
            {approvals.length}
          </span>
        </div>
        {approvals.length > 1 && (
          <button type="button" className="chip" onClick={approveAll} disabled={busy !== null}>
            Approve all
          </button>
        )}
      </div>
      <p className="text-xs mb-4" style={{ color: "var(--color-text-muted)" }}>
        T2 actions (book, change, cancel, pay) never run without your approval, not even after a timeout.
      </p>

      {approvals.length === 0 ? (
        <p className="text-xs rounded-lg p-4 text-center" style={{ border: "1.5px dashed var(--color-border)", color: "var(--color-text-muted)" }}>
          Nothing is waiting for approval.
        </p>
      ) : (
        <ul className="space-y-3" role="list">
          {approvals.map((a) => (
            <li key={a.action_id} className="rounded-lg p-3" style={{ border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface-raised)" }}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-semibold" style={{ color: "var(--color-text-primary)" }}>{a.title}</p>
                  <p className="text-xs mt-0.5" style={{ color: "var(--color-text-muted)" }}>
                    {a.tier} · {a.kind} · {a.refundable ? "refundable" : "non-refundable"}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  {a.old_amount != null ? (
                    <>
                      <p className="text-xs tabular-nums" style={{ color: "var(--color-text-muted)" }}>
                        <s>{fmtMoney(a.old_amount, a.currency)}</s> → {fmtMoney(a.amount, a.currency)}
                      </p>
                      <p
                        className="text-xs font-semibold tabular-nums"
                        style={{ color: (a.price_difference ?? 0) > 0 ? "#b04040" : "var(--color-accent)" }}
                      >
                        {(a.price_difference ?? 0) > 0 ? "+" : ""}
                        {fmtMoney(a.price_difference, a.currency)}
                      </p>
                    </>
                  ) : (
                    <p className="text-sm font-semibold tabular-nums">{fmtMoney(a.amount, a.currency)}</p>
                  )}
                </div>
              </div>

              {a.changes.length > 0 && (
                <ul className="mt-2 space-y-0.5" role="list" aria-label="What changes">
                  {a.changes.map((c) => (
                    <li key={c.field} className="text-xs" style={{ color: "var(--color-text-secondary)" }}>
                      <span style={{ color: "var(--color-text-muted)" }}>{c.field.replace(/_/g, " ")}:</span>{" "}
                      {fmtValue(c.old, a.currency)} → <strong className="font-medium">{fmtValue(c.new, a.currency)}</strong>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs mt-2" style={{ color: "var(--color-text-secondary)" }}>
                <span style={{ color: "var(--color-text-muted)" }}>Why:</span> {a.why}
              </p>
              {a.reminders > 0 && (
                <p className="text-xs mt-1 flex items-center gap-1" style={{ color: "#9a6a28" }}>
                  <BellRing size={11} aria-hidden="true" /> Reminder sent ×{a.reminders}: the offer is held, nothing is booked.
                </p>
              )}
              {(errors[a.action_id] || a.last_error) && (
                <p className="text-xs mt-1" style={{ color: "var(--color-danger)" }}>{errors[a.action_id] || a.last_error}</p>
              )}

              <div className="flex gap-2 mt-3">
                <button
                  type="button"
                  className="btn-primary"
                  style={{ padding: "0.4rem 1rem", fontSize: "0.8125rem" }}
                  disabled={busy !== null}
                  onClick={() => decide(a.action_id, "approve")}
                >
                  {busy === a.action_id ? "Booking…" : "Approve"}
                </button>
                <button type="button" className="chip" disabled={busy !== null} onClick={() => decide(a.action_id, "reject")}>
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
