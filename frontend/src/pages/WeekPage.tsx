import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Game, Pick } from "../api/types";
import { ConfirmDialog } from "../components/Dialog";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { useMeta } from "../components/MetaContext";
import { Loading } from "../components/Spinner";
import { useServerNow } from "../lib/clock";
import { replacePick, togglePick } from "../lib/picks";
import { pickLabel, teamSpread } from "../lib/format";
import { AlertLog } from "../week/AlertLog";
import { DoThis } from "../week/DoThis";
import { GamesTable, gameStarted } from "../week/GamesTable";
import { LineDialog, LockPickDialog, PointsDialog, ReplaceDialog, ReseedDialog, ScoreDialog } from "../week/dialogs";
import { MovementChart } from "../week/MovementChart";
import { PicksStrip, pickKickedOff } from "../week/PicksStrip";
import { SetupWeek } from "../week/SetupWeek";
import { SummaryBar } from "../week/SummaryBar";
import { chain, useWeek } from "../week/useWeek";
import { WeekHeader, useSchedule } from "../week/WeekHeader";

type DialogState =
  | { kind: "line"; game: Game }
  | { kind: "score"; game: Game }
  | { kind: "points"; pick: Pick; forced?: boolean }
  | { kind: "replace"; team: string; game: Game }
  | { kind: "lockpick"; game: Game }
  | { kind: "reseed" }
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

/** "IND -3.5 @ WAS" for either team of a game, from the frozen league line. */
function teamLabel(game: Game, team: string): string {
  const isHome = team === game.home;
  return pickLabel(team, isHome ? game.away : game.home, teamSpread(game.league_home_spread, isHome), isHome);
}

