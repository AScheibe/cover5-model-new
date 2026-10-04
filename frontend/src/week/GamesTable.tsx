import type { Game } from "../api/types";
import { teamStyle } from "../components/Team";
import { fmtKickoff, fmtPoints, fmtSpread, teamSpread } from "../lib/format";

export interface GameActions {
  onToggleTeam: (game: Game, team: string) => void;
  onEditLine: (game: Game) => void;
  onEditScore: (game: Game) => void;
  onLockToggle: (game: Game) => void;
}

interface Props extends GameActions {
  games: Game[];
  busy: boolean;
}

/** The team whose point of view a row's spreads are shown from. */
export function viewTeam(g: Game): string {
  return g.picked_team ?? g.best_team ?? g.home;
}

function TeamPickButton({ game, team, busy, onToggle }: { game: Game; team: string; busy: boolean; onToggle: () => void }) {
  const picked = game.picked_team === team;
  const best = game.best_team === team;
  return (
    <button
      type="button"
      className={`pick-btn ${picked ? "picked" : ""} ${best ? "best" : ""}`}
      style={teamStyle(team)}
      aria-pressed={picked}
      aria-label={`Pick ${team}`}
      title={best ? `${team} is the side the market moved toward` : undefined}
      disabled={busy}
      onClick={onToggle}
    >
      {team}
      {best && <span className="best-dot" aria-hidden="true" />}
    </button>
  );
}

function ResultCell({ game, busy, onEdit }: { game: Game; busy: boolean; onEdit: () => void }) {
  const r = game.result;
  const label = `Enter score for ${game.away} @ ${game.home}`;
  if (!r) {
    return (
      <button type="button" className="cell-btn muted" disabled={busy} onClick={onEdit} aria-label={label}>
        {game.locked ? "enter score" : "—"}
      </button>
    );
  }
  return (
    <button type="button" className="cell-btn" disabled={busy} onClick={onEdit} aria-label={label}>
      <span className="score">
        {game.away} {r.away_score} – {r.home_score} {game.home}
      </span>{" "}
      <span className={`badge ${r.final ? "badge-final" : "badge-live"}`}>{r.final ? "FINAL" : "LIVE"}</span>
      {r.source === "score override" && (
        <span className="star" title="Score entered by you">
          {" "}
          ★
        </span>
      )}
    </button>
  );
}

export function GamesTable({ games, busy, onToggleTeam, onEditLine, onEditScore, onLockToggle }: Props) {
  const sorted = [...games].sort((a, b) => a.kickoff_utc.localeCompare(b.kickoff_utc) || a.game_id.localeCompare(b.game_id));
  return (
    <div className="table-wrap">
      <table className="games-table">
        <thead>
          <tr>
            <th scope="col">Kickoff</th>
            <th scope="col">Game</th>
            <th scope="col">Pick</th>
            <th scope="col">League line</th>
            <th scope="col">Market</th>
            <th scope="col">Edge</th>
            <th scope="col">Result</th>
            <th scope="col">Lock</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((g) => {
            const vt = viewTeam(g);
            const isHome = vt === g.home;
            const league = teamSpread(g.league_home_spread, isHome);
            const sheet = teamSpread(g.sheet_home_spread, isHome);
            const market = teamSpread(g.market_home_spread, isHome);
            return (
              <tr key={g.game_id} className={`${g.locked ? "kicked" : ""} ${g.picked_team ? "is-picked" : ""}`} data-game={g.game_id}>
                <td className="nowrap">
                  {fmtKickoff(g.kickoff_utc)}
                  {g.locked && <span className="badge badge-locked tiny">KICKED OFF</span>}
                </td>
                <td className="nowrap matchup">
                  {g.away} <span className="muted">@</span> {g.home}
                </td>
                <td>
                  <div className="pick-btns">
                    <TeamPickButton game={g} team={g.away} busy={busy} onToggle={() => onToggleTeam(g, g.away)} />
                    <TeamPickButton game={g} team={g.home} busy={busy} onToggle={() => onToggleTeam(g, g.home)} />
                  </div>
                </td>
                <td className="nowrap">
                  <button
                    type="button"
                    className={`cell-btn ${g.line_overridden ? "overridden" : ""}`}
                    disabled={busy}
                    onClick={() => onEditLine(g)}
                    aria-label={`Edit league line for ${g.away} @ ${g.home}`}
                    title={g.line_overridden ? `Overridden; sheet had ${vt} ${fmtSpread(sheet)}` : "Click to correct the league line"}
                  >
                    {vt} {fmtSpread(league)}
                    {g.line_overridden && <span className="star"> ★</span>}
                  </button>
                  {g.line_overridden && <span className="sheet-note">sheet {fmtSpread(sheet)}</span>}
                </td>
                <td className="nowrap" title={`${g.books} book${g.books === 1 ? "" : "s"}`}>
                  {market == null ? "—" : `${vt} ${fmtSpread(market)}`}
                  {g.books > 0 && <span className="muted small"> ({g.books})</span>}
                </td>
                <td className="nowrap">
                  {g.edge == null || g.best_team == null ? (
                    <span className="muted">—</span>
                  ) : (
                    <span className={`edge ${g.edge > 0 ? "pos" : "zero"}`}>
                      {fmtPoints(g.edge, 1)} <strong>{g.best_team}</strong>
                    </span>
                  )}
                </td>
                <td className="nowrap">
                  <ResultCell game={g} busy={busy} onEdit={() => onEditScore(g)} />
                </td>
                <td>
                  {g.picked_team && (
                    <button
                      type="button"
                      className={`lock-btn ${g.lock ? "on" : ""}`}
                      aria-pressed={!!g.lock}
                      aria-label={`Lock ${g.picked_team}`}
                      disabled={busy || (g.locked && !g.lock)}
                      title={g.lock ? "Locked by you: click to unlock" : g.locked ? "Kicked off: locked by the league" : "Lock this pick"}
                      onClick={() => onLockToggle(g)}
                    >
                      {g.lock ? "Locked" : g.locked ? "Kicked off" : "Lock"}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
