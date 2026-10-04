import type { Pick } from "../api/types";
import { TeamBadge, teamStyle } from "../components/Team";
import { hasKickedOff } from "../lib/clock";
import { fmtDateTime, fmtKickoff, fmtPoints, fmtSpread, signClass } from "../lib/format";

export interface PickActions {
  onLockToggle: (pick: Pick) => void;
  onPoints: (pick: Pick) => void;
  onRemove: (pick: Pick) => void;
}

interface Props extends PickActions {
  picks: Pick[];
  nPicks: number;
  busy: boolean;
  now?: string;
}

export function statusBadges(p: Pick): { text: string; cls: string }[] {
  const out: { text: string; cls: string }[] = [];
  if (p.status === "final") out.push({ text: "FINAL", cls: "badge-final" });
  else if (p.status === "live") out.push({ text: "LIVE", cls: "badge-live" });
  if (p.manual) out.push({ text: "LOCKED by you", cls: "badge-mine" });
  else if (p.locked && p.status === "open") out.push({ text: "LOCKED", cls: "badge-locked" });
  if (out.length === 0) out.push({ text: "open", cls: "badge-open" });
  return out;
}

/** `locked` without a user lock means kicked off; otherwise compare the (ticking) clock. */
export function pickKickedOff(p: Pick, now?: string): boolean {
  if (p.locked && !p.manual) return true;
  return hasKickedOff(p.kickoff_utc, now);
}

export function PickTile({ pick, busy, now, onLockToggle, onPoints, onRemove }: { pick: Pick; busy: boolean; now?: string } & PickActions) {
  const open = pick.status === "open";
  const started = pickKickedOff(pick, now);
  return (
    <article className={`pick-tile pick-${pick.status}`} style={teamStyle(pick.team)} aria-label={`Pick ${pick.label}`}>
      <div className="pick-stripe" aria-hidden="true" />
      <div className="pick-head">
        <TeamBadge team={pick.team} size="lg" />
        <div className={`pick-points ${signClass(pick.points)} ${open ? "expected" : ""}`}>
          <span className="pick-points-num" data-testid={`points-${pick.team}`}>
            {open ? fmtPoints(pick.points, 1) : fmtPoints(pick.points)}
          </span>
          <span className="pick-points-unit">{open ? "expected" : pick.status === "live" ? "live pts" : "points"}</span>
        </div>
      </div>
      <div className="pick-label">
        {pick.label}
        {pick.line_overridden && (
          <span className="star" title="League line set by your override" aria-label="league line overridden">
            ★
          </span>
        )}
      </div>
      <div className="pick-meta">
        Market {fmtSpread(pick.market_spread)} · edge {fmtPoints(pick.edge, 1)} · <span className="nowrap">{fmtKickoff(pick.kickoff_utc)}</span>
        {pick.source !== "edge" && pick.source !== "nflverse" && <span className="pick-src"> · {pick.source}</span>}
      </div>
      <div className="pick-badges">
        {statusBadges(pick).map((b) => (
          <span key={b.text} className={`badge ${b.cls}`}>
            {b.text}
          </span>
        ))}
      </div>
      <div className="pick-actions">
        {pick.manual ? (
          <button type="button" className="btn btn-xs" disabled={busy} onClick={() => onLockToggle(pick)} aria-label={`Unlock ${pick.team}`}>
            Unlock
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-xs"
            disabled={busy || started}
            title={started ? "Kicked off: locked by the league" : "Pin this pick so the model never drops it"}
            onClick={() => onLockToggle(pick)}
            aria-label={`Lock ${pick.team}`}
          >
            Lock
          </button>
        )}
        <button
          type="button"
          className="btn btn-xs"
          disabled={busy || !started}
          title={
            started
              ? "Enter the points the league app shows for this pick"
              : `Points can be entered once the game kicks off (${fmtDateTime(pick.kickoff_utc)}). If the schedule's kickoff time is wrong, use the game's score in the table.`
          }
          onClick={() => onPoints(pick)}
          aria-label={`Enter points for ${pick.team}`}
        >
          Points
        </button>
        <button type="button" className="btn btn-xs btn-ghost" disabled={busy} onClick={() => onRemove(pick)} aria-label={`Remove ${pick.team} from picks`}>
          Remove
        </button>
      </div>
    </article>
  );
}

export function PicksStrip({ picks, nPicks, busy, now, ...actions }: Props) {
  const empty = Math.max(0, nPicks - picks.length);
  return (
    <section className="picks-strip" aria-label="Your picks">
      {picks.map((p) => (
        <PickTile key={p.team} pick={p} busy={busy} now={now} {...actions} />
      ))}
      {Array.from({ length: empty }, (_, i) => (
        <div key={`empty-${i}`} className="pick-tile pick-empty" aria-label="Empty pick slot">
          <span>Open slot</span>
          <small>Pick a team in the table below</small>
        </div>
      ))}
    </section>
  );
}
