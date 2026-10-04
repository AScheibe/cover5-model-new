import type { Summary } from "../api/types";
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

export function SummaryBar({ summary }: { summary: Summary }) {
  return (
    <section className="summary-bar panel" aria-label="Week total" title={summary.line}>
      <Stat label="Final" value={summary.final} sub={`${summary.n_final} pick${summary.n_final === 1 ? "" : "s"}`} />
      <Stat label="Live" value={summary.live} sub={`${summary.n_live} in progress`} />
      <Stat label="Expected" value={summary.expected} sub={`${summary.n_open} open, from line movement`} cls="stat-expected" />
      <Stat label="Projected" value={summary.projected} sub="week total" big />
    </section>
  );
}
