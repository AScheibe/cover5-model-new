import { useState } from "react";
import type { Diff, Game, LastChange, Pick, Todo } from "../api/types";
import { Spinner } from "../components/Spinner";
import { hasKickedOff } from "../lib/clock";
import { fmtDateTime, fmtPoints, fmtSpread, pickLabel, teamSpread } from "../lib/format";

function describe(team: string, picks: Pick[]): string {
  const p = picks.find((x) => x.team === team);
  if (!p) return team;
  return `${p.label} (market ${fmtSpread(p.market_spread)}, edge ${fmtPoints(p.edge, 1)})`;
}

/** A team that is no longer a pick, as the league app shows it: "DET +3.5 @ CAR". */
function describeDropped(team: string, games: Game[]): string {
  const g = games.find((x) => x.home === team || x.away === team);
  if (!g) return team;
  const isHome = g.home === team;
  return pickLabel(team, isHome ? g.away : g.home, teamSpread(g.league_home_spread, isHome), isHome);
}

/**
 * The steps you can still act on: the server leaves out games that had kicked
 * off when the view was computed, and this drops the ones that have kicked
 * off since (`now` is the ticking clock), since the league app locks them.
 */
export function openSteps(d: Diff, games: Game[] = [], now?: string): Diff {
  const open = (team: string) => {
    const g = games.find((x) => x.home === team || x.away === team);
    return !g || !hasKickedOff(g.kickoff_utc, now);
  };
  const added = d.added.filter(open);
  const dropped = d.dropped.filter(open);
  const flipped = d.flipped.filter(open);
  return { changed: added.length + dropped.length + flipped.length > 0, added, dropped, flipped };
}

export function diffSteps(d: Diff, picks: Pick[], games: Game[] = []): { verb: string; text: string; cls: string }[] {
  return [
    ...d.dropped.map((t) => ({ verb: "Drop", text: describeDropped(t, games), cls: "step-drop" })),
    ...d.added.map((t) => ({ verb: "Add", text: describe(t, picks), cls: "step-add" })),
    ...d.flipped.map((t) => ({ verb: "Switch sides to", text: describe(t, picks), cls: "step-flip" })),
  ];
}

function Steps({ diff, picks, games }: { diff: Diff; picks: Pick[]; games: Game[] }) {
  const steps = diffSteps(diff, picks, games);
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

function since(todo: Todo): string {
  if (todo.confirmed == null) return "You haven't confirmed your picks in the app this week, so this is the full set to enter.";
  if (todo.since) return `Every change since you confirmed your picks (${fmtDateTime(todo.since)}).`;
  return "Every change since you last told the tracker your picks.";
}

interface DoThisProps {
  /** The model's picks versus the ones you confirmed (server side, docs/api.md). */
  todo: Todo | undefined;
  /** The latest run that changed picks: shown for its alert, not for the steps. */
  lastChange: LastChange | null | undefined;
  picks: Pick[];
  games?: Game[];
  /** The ticking server clock; steps for games kicked off by then are hidden. */
  now?: string;
  busy?: boolean;
  confirming?: boolean;
  onConfirm?: () => void;
}

export function DoThis({ todo, lastChange, picks, games = [], now, busy = false, confirming = false, onConfirm }: DoThisProps) {
  const [showBody, setShowBody] = useState(false);
  const steps = todo ? openSteps(todo, games, now) : null;
  if (!todo || !steps?.changed) return null;
  return (
    <section className="do-this panel" aria-label="Do this">
      <div className="do-this-head">
        <span className="do-this-title">Do this</span>
        <span className="do-this-when">{since(todo)}</span>
      </div>
      <Steps diff={steps} picks={picks} games={games} />
      <p className="hint">Make these changes in the league app before each game's kickoff, then mark them done.</p>
      <div className="do-this-foot">
        {onConfirm && (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={onConfirm}>
            {confirming && <Spinner label="Saving" />} I've made these changes
          </button>
        )}
        {lastChange && (
          <span className="do-this-when small">
            Last alert {fmtDateTime(lastChange.at)} · {lastChange.reason}{" "}
            <span className={`badge ${lastChange.sent ? "badge-good" : "badge-open"}`}>{lastChange.sent ? "alert sent" : "not sent"}</span>
          </span>
        )}
        {lastChange && (
          <button type="button" className="link-btn" aria-expanded={showBody} onClick={() => setShowBody((v) => !v)}>
            {showBody ? "Hide alert text" : "Show alert text"}
          </button>
        )}
      </div>
      {showBody && lastChange && <pre className="alert-body">{lastChange.body}</pre>}
    </section>
  );
}
