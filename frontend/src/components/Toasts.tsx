import { useEffect } from "react";
import { Bell, CheckCircle, Undo2, X } from "lucide-react";

export interface Toast {
  id: string;
  title: string;
  body?: string;
  tone: "info" | "success" | "warning";
  /** T1 action that can be undone from the toast. */
  undoActionId?: string;
}

const TONES = {
  info: { border: "var(--color-border)", icon: "var(--color-accent)" },
  success: { border: "var(--color-accent-mid)", icon: "var(--color-accent)" },
  warning: { border: "var(--color-warning-border)", icon: "#b87c20" },
} as const;

/** T1 notifications ("Rebooked pickup for 19:30 · Undo") and short confirmations. */
export default function Toasts({
  toasts,
  onDismiss,
  onUndo,
}: {
  toasts: Toast[];
  onDismiss: (id: string) => void;
  onUndo: (actionId: string, toastId: string) => void;
}) {
  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2"
      style={{ width: "min(360px, calc(100vw - 2rem))" }}
      aria-live="polite"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} onUndo={onUndo} />
      ))}
    </div>
  );
}

function ToastItem({ toast, onDismiss, onUndo }: { toast: Toast; onDismiss: (id: string) => void; onUndo: (actionId: string, toastId: string) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), toast.undoActionId ? 15000 : 6000);
    return () => clearTimeout(timer);
  }, [toast.id, toast.undoActionId, onDismiss]);

  const tone = TONES[toast.tone];
  return (
    <div
      className="rounded-lg px-4 py-3 flex gap-3 items-start"
      style={{ backgroundColor: "var(--color-surface)", border: `1px solid ${tone.border}`, boxShadow: "var(--shadow-raised)" }}
    >
      <span style={{ color: tone.icon, marginTop: 2 }} aria-hidden="true">
        {toast.tone === "success" ? <CheckCircle size={15} strokeWidth={1.75} /> : <Bell size={15} strokeWidth={1.75} />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium" style={{ color: "var(--color-text-primary)" }}>{toast.title}</p>
        {toast.body && <p className="text-xs mt-0.5" style={{ color: "var(--color-text-secondary)" }}>{toast.body}</p>}
        {toast.undoActionId && (
          <button
            type="button"
            className="chip mt-2"
            style={{ padding: "0.25rem 0.75rem" }}
            onClick={() => onUndo(toast.undoActionId!, toast.id)}
          >
            <Undo2 size={12} strokeWidth={1.75} className="mr-1" aria-hidden="true" /> Undo
          </button>
        )}
      </div>
      <button type="button" onClick={() => onDismiss(toast.id)} aria-label="Dismiss" style={{ color: "var(--color-text-muted)", background: "none", border: "none", cursor: "pointer" }}>
        <X size={14} />
      </button>
    </div>
  );
}
