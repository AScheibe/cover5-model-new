import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import type { Alert, Outcome } from "../api/types";

export type ToastKind = "info" | "success" | "error" | "alert";

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
  /** Several messages from one outcome, shown together as one notification. */
  lines?: string[];
  title?: string;
  body?: string;
}

interface ToastApi {
  push: (t: Omit<Toast, "id">) => void;
  error: (e: unknown) => void;
  outcome: (o: Pick<Outcome, "messages" | "alert">) => void;
}

const Ctx = createContext<ToastApi | null>(null);

export function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

const TTL: Record<ToastKind, number> = { info: 6000, success: 5000, error: 12000, alert: 15000 };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);

  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = next.current++;
      // One copy of a message at a time; keep the newest four.
      setToasts((ts) => [...ts.filter((x) => !(x.text === t.text && x.title === t.title)), { ...t, id }].slice(-4));
      // A longer notification stays up longer (up to the alert's 15 s).
      const ttl = Math.min(TTL.alert, TTL[t.kind] + 2000 * Math.max(0, (t.lines?.length ?? 1) - 1));
      window.setTimeout(() => dismiss(id), ttl);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      error: (e) => push({ kind: "error", text: errorText(e) }),
      outcome: (o) => {
        // One notification per outcome, so its own messages never push each other out.
        const msgs = o.messages ?? [];
        if (msgs.length === 1) push({ kind: "info", text: msgs[0]! });
        else if (msgs.length > 1) push({ kind: "info", text: msgs.join("\n"), lines: msgs });
        const a: Alert | null | undefined = o.alert;
        if (a && (a.changed || a.sent)) {
          push({
            kind: "alert",
            title: a.title,
            text: a.changed ? (a.sent ? "Picks changed - alert sent." : "Picks changed - see the banner.") : "Alert sent.",
            body: a.body,
          });
        }
        // A push that didn't reach the phone must never look sent.
        if (a?.failures && a.failures.length > 0) {
          push({ kind: "error", text: `Alert not delivered: ${a.failures.join("; ")}. Check the alert settings.` });
        }
      },
    }),
    [push],
  );

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications">
        <div aria-live="polite" className="toast-stack">
          {toasts.map((t) => (
            <div key={t.id} className={`toast toast-${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
              <div className="toast-main">
                {t.title && <strong className="toast-title">{t.title}</strong>}
                {t.lines ? (
                  t.lines.map((l, i) => (
                    <span key={i} className="toast-text toast-line">
                      {l}
                    </span>
                  ))
                ) : (
                  <span className="toast-text">{t.text}</span>
                )}
                {t.body && (
                  <details className="toast-body">
                    <summary>Alert text</summary>
                    <pre>{t.body}</pre>
                  </details>
                )}
              </div>
              <button type="button" className="toast-x" aria-label="Dismiss notification" onClick={() => dismiss(t.id)}>
                ×
              </button>
            </div>
          ))}
        </div>
      </div>
    </Ctx.Provider>
  );
}

export function useToasts(): ToastApi {
  const c = useContext(Ctx);
  if (!c) throw new Error("useToasts outside ToastProvider");
  return c;
}
