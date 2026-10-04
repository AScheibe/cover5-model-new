import type { SchedulerStatus } from "../api/types";
import { fmtAgo, fmtKickoff } from "../lib/format";

export function SchedulerPill({ status }: { status: SchedulerStatus | null | undefined }) {
  if (!status) return <span className="pill pill-muted">Scheduler: unknown</span>;
  if (status.last_error) {
    return (
      <span className="pill pill-bad" title={`Last error: ${status.last_error}`}>
        Scheduler error
      </span>
    );
  }
  if (!status.enabled) {
    return (
      <span className="pill pill-muted" title="Turn it on in Settings">
        Scheduler off
      </span>
    );
  }
  const bits = [
    status.last_update_at ? `last update ${fmtAgo(status.last_update_at)}` : "no update yet",
    status.next_due ? `next ${fmtKickoff(status.next_due)}` : null,
    status.last_result,
  ].filter(Boolean);
  return (
    <span className={`pill ${status.running ? "pill-good" : "pill-warn"}`} title={bits.join(" · ")}>
      <span className="dot" aria-hidden="true" />
      {status.running ? "Scheduler on" : "Scheduler stopped"}
      {status.next_due && <span className="pill-sub"> · next {fmtKickoff(status.next_due)}</span>}
    </span>
  );
}
