import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Run } from "../api/types";
import { errorText } from "../components/Toasts";
import { fmtDateTime } from "../lib/format";

export function AlertLog({ season, week, version }: { season: number; week: number; version: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .runs(season, week, 50)
      .then((r) => {
        if (alive) {
          setRuns(r);
          setError(null);
        }
      })
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [season, week, version]);

  return (
    <section className="panel alert-log" aria-label="Alert log">
      <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
        <summary>
          <h2>Alert log</h2>
          <span className="muted small">
            {runs ? `${runs.length} run${runs.length === 1 ? "" : "s"}` : error ? "unavailable" : "loading"}
            {runs && runs.some((r) => r.changed) && ` · ${runs.filter((r) => r.changed).length} changed picks`}
          </span>
        </summary>
        {error && <p className="field-error">{error}</p>}
        {runs && runs.length === 0 && <p className="muted">No runs yet for this week.</p>}
        <ul className="runs">
          {runs?.map((r) => (
            <li key={r.id}>
              <details>
                <summary>
                  <span className="run-time">{fmtDateTime(r.at)}</span>
                  <span className="run-reason">{r.reason}</span>
                  {r.changed && <span className="badge badge-live">changed</span>}
                  {r.sent && <span className="badge badge-good">sent</span>}
                  {!r.sent && r.warnings.some((w) => w.startsWith("Alert not delivered")) && (
                    <span className="badge badge-bad" title={r.warnings.filter((w) => w.startsWith("Alert not delivered")).join("\n")}>
                      not delivered
                    </span>
                  )}
                  <span className="run-title muted">{r.title}</span>
                </summary>
                <pre className="alert-body">{r.body}</pre>
              </details>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
