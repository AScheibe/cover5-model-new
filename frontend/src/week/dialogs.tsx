import { useId, useState } from "react";
import type { Game, Pick } from "../api/types";
import { Dialog } from "../components/Dialog";
import { TeamBadge } from "../components/Team";
import { fmtKickoff, fmtPoints, fmtSpread, parseNumber, parseSpreadInput, pickLabel, teamSpread } from "../lib/format";

const FOUR_HOURS = 4 * 3600 * 1000;

function probablyLive(kickoffIso: string, nowIso?: string): boolean {
  const k = new Date(kickoffIso).getTime();
  const now = nowIso ? new Date(nowIso).getTime() : Date.now();
  return now >= k && now - k < FOUR_HOURS;
}

/* ------------------------------------------------------------------ line */

interface LineProps {
  game: Game;
  busy: boolean;
  onSave: (team: string, spread: number | "PK") => void;
  onRevert: (team: string) => void;
  onClose: () => void;
}

export function LineDialog({ game, busy, onSave, onRevert, onClose }: LineProps) {
  const initialTeam = game.picked_team ?? game.best_team ?? game.home;
  const [team, setTeam] = useState(initialTeam);
  const isHome = team === game.home;
  const [text, setText] = useState(() => {
    const v = teamSpread(game.league_home_spread, initialTeam === game.home);
    return v == null ? "" : fmtSpread(v);
  });
  const [err, setErr] = useState<string | null>(null);
  const inputId = useId();
  const opp = isHome ? game.away : game.home;

  const switchTeam = (t: string) => {
    if (t === team) return;
    const p = parseSpreadInput(text);
    if (typeof p === "number") setText(fmtSpread(-p));
    setTeam(t);
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const p = parseSpreadInput(text);
    if (p == null) {
      setErr('Enter a spread like -3.5, +7 or PK');
      return;
    }
    onSave(team, p);
  };

  const sheet = teamSpread(game.sheet_home_spread, isHome);
  const preview = parseSpreadInput(text);

  return (
    <Dialog
      title={`League line: ${game.away} @ ${game.home}`}
      onClose={onClose}
      size="sm"
      description="Enter the spread exactly as the league app shows it, from either team's point of view."
    >
      <form onSubmit={submit} className="form-stack">
        <fieldset className="seg">
          <legend>Spread for</legend>
          {[game.away, game.home].map((t) => (
            <label key={t} className={`seg-opt ${t === team ? "on" : ""}`}>
              <input type="radio" name="line-team" value={t} checked={t === team} onChange={() => switchTeam(t)} />
              {t}
            </label>
          ))}
        </fieldset>
        <label htmlFor={inputId} className="field-label">
          {team} spread
        </label>
        <input
          id={inputId}
          className="input input-lg"
          value={text}
          inputMode="decimal"
          autoComplete="off"
          data-autofocus
          onChange={(e) => {
            setText(e.target.value);
            setErr(null);
          }}
          aria-invalid={err ? true : undefined}
          aria-describedby={err ? `${inputId}-err` : undefined}
        />
        {err && (
          <p id={`${inputId}-err`} className="field-error">
            {err}
          </p>
        )}
        <p className="hint">
          Will read: <strong>{pickLabel(team, opp, preview === "PK" ? 0 : preview, isHome)}</strong>
        </p>
        <p className="hint">
          Sheet: {sheet == null ? "—" : pickLabel(team, opp, sheet, isHome)}
          {game.line_overridden && <span className="star" title="Overridden"> ★ overridden</span>}
        </p>
        <div className="dialog-foot">
          {game.line_overridden && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => onRevert(team)}>
              Revert to sheet ({sheet == null ? "—" : fmtSpread(sheet)})
            </button>
          )}
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save line
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/* ----------------------------------------------------------------- score */

interface ScoreProps {
  game: Game;
  now?: string;
  busy: boolean;
  onSave: (body: { team: string; team_points: number; opponent_points: number; live: boolean }) => void;
  onRevert: (team: string) => void;
  onClose: () => void;
}

