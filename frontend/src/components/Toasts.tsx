import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import type { Alert, Outcome } from "../api/types";

export type ToastKind = "info" | "success" | "error" | "alert";

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
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
      setToasts((ts) => [...ts.slice(-5), { ...t, id }]);
      window.setTimeout(() => dismiss(id), TTL[t.kind]);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      error: (e) => push({ kind: "error", text: errorText(e) }),
      outcome: (o) => {
        for (const m of o.messages ?? []) push({ kind: "info", text: m });
        const a: Alert | null | undefined = o.alert;
        if (a && (a.changed || a.sent)) {
          push({
            kind: "alert",
            title: a.title,
            text: a.changed ? (a.sent ? "Picks changed - alert sent." : "Picks changed - see the banner.") : "Alert sent.",
            body: a.body,
          });
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
                <span className="toast-text">{t.text}</span>
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
