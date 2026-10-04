import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { HistoryResponse, WeekRecord, WeekRecordUserFields } from "../api/types";
import { EditableCell } from "../components/EditableCell";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { useMeta } from "../components/MetaContext";
import { Loading, Spinner } from "../components/Spinner";
import { teamStyle } from "../components/Team";
import { errorText, useToasts } from "../components/Toasts";
import { fmtDateTime, fmtPoints, signClass } from "../lib/format";
import { SERIES } from "../week/MovementChart";

type Field = keyof WeekRecordUserFields;

const INK = "#a9b6d3";

function PickChips({ rec }: { rec: WeekRecord }) {
  return (
    <div className="chips">
      {rec.picks.map((p) => (
        <span key={p.team} className={`chip chip-${p.status}`} style={teamStyle(p.team)} title={`${p.label} · ${p.status}`}>
          <span className="chip-team">{p.team}</span>
          <span className={`chip-pts ${signClass(p.points)}`}>{p.status === "open" ? `~${fmtPoints(p.points, 1)}` : fmtPoints(p.points)}</span>
        </span>
      ))}
      {Array.from({ length: Math.max(0, rec.n_picks - rec.picks.length) }, (_, i) => (
        <span key={i} className="chip chip-empty">
          —
        </span>
      ))}
    </div>
  );
}

export function weekTotal(r: WeekRecord): number {
  return r.final_points + r.live_points;
}

