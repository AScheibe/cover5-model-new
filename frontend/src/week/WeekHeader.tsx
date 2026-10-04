import { useEffect, useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ResultsInfo, ScheduleWeek, SchedulerStatus, WeekView } from "../api/types";
import { SchedulerPill } from "../components/SchedulerPill";
import { Spinner } from "../components/Spinner";
import { fmtAgo, fmtDateTime, fmtShortDate } from "../lib/format";

interface Props {
  season: number;
  week: number;
  seasons: number[];
  view: WeekView | null;
  busy: string | null;
  scheduler: SchedulerStatus | null | undefined;
  onFetch: () => void;
  weeks: ScheduleWeek[] | null;
  /** Fetch lines and push the alert even if nothing changed (CLI: update --force-alert). */
  onSendAlert?: () => void;
  /** Download nflverse results now. Shown once a game has kicked off. */
  onRefreshResults?: () => void;
  results?: ResultsInfo | null;
  /** Some game of the week has kicked off (results matter). */
  started?: boolean;
  /** Every game has kicked off: there are no lines left to fetch. */
  finished?: boolean;
}

export function useSchedule(season: number, version = 0) {
  const [weeks, setWeeks] = useState<ScheduleWeek[] | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .schedule(season)
      .then((w) => alive && setWeeks(w))
      .catch(() => alive && setWeeks(null));
    return () => {
      alive = false;
    };
  }, [season, version]);
  return weeks;
}

export function WeekHeader({ season, week, seasons, view, busy, scheduler, onFetch, weeks, onSendAlert, onRefreshResults, results, started, finished }: Props) {
  const navigate = useNavigate();
  const sid = useId();
  const wid = useId();
  const seasonList = seasons.includes(season) ? seasons : [...seasons, season].sort((a, b) => b - a);
  const options: Partial<ScheduleWeek>[] = weeks ?? Array.from({ length: 18 }, (_, i) => ({ week: i + 1 }));
  if (!options.some((o) => o.week === week)) options.push({ week });
  const weekNums = options.map((w) => w.week ?? 0);
  const maxWeek = Math.max(...weekNums, week);
  const fetchedAt = view?.market?.fetched_at ?? null;

  return (
    <header className="week-header">
      <div className="week-nav">
        <button type="button" className="btn btn-icon" aria-label="Previous week" disabled={week <= 1} onClick={() => navigate(`/week/${season}/${week - 1}`)}>
          ‹
        </button>
        <label htmlFor={sid} className="sr-only">
          Season
        </label>
        <select id={sid} className="select select-season" value={season} onChange={(e) => navigate(`/week/${e.target.value}/${week}`)}>
          {seasonList.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label htmlFor={wid} className="sr-only">
          Week
        </label>
        <select id={wid} className="select" value={week} onChange={(e) => navigate(`/week/${season}/${e.target.value}`)}>
          {options.map((w) => (
            <option key={w.week} value={w.week}>
              Week {w.week}
              {w.first_kickoff ? ` · ${fmtShortDate(w.first_kickoff)}` : ""}
              {w.has_league_file ? " · set up" : ""}
              {w.has_record ? " · recorded" : ""}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-icon" aria-label="Next week" disabled={week >= maxWeek} onClick={() => navigate(`/week/${season}/${week + 1}`)}>
          ›
        </button>
      </div>
      <div className="week-tools">
        <SchedulerPill status={scheduler} />
        {view?.has_league_file && (
          <>
            <span className="fetched" title={fetchedAt ? fmtDateTime(fetchedAt) : undefined}>
              Lines {view.market?.kind === "live" ? "live" : "fetched"} {fmtAgo(fetchedAt)}
              {fetchedAt && <span className="muted"> · {fmtDateTime(fetchedAt)}</span>}
            </span>
            {started && onRefreshResults && (
              <button
                type="button"
                className="btn"
                onClick={onRefreshResults}
                disabled={busy != null}
                title={
                  results?.offline
                    ? "Offline: the saved nflverse results are used"
                    : `Download nflverse scores now (last downloaded ${fmtDateTime(results?.downloaded_at)})`
                }
              >
                {busy === "results" && <Spinner label="Downloading results" />}
                Refresh results
                {results?.downloaded_at && <span className="muted small"> · {fmtAgo(results.downloaded_at)}</span>}
              </button>
            )}
            {onSendAlert && (
              <button
                type="button"
                className="btn"
                onClick={onSendAlert}
                disabled={busy != null || finished}
                title={finished ? "Every game has kicked off" : "Fetch lines and push the current picks to your phone, even if nothing changed"}
              >
                {busy === "alert" && <Spinner label="Sending" />}
                Send alert
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={onFetch}
              disabled={busy != null || finished}
              title={finished ? "Every game has kicked off, so there are no lines left to fetch" : undefined}
            >
              {busy === "update" ? <Spinner label="Fetching lines" /> : null}
              {busy === "update" ? "Fetching..." : "Fetch lines"}
            </button>
          </>
        )}
      </div>
    </header>
  );
}