function WeekScreen({ season, week }: { season: number; week: number }) {
  const { meta, refresh: refreshMeta } = useMeta();
  const { view, loadError, busy, version, reload, run } = useWeek(season, week);
  const weeks = useSchedule(season, version);
  const [dialog, setDialog] = useState<DialogState>(null);
  // The server's clock, ticking: kickoff-dependent UI updates without a refetch.
  const now = useServerNow(view?.now);

  const nPicks = view?.n_picks ?? meta?.config.n_picks ?? 5;
  const picks = view?.picks ?? [];
  const games = view?.games ?? [];
  const isBusy = busy != null;
  const started = (g: Game) => gameStarted(g, now);
  const anyStarted = games.some(started);
  const allStarted = games.length > 0 && games.every(started);

  // Reload when idle after a background update (the scheduler, the CLI) or a
  // kickoff. A reload that comes due while a dialog is open or a request runs
  // waits until both are done instead of being dropped.
  const lastUpdate = meta?.scheduler.last_update_at ?? null;
  const seenUpdate = useRef(lastUpdate);
  const pendingReload = useRef(false);
  const reloadedForKick = useRef<string | null>(null);
  const nextKick = useMemo(() => {
    if (!view?.now) return null;
    const t0 = new Date(view.now).getTime();
    const later = games.map((g) => g.kickoff_utc).filter((k) => new Date(k).getTime() > t0);
    return later.length ? later.reduce((a, b) => (new Date(a) < new Date(b) ? a : b)) : null;
  }, [view?.now, games]);
  useEffect(() => {
    if (lastUpdate !== seenUpdate.current) {
      seenUpdate.current = lastUpdate;
      pendingReload.current = true;
    }
    if (nextKick && now && reloadedForKick.current !== nextKick && new Date(now).getTime() >= new Date(nextKick).getTime() + 5000) {
      reloadedForKick.current = nextKick;
      pendingReload.current = true;
    }
    if (pendingReload.current && busy == null && dialog == null) {
      pendingReload.current = false;
      void reload();
    }
  }, [lastUpdate, busy, dialog, reload, now, nextKick]);

  const close = () => setDialog(null);
  /** Changing a game that has kicked off: say exactly what will happen. */
  const confirmKickoff = (title: string, what: string, confirmLabel: string, action: () => void) =>
    setDialog({
      kind: "confirm",
      title,
      message: `${what} The league app locks a pick at kickoff, so only do this if it matches your picks there.`,
      confirmLabel,
      action,
    });

  const gameOf = (team: string) => games.find((g) => g.home === team || g.away === team);

  /**
   * PUT the full new set. `lock`: teams you picked by hand, locked so the model
   * keeps them (it would otherwise swap a team it likes less straight back).
   * Teams leaving the set that you locked are unlocked first, or the lock would keep them.
   */
  const submitPicks = (teams: string[], lock: string[] = []) => {
    const leaving = picks.filter((p) => p.manual && !teams.includes(p.team)).map((p) => p.team);
    close();
    void run("picks", async () => {
      if (leaving.length === 0) return api.setPicks(season, week, teams, lock);
      let first = () => api.unlock(season, week, leaving[0]!);
      for (const t of leaving.slice(1)) {
        const prev = first;
        first = () => chain(prev, () => api.unlock(season, week, t));
      }
      return chain(first, () => api.setPicks(season, week, teams, lock));
    });
  };

  const onToggleTeam = (game: Game, team: string) => {
    const current = picks.map((p) => p.team);
    const res = togglePick(current, team, game, nPicks);
    if (res.kind === "choose") {
      setDialog({ kind: "replace", team, game });
      return;
    }
    const kicked = started(game);
    const lock = res.action === "remove" || kicked ? [] : [team];
    const go = () => submitPicks(res.teams, lock);
    if (!kicked) return go();
    const matchup = `${game.away} @ ${game.home} has kicked off.`;
    if (res.action === "remove") {
      const p = picks.find((x) => x.team === team);
      confirmKickoff(`Remove ${team} from your picks?`, `${matchup} This removes ${p?.label ?? team} from your picks and its points from the week.`, `Remove ${team}`, go);
    } else if (res.action === "switch") {
      confirmKickoff(`Switch from ${res.from} to ${team}?`, `${matchup} This replaces ${res.from} with ${teamLabel(game, team)} in that game.`, `Switch to ${team}`, go);
    } else {
      confirmKickoff(`Add ${team} to your picks?`, `${matchup} This adds ${teamLabel(game, team)} in an open slot.`, `Add ${team}`, go);
    }
  };

  const onReplace = (team: string, game: Game, out: Pick) => {
    const teams = replacePick(picks.map((p) => p.team), out.team, team);
    const go = () => submitPicks(teams, started(game) ? [] : [team]);
    const outGame = gameOf(out.team);
    if (started(game) || pickKickedOff(out, now) || (outGame && started(outGame))) {
      const which = started(game) ? `${game.away} @ ${game.home} has kicked off.` : `${out.label} has kicked off.`;
      confirmKickoff(`Replace ${out.team} with ${team}?`, `${which} This replaces ${out.label} with ${teamLabel(game, team)}.`, `Replace ${out.team} with ${team}`, go);
    } else go();
  };

  const onRemovePick = (p: Pick) => {
    const go = () => submitPicks(picks.filter((x) => x.team !== p.team).map((x) => x.team));
    if (pickKickedOff(p, now))
      confirmKickoff(`Remove ${p.team} from your picks?`, `${p.label} has kicked off. This removes it from your picks and its points from the week.`, `Remove ${p.team}`, go);
    else go();
  };

  const lockTeam = (team: string, locked: boolean) =>
    void run("lock", () => (locked ? api.unlock(season, week, team) : api.lock(season, week, [team])));

  const header = (
    <WeekHeader
      season={season}
      week={week}
      seasons={meta?.seasons ?? []}
      view={loadError && !view ? null : view}
      busy={busy}
      scheduler={meta?.scheduler}
      weeks={weeks}
      results={meta?.results}
      started={anyStarted}
      finished={allStarted}
      onFetch={() => void run("update", () => api.update(season, week, {}))}
      onSendAlert={() => void run("alert", () => api.update(season, week, { force_alert: true }))}
      onRefreshResults={() =>
        void run("results", async () => {
          const o = await api.results(season, week);
          void refreshMeta(); // the header shows when results were last downloaded
          return o;
        })
      }
    />
  );

  if (loadError && !view) {
    return (
      <>
        {header}
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

  const scorePick = dialog?.kind === "score" ? picks.find((p) => p.game_id === dialog.game.game_id) : undefined;

  return (
    <>
      {header}
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
        <div className="week-grid" aria-busy={isBusy}>
          <DoThis
            todo={view.todo}
            lastChange={view.last_change}
            picks={picks}
            games={games}
            now={now}
            busy={isBusy}
            confirming={busy === "confirm"}
            onConfirm={() => void run("confirm", () => api.confirm(season, week))}
          />
          <PicksStrip
            picks={picks}
            nPicks={nPicks}
            busy={isBusy}
            now={now}
            onLockToggle={(p) => lockTeam(p.team, p.manual)}
            onPoints={(p) => setDialog({ kind: "points", pick: p })}
            onRemove={onRemovePick}
          />
          {view.summary && <SummaryBar summary={view.summary} picks={picks} now={now} />}
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
                Click a team to record it as one of your picks (it is locked so the model keeps it), a line to correct it, a result to enter a
                score. Spreads are from the named team's view.
              </span>
            </div>
            <GamesTable
              games={games}
              busy={isBusy}
              now={now}
              onToggleTeam={onToggleTeam}
              onEditLine={(g) => setDialog({ kind: "line", game: g })}
              onEditScore={(g) => setDialog({ kind: "score", game: g })}
              onLockToggle={(g) => g.picked_team && lockTeam(g.picked_team, !!g.lock)}
              onLockOther={(g) => setDialog({ kind: "lockpick", game: g })}
            />
            <div className="table-foot">
              <button type="button" className="link-btn small" disabled={isBusy} onClick={() => setDialog({ kind: "reseed" })}>
                Re-seed league lines...
              </button>
            </div>
          </section>
          <div className="two-col">
            <ErrorBoundary label="Line movement" resetKey={version}>
              <MovementChart season={season} week={week} games={games} picks={picks} version={version} />
            </ErrorBoundary>
            <ErrorBoundary label="Alert log" resetKey={version}>
              <AlertLog season={season} week={week} version={version} />
            </ErrorBoundary>
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
          now={now}
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
          onPoints={scorePick ? (force) => setDialog({ kind: "points", pick: scorePick, forced: force }) : undefined}
        />
      )}
      {dialog?.kind === "points" && (
        <PointsDialog
          pick={dialog.pick}
          now={now}
          busy={isBusy}
          forced={dialog.forced}
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
          incomingLabel={teamLabel(dialog.game, dialog.team)}
          picks={picks}
          busy={isBusy}
          onClose={close}
          onChoose={(out) => onReplace(dialog.team, dialog.game, out)}
        />
      )}
      {dialog?.kind === "lockpick" && (
        <LockPickDialog
          game={dialog.game}
          busy={isBusy}
          onClose={close}
          onLock={(team) => {
            close();
            void run("lock", () => api.lock(season, week, [team]));
          }}
        />
      )}
      {dialog?.kind === "reseed" && (
        <ReseedDialog
          busy={isBusy}
          onClose={close}
          onReseed={(useMarket) => {
            close();
            void run("init", () => api.init(season, week, { overwrite: true, use_market: useMarket }));
          }}
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
