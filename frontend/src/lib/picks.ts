import type { Game } from "../api/types";

/** What clicking a team in the games table should do to the pick set. */
export type ToggleResult =
  | { kind: "set"; teams: string[]; action: "add" | "remove" | "switch"; from?: string }
  | { kind: "choose"; team: string; current: string[] };

/**
 * Toggle `team` (one side of `game`) in the pick set `current`.
 * - picked already: remove it
 * - other side of the same game picked: switch sides in place
 * - room left: add it
 * - set full: the caller asks which pick to replace (see replacePick)
 */
export function togglePick(current: string[], team: string, game: Pick<Game, "away" | "home">, nPicks: number): ToggleResult {
  if (current.includes(team)) {
    return { kind: "set", teams: current.filter((t) => t !== team), action: "remove" };
  }
  const other = team === game.home ? game.away : game.home;
  if (current.includes(other)) {
    return { kind: "set", teams: current.map((t) => (t === other ? team : t)), action: "switch", from: other };
  }
  if (current.length < nPicks) {
    return { kind: "set", teams: [...current, team], action: "add" };
  }
  return { kind: "choose", team, current };
}

/** Replace `out` with `incoming`, keeping the order of the other picks. */
export function replacePick(current: string[], out: string, incoming: string): string[] {
  return current.map((t) => (t === out ? incoming : t));
}
