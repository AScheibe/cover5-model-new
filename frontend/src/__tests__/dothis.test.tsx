import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import weekView from "../../../docs/samples/week_view.json";
import type { Game, LastChange, Pick, Todo, WeekView } from "../api/types";
import { bestTeam, viewTeam } from "../week/GamesTable";
import { DoThis, openSteps } from "../week/DoThis";

const view = weekView as unknown as WeekView;
const picks = view.picks as Pick[];
const games = view.games as Game[];
const teams = picks.map((p) => p.team);

function todo(over: Partial<Todo>): Todo {
  return { changed: true, added: [], dropped: [], flipped: [], since: "2026-10-04T20:30:00+00:00", confirmed: [], ...over };
}

function change(over: Partial<LastChange>): LastChange {
  return { changed: true, added: [], dropped: [], flipped: [], at: "2026-10-04T17:00:00+00:00", reason: "market update", sent: false, body: "DO THIS", ...over };
}

describe("Do this banner", () => {
  it("lists every change in the to-do list, not just the latest run's", () => {
    // The last run only swapped one team; the to-do nets everything since you confirmed.
    const gone = games.filter((g) => !g.picked_team && g.league_home_spread != null && !g.locked).slice(0, 2);
    const t = todo({ added: ["ARI", "WAS"], dropped: gone.map((g) => g.home) });
    render(<DoThis todo={t} lastChange={change({ added: ["ARI"], dropped: [gone[0]!.home] })} picks={picks} games={games} now={view.now} />);
    const banner = screen.getByRole("region", { name: "Do this" });
    expect(banner.querySelectorAll(".step-add")).toHaveLength(2);
    expect(banner.querySelectorAll(".step-drop")).toHaveLength(2);
    expect(within(banner).getByText(/Every change since you confirmed your picks/)).toBeInTheDocument();
    // One set of instructions only: no separate "pending change" block.
    expect(within(banner).queryByText(/Pending change/)).toBeNull();
  });

  it("before you confirm anything it is the full set to enter", () => {
    render(<DoThis todo={todo({ added: ["ARI", "WAS"], confirmed: null, since: null })} lastChange={null} picks={picks} games={games} now={view.now} />);
    expect(screen.getByText(/haven't confirmed your picks in the app this week/)).toBeInTheDocument();
  });

  it("labels drops as the app shows them and hides an empty to-do", () => {
    const gone = games.find((g) => !g.picked_team && g.league_home_spread != null && !g.locked)!;
    const { rerender } = render(<DoThis todo={todo({ changed: false })} lastChange={change({ added: ["ARI"] })} picks={picks} games={games} now={view.now} />);
    expect(screen.queryByRole("region", { name: "Do this" })).toBeNull();
    rerender(<DoThis todo={todo({ dropped: [gone.away] })} lastChange={null} picks={picks} games={games} now={view.now} />);
    const banner = screen.getByRole("region", { name: "Do this" });
    const spread = -gone.league_home_spread!;
    const shown = spread === 0 ? "PK" : spread > 0 ? `+${spread}` : `${spread}`;
    expect(within(banner).getByText(`${gone.away} ${shown} @ ${gone.home}`)).toBeInTheDocument();
  });

  it("marks the changes done", async () => {
    const onConfirm = vi.fn();
    render(<DoThis todo={todo({ added: ["ARI"] })} lastChange={change({ sent: true })} picks={picks} games={games} now={view.now} onConfirm={onConfirm} />);
    expect(screen.getByText("alert sent")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "I've made these changes" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("Do this after kickoff", () => {
  it("hides steps for games that kick off while the page is open", () => {
    const team = teams[0]!;
    const kick = picks[0]!.kickoff_utc;
    const before = new Date(new Date(kick).getTime() - 60_000).toISOString();
    const after = new Date(new Date(kick).getTime() + 60_000).toISOString();
    expect(openSteps(todo({ added: [team] }), games, before).added).toEqual([team]);
    expect(openSteps(todo({ added: [team] }), games, after).changed).toBe(false);
    render(<DoThis todo={todo({ added: [team] })} lastChange={null} picks={picks} games={games} now={after} />);
    expect(screen.queryByRole("region", { name: "Do this" })).toBeNull();
  });
});

describe("games table sides", () => {
  it("marks no best side when the market hasn't moved", () => {
    const g: Game = { ...games[0]!, edge: 0, best_team: games[0]!.home, picked_team: null };
    expect(bestTeam(g)).toBeNull();
    expect(viewTeam(g)).toBe(g.home);
    const moved: Game = { ...g, edge: 1.5, best_team: g.away };
    expect(bestTeam(moved)).toBe(g.away);
    expect(viewTeam(moved)).toBe(g.away);
    expect(viewTeam({ ...moved, picked_team: g.home })).toBe(g.home);
  });
});
