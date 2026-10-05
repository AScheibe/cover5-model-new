import type { CSSProperties } from "react";
import { teamInfo, teamInk } from "../lib/teams";

export function teamStyle(code: string | null | undefined): CSSProperties {
  const t = teamInfo(code);
  return {
    "--team": t.primary,
    "--team-2": t.secondary,
    "--team-ink": teamInk(code),
  } as CSSProperties;
}

/** Bold team block in team colours, like the league app's tiles. */
export function TeamBadge({ team, size = "md" }: { team: string; size?: "sm" | "md" | "lg" }) {
  const info = teamInfo(team);
  return (
    <span className={`team-badge team-badge-${size}`} style={teamStyle(team)} title={info.name || team}>
      {team}
    </span>
  );
}
