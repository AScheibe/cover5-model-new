import { useEffect, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Game, Pick } from "../api/types";
import { ConfirmDialog } from "../components/Dialog";
import { useMeta } from "../components/MetaContext";
import { Loading } from "../components/Spinner";
import { replacePick, togglePick } from "../lib/picks";
import { pickLabel, teamSpread } from "../lib/format";
import { AlertLog } from "../week/AlertLog";
import { DoThis } from "../week/DoThis";
import { GamesTable } from "../week/GamesTable";
import { LineDialog, PointsDialog, ReplaceDialog, ScoreDialog } from "../week/dialogs";
import { MovementChart } from "../week/MovementChart";
import { PicksStrip, pickKickedOff } from "../week/PicksStrip";
import { SetupWeek } from "../week/SetupWeek";
import { SummaryBar } from "../week/SummaryBar";
import { chain, useWeek } from "../week/useWeek";
import { WeekHeader, useSchedule } from "../week/WeekHeader";

type DialogState =
  | { kind: "line"; game: Game }
  | { kind: "score"; game: Game }
  | { kind: "points"; pick: Pick }
  | { kind: "replace"; team: string; game: Game }
  | { kind: "confirm"; title: string; message: string; confirmLabel: string; action: () => void }
  | null;

export function CurrentWeekRedirect() {
  const { meta, error, refresh } = useMeta();
  if (meta) return <Navigate to={`/week/${meta.current.season}/${meta.current.week}`} replace />;
  if (error)
    return (
      <div className="panel empty-state">
        <h2>Can't reach the Cover 5 server</h2>
        <p>{error}</p>
        <p className="hint">
          Start it with <code>python -m cover5 serve</code>, then retry.
        </p>
        <button type="button" className="btn btn-primary" onClick={() => void refresh()}>
          Retry
        </button>
      </div>
    );
  return <Loading what="current week" />;
}

export function WeekPage() {
  const params = useParams();
  const season = Number(params.season);
  const week = Number(params.week);
  if (!Number.isInteger(season) || !Number.isInteger(week) || week < 1) {
    return (
      <div className="panel empty-state">
        <h2>Unknown week</h2>
        <p>Pick a season and week from the menu.</p>
      </div>
    );
  }
  return <WeekScreen key={`${season}-${week}`} season={season} week={week} />;
}

