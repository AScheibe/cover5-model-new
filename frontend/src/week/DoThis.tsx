import { useState } from "react";
import type { Diff, LastChange, Pick } from "../api/types";
import { fmtDateTime, fmtPoints, fmtSpread } from "../lib/format";

function describe(team: string, picks: Pick[]): string {
  const p = picks.find((x) => x.team === team);
  if (!p) return team;
  return `${p.label} (market ${fmtSpread(p.market_spread)}, edge ${fmtPoints(p.edge, 1)})`;
}

export function diffSteps(d: Diff, picks: Pick[]): { verb: string; text: string; cls: string }[] {
  return [
    ...d.dropped.map((t) => ({ verb: "Drop", text: t, cls: "step-drop" })),
    ...d.added.map((t) => ({ verb: "Add", text: describe(t, picks), cls: "step-add" })),
    ...d.flipped.map((t) => ({ verb: "Switch sides to", text: describe(t, picks), cls: "step-flip" })),
  ];
}

function Steps({ diff, picks }: { diff: Diff; picks: Pick[] }) {
  const steps = diffSteps(diff, picks);
  if (steps.length === 0) return null;
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={i} className={s.cls}>
          <span className="step-verb">{s.verb}</span> <span className="step-text">{s.text}</span>
        </li>
      ))}
    </ol>
  );
}

export function DoThis({ lastChange, pending, picks }: { lastChange: LastChange | null | undefined; pending: Diff | undefined; picks: Pick[] }) {
  const [showBody, setShowBody] = useState(false);
  if (!lastChange && !pending?.changed) return null;
  return (
    <section className="do-this panel" aria-label="Do this">
      {lastChange && (
        <>
          <div className="do-this-head">
            <span className="do-this-title">Do this</span>
            <span className="do-this-when">
              {fmtDateTime(lastChange.at)} · {lastChange.reason}
            </span>
            <span className={`badge ${lastChange.sent ? "badge-good" : "badge-open"}`}>{lastChange.sent ? "alert sent" : "not sent"}</span>
          </div>
          <Steps diff={lastChange} picks={picks} />
          <p className="hint">Make these changes in the league app, before each game's kickoff.</p>
          <button type="button" className="link-btn" aria-expanded={showBody} onClick={() => setShowBody((v) => !v)}>
            {showBody ? "Hide alert text" : "Show alert text"}
          </button>
          {showBody && <pre className="alert-body">{lastChange.body}</pre>}
        </>
      )}
      {pending?.changed && (
        <div className="pending">
          <span className="do-this-title pending-title">Pending change</span>
          <span className="hint"> The model now suggests this versus the picks you have:</span>
          <Steps diff={pending} picks={picks} />
        </div>
      )}
    </section>
  );
}