export function ScoreDialog({ game, now, busy, onSave, onRevert, onClose }: ScoreProps) {
  const r = game.result;
  const [away, setAway] = useState(r ? String(r.away_score) : "");
  const [home, setHome] = useState(r ? String(r.home_score) : "");
  const [live, setLive] = useState(r ? !r.final : probablyLive(game.kickoff_utc, now));
  const [err, setErr] = useState<string | null>(null);
  const awayId = useId();
  const homeId = useId();
  const liveId = useId();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const a = parseNumber(away);
    const h = parseNumber(home);
    if (a == null || h == null || a < 0 || h < 0) {
      setErr("Enter both scores as non-negative numbers");
      return;
    }
    const team = game.picked_team ?? game.home;
    const teamIsHome = team === game.home;
    onSave({ team, team_points: teamIsHome ? h : a, opponent_points: teamIsHome ? a : h, live });
  };

  return (
    <Dialog title={`Score: ${game.away} @ ${game.home}`} onClose={onClose} size="sm" description={`Kickoff ${fmtKickoff(game.kickoff_utc)}. Enter the score if nflverse is behind or wrong.`}>
      <form onSubmit={submit} className="form-stack">
        <div className="score-inputs">
          <div>
            <label htmlFor={awayId} className="field-label">
              <TeamBadge team={game.away} size="sm" /> {game.away} (away)
            </label>
            <input id={awayId} className="input input-lg" inputMode="numeric" value={away} data-autofocus onChange={(e) => { setAway(e.target.value); setErr(null); }} />
          </div>
          <div>
            <label htmlFor={homeId} className="field-label">
              <TeamBadge team={game.home} size="sm" /> {game.home} (home)
            </label>
            <input id={homeId} className="input input-lg" inputMode="numeric" value={home} onChange={(e) => { setHome(e.target.value); setErr(null); }} />
          </div>
        </div>
        <label htmlFor={liveId} className="check">
          <input id={liveId} type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          Game still in progress (live score)
        </label>
        {err && <p className="field-error">{err}</p>}
        <div className="dialog-foot">
          {r?.source === "score override" && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => onRevert(game.picked_team ?? game.home)}>
              Revert to nflverse
            </button>
          )}
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save score
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- points */

interface PointsProps {
  pick: Pick;
  now?: string;
  hasOverride: boolean;
  busy: boolean;
  onSave: (body: { team: string; points: number; live: boolean }) => void;
  onRevert: (team: string) => void;
  onClose: () => void;
}

export function PointsDialog({ pick, now, hasOverride, busy, onSave, onRevert, onClose }: PointsProps) {
  const graded = pick.status !== "open";
  const [text, setText] = useState(graded ? fmtPoints(pick.points) : "");
  const [live, setLive] = useState(pick.status === "live" || (!graded && probablyLive(pick.kickoff_utc, now)));
  const [err, setErr] = useState<string | null>(null);
  const id = useId();
  const liveId = useId();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const n = parseNumber(text);
    if (n == null) {
      setErr("Enter the points as a number, e.g. +8 or -3.5");
      return;
    }
    onSave({ team: pick.team, points: n, live });
  };

  return (
    <Dialog title={`Points: ${pick.label}`} onClose={onClose} size="sm" description="Enter exactly the points the league app shows for this pick. This also locks the pick.">
      <form onSubmit={submit} className="form-stack">
        <label htmlFor={id} className="field-label">
          Points
        </label>
        <input
          id={id}
          className="input input-lg"
          inputMode="decimal"
          value={text}
          placeholder="+8"
          data-autofocus
          onChange={(e) => {
            setText(e.target.value);
            setErr(null);
          }}
        />
        <label htmlFor={liveId} className="check">
          <input id={liveId} type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          Game still in progress (live)
        </label>
        {err && <p className="field-error">{err}</p>}
        <div className="dialog-foot">
          {hasOverride && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => onRevert(pick.team)}>
              Clear entered points
            </button>
          )}
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save points
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/* --------------------------------------------------------------- replace */

interface ReplaceProps {
  incoming: string;
  incomingLabel: string;
  picks: Pick[];
  busy: boolean;
  onChoose: (out: Pick) => void;
  onClose: () => void;
}

export function ReplaceDialog({ incoming, incomingLabel, picks, busy, onChoose, onClose }: ReplaceProps) {
  return (
    <Dialog
      title={`Replace which pick with ${incoming}?`}
      onClose={onClose}
      size="sm"
      description={<>Your picks are full. {incomingLabel} goes in place of:</>}
    >
      <ul className="replace-list">
        {picks.map((p, i) => (
          <li key={p.team}>
            <button type="button" className="replace-opt" disabled={busy} onClick={() => onChoose(p)} data-autofocus={i === 0 ? true : undefined}>
              <TeamBadge team={p.team} size="sm" />
              <span className="replace-label">{p.label}</span>
              <span className="replace-meta">
                {p.status === "open" ? `edge ${fmtPoints(p.edge, 1)}` : `${p.status} ${fmtPoints(p.points)}`}
                {p.locked && " · locked"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
