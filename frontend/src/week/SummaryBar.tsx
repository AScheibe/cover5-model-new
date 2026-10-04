import type { Pick, Summary } from "../api/types";
import { hasKickedOff } from "../lib/clock";
import { fmtPoints, signClass } from "../lib/format";

function Stat({ label, value, sub, cls = "", big }: { label: string; value: number; sub: string; cls?: string; big?: boolean }) {
  return (
    <div className={`stat ${big ? "stat-big" : ""} ${cls}`}>
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${signClass(value)}`}>{fmtPoints(value, 1)}</span>
      <span className="stat-sub">{sub}</span>
    </div>
  );
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Open picks split by the clock: a game under way with no score entered (and
 * no nflverse final) still counts its edge as expected points, but it is not
 * "open": it needs live points.
 */
export function openCounts(picks: Pick[], now?: string): { underway: number; notStarted: number } {
  const open = picks.filter((p) => p.status === "open");
  const underway = open.filter((p) => hasKickedOff(p.kickoff_utc, now)).length;
  return { underway, notStarted: open.length - underway };
}

export function SummaryBar({ summary, picks = [], now }: { summary: Summary; picks?: Pick[]; now?: string }) {
  const { underway, notStarted } = openCounts(picks, now);
  const live =
    underway > 0
      ? `${plural(summary.n_live, "score")} entered · ${underway} under way, enter live points`
      : `${summary.n_live} in progress`;
  const expected =
    underway > 0
      ? `${notStarted} not started, ${underway} under way (edge until scored)`
      : `${summary.n_open} not started, from line movement`;
  return (
    <section className="summary-bar panel" aria-label="Week total" title={summary.line}>
      <Stat label="Final" value={summary.final} sub={plural(summary.n_final, "pick")} />
      <Stat label="Live" value={summary.live} sub={live} />
      <Stat label="Expected" value={summary.expected} sub={expected} cls="stat-expected" />
      <Stat label="Projected" value={summary.projected} sub="week total" big />
    </section>
  );
}