export function HistoryPage() {
  const { meta } = useMeta();
  const toasts = useToasts();
  const [params, setParams] = useSearchParams();
  const seasonParam = params.get("season");
  const season = seasonParam ? Number(seasonParam) : meta?.current.season ?? null;
  const [data, setData] = useState<HistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.history(season ?? undefined);
      setData(d);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [season]);

  useEffect(() => {
    if (season == null && !meta) return; // wait for the current season
    void load();
  }, [load, season, meta]);

  const weeks = useMemo(() => {
    const all = data?.weeks ?? [];
    return [...all].filter((w) => season == null || w.season === season).sort((a, b) => a.week - b.week);
  }, [data, season]);

  const chart = useMemo(() => {
    let pts = 0;
    let edge = 0;
    return weeks.map((w) => {
      pts += weekTotal(w);
      edge += w.movement_edge;
      return { week: w.week, points: Math.round(pts * 10) / 10, edge: Math.round(edge * 10) / 10 };
    });
  }, [weeks]);

  const saveField = async (rec: WeekRecord, field: Field, value: number | string | null): Promise<boolean> => {
    try {
      const updated = await api.updateRecord(rec.season, rec.week, { [field]: value } as Partial<WeekRecordUserFields>);
      setData((d) =>
        d ? { ...d, weeks: d.weeks.map((w) => (w.season === updated.season && w.week === updated.week ? updated : w)) } : d,
      );
      return true;
    } catch (e) {
      toasts.error(e);
      return false;
    }
  };

  const refresh = async () => {
    setBusy(true);
    try {
      const r = await api.refreshHistory(season ?? undefined);
      toasts.push({ kind: "success", text: `Refreshed ${r.updated} week record${r.updated === 1 ? "" : "s"}.` });
      await load();
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  };

  const seasons = Array.from(new Set([...(data?.seasons ?? []), ...(meta?.seasons ?? []), ...(season != null ? [season] : [])])).sort((a, b) => b - a);
  const t = data?.totals;

  const cell = (rec: WeekRecord, field: Field, kind: "int" | "number" | "text", label: string, format?: (v: number | string) => string) => (
    <EditableCell value={rec[field]} kind={kind} label={`${label}, week ${rec.week}`} format={format} onSave={(v) => saveField(rec, field, v)} />
  );

  return (
    <div className="page">
      <header className="page-head">
        <h1>History</h1>
        <div className="row gap">
          <label className="sr-only" htmlFor="hist-season">
            Season
          </label>
          <select id="hist-season" className="select" value={season ?? ""} onChange={(e) => setParams({ season: e.target.value })}>
            {seasons.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button type="button" className="btn" onClick={() => void refresh()} disabled={busy}>
            {busy && <Spinner label="Refreshing" />} Refresh records
          </button>
          <a className="btn" href={api.exportUrl} download>
            Export JSON
          </a>
        </div>
      </header>

      {error && <p className="panel field-error">{error}</p>}
      {!data && !error && <Loading what="history" />}

      {data && (
        <>
          {t && (
            <section className="summary-bar panel" aria-label="Season totals">
              <div className="stat">
                <span className="stat-label">Weeks</span>
                <span className="stat-value">{t.weeks}</span>
                <span className="stat-sub">{t.complete_weeks} complete</span>
              </div>
              <div className="stat">
                <span className="stat-label">Final points</span>
                <span className={`stat-value ${signClass(t.final_points)}`}>{fmtPoints(t.final_points)}</span>
                <span className="stat-sub">graded picks</span>
              </div>
              <div className="stat stat-expected">
                <span className="stat-label">Movement edge</span>
                <span className={`stat-value ${signClass(t.movement_edge)}`}>{fmtPoints(t.movement_edge, 1)}</span>
                <span className="stat-sub">expected from line moves</span>
              </div>
              <div className="stat stat-big">
                <span className="stat-label">Avg week</span>
                <span className={`stat-value ${signClass(t.avg_week)}`}>{t.avg_week == null ? "—" : fmtPoints(t.avg_week, 1)}</span>
                <span className="stat-sub">complete weeks</span>
              </div>
            </section>
          )}

          {weeks.length === 0 ? (
            <div className="panel empty-state">
              <h2>No records for {season}</h2>
              <p>Records are saved each time a week is updated. "Refresh records" rebuilds them from the saved weeks.</p>
            </div>
          ) : (
            <section className="panel" aria-label="Week records">
              <div className="table-wrap">
                <table className="history-table">
                  <thead>
                    <tr>
                      <th scope="col">Week</th>
                      <th scope="col">Picks</th>
                      <th scope="col">Total</th>
                      <th scope="col">Edge</th>
                      <th scope="col">Week rank</th>
                      <th scope="col">Overall pts</th>
                      <th scope="col">Overall rank</th>
                      <th scope="col">App pts</th>
                      <th scope="col">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {weeks.map((r) => {
                      const total = weekTotal(r);
                      return (
                        <tr key={`${r.season}-${r.week}`}>
                          <td className="nowrap">
                            <Link to={`/week/${r.season}/${r.week}`} className="week-link">
                              Wk {r.week}
                            </Link>
                            {!r.complete && <span className="badge badge-open tiny">{r.n_live > 0 ? "live" : "open"}</span>}
                          </td>
                          <td>
                            <PickChips rec={r} />
                          </td>
                          <td className={`num ${signClass(total)}`} title={`Updated ${fmtDateTime(r.updated_at)}`}>
                            <strong>{fmtPoints(total)}</strong>
                            {!r.complete && <span className="muted small"> proj {fmtPoints(r.projected, 1)}</span>}
                          </td>
                          <td className="num">{fmtPoints(r.movement_edge, 1)}</td>
                          <td className="nowrap">
                            {cell(r, "week_rank", "int", "Week rank")}
                            <span className="muted"> of </span>
                            {cell(r, "entrants", "int", "Entrants")}
                          </td>
                          <td>{cell(r, "overall_points", "number", "Overall points", (v) => String(v))}</td>
                          <td>{cell(r, "overall_rank", "int", "Overall rank")}</td>
                          <td>
                            {cell(r, "app_points", "number", "App points", (v) => fmtPoints(Number(v)))}
                            {r.app_points != null && r.complete && Math.abs(r.app_points - total) > 0.01 && (
                              <span className="badge badge-bad tiny" title="The app's score differs from the model's grading">
                                differs
                              </span>
                            )}
                          </td>
                          <td className="notes">{cell(r, "notes", "text", "Notes")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {chart.length > 0 && (
            <ErrorBoundary label="Cumulative points">
            <section className="panel chart-panel" aria-label="Cumulative points">
              <div className="panel-head">
                <h2>Cumulative points vs movement edge</h2>
              </div>
              <p className="chart-caption">Graded points (final + live) against the edge the line moves promised, summed week by week.</p>
              <div className="chart-box">
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={chart} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
                    <CartesianGrid stroke="#22314f" vertical={false} />
                    <XAxis dataKey="week" stroke={INK} tick={{ fill: INK, fontSize: 11 }} tickFormatter={(v: number) => `Wk ${v}`} />
                    <YAxis stroke={INK} tick={{ fill: INK, fontSize: 11 }} width={44} label={{ value: "points", angle: -90, position: "insideLeft", fill: INK, fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ background: "#0d1628", border: "1px solid #2a3b5e", borderRadius: 8 }}
                      labelFormatter={(v) => `Week ${v}`}
                      formatter={(v: number, name: string) => [fmtPoints(v, 1), name]}
                    />
                    <Legend wrapperStyle={{ fontSize: 12, color: INK }} />
                    <Line type="linear" dataKey="points" name="Points (graded)" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
                    <Line type="linear" dataKey="edge" name="Movement edge" stroke={SERIES[1]} strokeWidth={2} strokeDasharray="6 4" dot={{ r: 4 }} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>
            </ErrorBoundary>
          )}
        </>
      )}
    </div>
  );
}