function WeekScreen({ season, week }: { season: number; week: number }) {
  const { meta } = useMeta();
  const { view, loadError, busy, version, reload, run } = useWeek(season, week);
  const weeks = useSchedule(season, version);
  const [dialog, setDialog] = useState<DialogState>(null);

  // A background scheduler update changes last_update_at; pick it up when idle.
  const lastUpdate = meta?.scheduler.last_update_at ?? null;
  const seenUpdate = useRef(lastUpdate);
  useEffect(() => {
    if (lastUpdate !== seenUpdate.current) {
      seenUpdate.current = lastUpdate;
      if (busy == null && dialog == null) void reload();
    }
  }, [lastUpdate, busy, dialog, reload]);

  const nPicks = view?.n_picks ?? meta?.config.n_picks ?? 5;
  const picks = view?.picks ?? [];
  const games = view?.games ?? [];
  const isBusy = busy != null;
  const close = () => setDialog(null);
  const confirmKickoff = (what: string, action: () => void) =>
    setDialog({
      kind: "confirm",
      title: "This game has kicked off",
      message: `${what} has kicked off; only do this if it matches the app.`,
      confirmLabel: "Yes, it matches the app",
      action,
    });

  const gameOf = (team: string) => games.find((g) => g.home === team || g.away === team);

  /** PUT the full new set. Teams leaving the set that you locked are unlocked first, or the lock would keep them. */
  const submitPicks = (teams: string[]) => {
    const leaving = picks.filter((p) => p.manual && !teams.includes(p.team)).map((p) => p.team);
    close();
    void run("picks", async () => {
      if (leaving.length === 0) return api.setPicks(season, week, teams);
      let first = () => api.unlock(season, week, leaving[0]!);
      for (const t of leaving.slice(1)) {
        const prev = first;
        first = () => chain(prev, () => api.unlock(season, week, t));
      }
      return chain(first, () => api.setPicks(season, week, teams));
    });
  };

  const onToggleTeam = (game: Game, team: string) => {
    const current = picks.map((p) => p.team);
    const res = togglePick(current, team, game, nPicks);
    if (res.kind === "choose") {
      setDialog({ kind: "replace", team, game });
      return;
    }
    const go = () => submitPicks(res.teams);
    if (game.locked) confirmKickoff(`${game.away} @ ${game.home}`, go);
    else go();
  };

  const onReplace = (team: string, game: Game, out: Pick) => {
    const teams = replacePick(picks.map((p) => p.team), out.team, team);
    const go = () => submitPicks(teams);
    const outGame = gameOf(out.team);
    if (game.locked || pickKickedOff(out, view?.now) || outGame?.locked) {
      const which = game.locked ? `${game.away} @ ${game.home}` : `${out.label}`;
      confirmKickoff(which, go);
    } else go();
  };

  const onRemovePick = (p: Pick) => {
    const go = () => submitPicks(picks.filter((x) => x.team !== p.team).map((x) => x.team));
    if (pickKickedOff(p, view?.now)) confirmKickoff(p.label, go);
    else go();
  };

  const lockTeam = (team: string, locked: boolean) =>
    void run("lock", () => (locked ? api.unlock(season, week, team) : api.lock(season, week, [team])));

  if (loadError && !view) {
    return (
      <>
        <WeekHeader season={season} week={week} seasons={meta?.seasons ?? []} view={null} busy={busy} scheduler={meta?.scheduler} onFetch={() => {}} weeks={weeks} />
        <div className="panel empty-state">
          <h2>Couldn't load {season} week {week}</h2>
          <p className="field-error">{loadError}</p>
          <button type="button" className="btn btn-primary" onClick={() => void reload()}>
            Retry
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <WeekHeader
        season={season}
        week={week}
        seasons={meta?.seasons ?? []}
        view={view}
        busy={busy}
        scheduler={meta?.scheduler}
        weeks={weeks}
        onFetch={() => void run("update", () => api.update(season, week, {}))}
      />
      {!view ? (
        <Loading what={`week ${week}`} />
      ) : !view.has_league_file ? (
        <SetupWeek
          season={season}
          week={week}
          info={weeks?.find((w) => w.week === week)}
          busy={busy}
          onInit={(useMarket) => void run("init", () => api.init(season, week, { use_market: useMarket }))}
        />
      ) : (
        <div className="week-grid">
          <DoThis lastChange={view.last_change} pending={view.diff} picks={picks} />
          <PicksStrip
            picks={picks}
            nPicks={nPicks}
            busy={isBusy}
            now={view.now}
            onLockToggle={(p) => lockTeam(p.team, p.manual)}
            onPoints={(p) => setDialog({ kind: "points", pick: p })}
            onRemove={onRemovePick}
          />
          {view.summary && <SummaryBar summary={view.summary} />}
          {view.warnings && view.warnings.length > 0 && (
            <section className="panel warnings" aria-label="Warnings">
              <h2>Warnings</h2>
              <ul>
                {view.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </section>
          )}
          <section className="panel" aria-label="Games">
            <div className="panel-head">
              <h2>Games</h2>
              <span className="muted small">
                Click a team to pick it, a line to correct it, a result to enter a score. Spreads are from the named team's view.
              </span>
            </div>
            <GamesTable
              games={games}
              busy={isBusy}
              onToggleTeam={onToggleTeam}
              onEditLine={(g) => setDialog({ kind: "line", game: g })}
              onEditScore={(g) => setDialog({ kind: "score", game: g })}
              onLockToggle={(g) => g.picked_team && lockTeam(g.picked_team, !!g.lock)}
            />
          </section>
          <div className="two-col">
            <MovementChart season={season} week={week} games={games} picks={picks} version={version} />
            <AlertLog season={season} week={week} version={version} />
          </div>
        </div>
      )}

      {dialog?.kind === "line" && (
        <LineDialog
          game={dialog.game}
          busy={isBusy}
          onClose={close}
          onSave={(team, spread) => {
            close();
            void run("line", () => api.setLine(season, week, team, spread));
          }}
          onRevert={(team) => {
            close();
            void run("line", () => api.clearOverride(season, week, team, ["line"]));
          }}
        />
      )}
      {dialog?.kind === "score" && (
        <ScoreDialog
          game={dialog.game}
          now={view?.now}
          busy={isBusy}
          onClose={close}
          onSave={(body) => {
            close();
            void run("score", () => api.setScore(season, week, body));
          }}
          onRevert={(team) => {
            close();
            void run("score", () => api.clearOverride(season, week, team, ["score"]));
          }}
        />
      )}
      {dialog?.kind === "points" && (
        <PointsDialog
          pick={dialog.pick}
          now={view?.now}
          busy={isBusy}
          hasOverride={(view?.overrides?.points ?? []).some((o) => o.game_id === dialog.pick.game_id)}
          onClose={close}
          onSave={(body) => {
            close();
            void run("points", () => api.setPoints(season, week, body));
          }}
          onRevert={(team) => {
            close();
            void run("points", () => api.clearOverride(season, week, team, ["points"]));
          }}
        />
      )}
      {dialog?.kind === "replace" && (
        <ReplaceDialog
          incoming={dialog.team}
          incomingLabel={pickLabel(
            dialog.team,
            dialog.team === dialog.game.home ? dialog.game.away : dialog.game.home,
            teamSpread(dialog.game.league_home_spread, dialog.team === dialog.game.home),
            dialog.team === dialog.game.home,
          )}
          picks={picks}
          busy={isBusy}
          onClose={close}
          onChoose={(out) => onReplace(dialog.team, dialog.game, out)}
        />
      )}
      {dialog?.kind === "confirm" && (
        <ConfirmDialog
          title={dialog.title}
          message={dialog.message}
          confirmLabel={dialog.confirmLabel}
          onCancel={close}
          onConfirm={() => {
            const a = dialog.action;
            close();
            a();
          }}
        />
      )}
    </>
  );
}
