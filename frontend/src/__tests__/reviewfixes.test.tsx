/** Frontend regression tests for the confirmed findings of the web app review. */
import { act, cloneElement, type ReactElement } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Pick, SnapshotGame, Summary } from "../api/types";
import { restoreFocus } from "../components/Dialog";
import { Providers } from "../App";
import { BacktestPage } from "../pages/BacktestPage";
import { clone, meta, mockServer, samples } from "../test/mockApi";
import { renderApp } from "../test/render";
import { PointsDialog } from "../week/dialogs";
import { MovementChart } from "../week/MovementChart";
import { SummaryBar } from "../week/SummaryBar";

// Recharts' ResponsiveContainer measures 0x0 in jsdom; give the chart a size.
vi.mock("recharts", async (orig) => {
  const mod = await orig<typeof import("recharts")>();
  return {
    ...mod,
    ResponsiveContainer: ({ children }: { children: ReactElement }) => cloneElement(children, { width: 640, height: 260 } as object),
  };
});

const view = samples.weekView;
const picks = view.picks as Pick[];
const at = (base: string, ms: number) => new Date(new Date(base).getTime() + ms).toISOString();

async function openWeek(opts: { pollMs?: number } = {}) {
  const server = mockServer();
  const r = renderApp("/week/2026/4", opts);
  await screen.findByRole("article", { name: "Pick ARI +7 @ NYG" });
  return { ...r, server };
}

describe("summary wording (a pick under way with no score)", () => {
  it("counts a kicked-off open pick as under way, not open, and asks for live points", () => {
    const now = view.now;
    const p = (team: string, kick: string): Pick => ({ ...picks[0]!, team, status: "open", points: 1, kickoff_utc: kick });
    const summary: Summary = { final: 0, live: 0, expected: 5, projected: 5, n_final: 0, n_live: 0, n_open: 2, line: "" };
    render(<SummaryBar summary={summary} picks={[p("LAC", at(now, -3600_000)), p("ATL", at(now, 86400_000))]} now={now} />);
    const bar = screen.getByRole("region", { name: "Week total" });
    expect(bar).toHaveTextContent("0 scores entered · 1 under way, enter live points");
    expect(bar).toHaveTextContent("1 not started, 1 under way (edge until scored)");
    expect(bar).not.toHaveTextContent("0 in progress");
  });
});

describe("points dialog live default", () => {
  const live = (kickAgoMs: number): Pick => ({ ...picks[1]!, status: "live", kickoff_utc: at(view.now, -kickAgoMs) });
  const renderDlg = (pick: Pick) =>
    render(<PointsDialog pick={pick} now={view.now} hasOverride busy={false} onSave={() => {}} onRevert={() => {}} onClose={() => {}} />);

  it("starts unticked when you come back hours after kickoff, even after a live entry", () => {
    renderDlg(live(6 * 3600_000));
    expect(screen.getByLabelText("Game still in progress (live)")).not.toBeChecked();
  });

  it("starts ticked while the game is probably still being played", () => {
    renderDlg(live(3600_000));
    expect(screen.getByLabelText("Game still in progress (live)")).toBeChecked();
  });
});

describe("CLI abilities in the week page", () => {
  it("sends the current picks as an alert on demand", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Send alert" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/week/2026/4/update", body: { force_alert: true } });
  });

  it("downloads nflverse results on demand", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: /Refresh results/ }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/week/2026/4/results" });
  });

  it("re-seeds from nflverse only, and says played games keep their frozen line", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Re-seed league lines..." }));
    const dlg = await screen.findByRole("dialog", { name: "Re-seed league lines?" });
    expect(dlg).toHaveTextContent("Games that have kicked off keep their frozen line");
    expect(dlg).toHaveTextContent("data/league_lines/backup/");
    await user.click(within(dlg).getByRole("radio", { name: /nflverse spreads/ }));
    await user.click(within(dlg).getByRole("button", { name: "Re-seed from nflverse" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/week/2026/4/init", body: { overwrite: true, use_market: false } });
  });

  it("locks a team you don't hold", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Lock a team in NYJ @ CHI" }));
    const dlg = await screen.findByRole("dialog", { name: "Lock a team in NYJ @ CHI" });
    await user.click(within(dlg).getByRole("button", { name: /Lock CHI/ }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/week/2026/4/locks", body: { teams: ["CHI"] } });
  });
});

describe("background updates while a dialog is open", () => {
  it("reloads the week once the dialog closes instead of dropping the update", async () => {
    const { user, server } = await openWeek({ pollMs: 100 });
    const weekGets = () => server.calls.filter((c) => c.method === "GET" && c.path === "/api/week/2026/4").length;
    const metaGets = () => server.calls.filter((c) => c.path === "/api/meta").length;
    await user.click(screen.getByRole("button", { name: "Edit league line for IND @ WAS" }));
    await screen.findByRole("dialog", { name: "League line: IND @ WAS" });
    const before = weekGets();
    // The scheduler (or the CLI) updates the week while the dialog is open.
    server.on("GET /api/meta", () => ({ ...meta, scheduler: { ...meta.scheduler, last_update_at: "2026-10-04T21:10:00+00:00" } }));
    const m0 = metaGets();
    await waitFor(() => expect(metaGets()).toBeGreaterThan(m0 + 2));
    expect(weekGets()).toBe(before); // never reloaded under an open dialog
    await user.keyboard("{Escape}");
    await waitFor(() => expect(weekGets()).toBe(before + 1));
  });
});

describe("history: a week you never tracked", () => {
  it("adds a standings-only record", async () => {
    const server = mockServer();
    server.on("PUT /api/history/2026/2", () => ({ ...clone(samples.weekRecord), week: 2, picks: [] }));
    const { user } = renderApp("/history?season=2026");
    const panel = await screen.findByRole("region", { name: "Add a week" });
    await user.selectOptions(within(panel).getByLabelText("Week"), "2");
    await user.click(within(panel).getByRole("button", { name: "Add week" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "PUT", path: "/api/history/2026/2", body: {} });
    expect(await screen.findByText(/Added week 2/)).toBeInTheDocument();
  });
});

describe("backtest from the app", () => {
  it("runs the backtest and loads the results when it finishes", async () => {
    const server = mockServer();
    let ready = false;
    let started = false;
    server.on("GET /api/backtest", () => (ready ? { dataset: "nflverse 2007-2025", strategies: {} } : { status: 404, body: { detail: "none" } }));
    server.on("GET /api/backtest/status", () =>
      ready
        ? { state: "done", started_at: null, finished_at: "2026-10-04T21:00:00+00:00", error: null }
        : { state: started ? "running" : "idle", started_at: null, finished_at: null, error: null });
    server.on("POST /api/backtest/run", () => (started = true) && ({ state: "running", started_at: "2026-10-04T20:59:00+00:00", finished_at: null, error: null, started: true }));
    const user = userEvent.setup();
    render(
      <Providers pollMs={0}>
        <BacktestPage pollMs={50} />
      </Providers>,
    );
    await screen.findByText("No backtest results yet");
    await user.click(screen.getByRole("button", { name: "Run backtest" }));
    expect(await screen.findByRole("button", { name: /Running backtest/ })).toBeDisabled();
    ready = true;
    expect(await screen.findByText("nflverse 2007-2025")).toBeInTheDocument();
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/backtest/run" });
  });
});

describe("settings: clearing the key", () => {
  it("keeps the other edits you haven't saved", async () => {
    const settings = {
      provider: "oddsapi", odds_api_key_set: true, odds_api_key_hint: "…5678", ntfy_topic: "", ntfy_server: "https://ntfy.sh",
      webhook_url: "", swap_margin: 0.5, flip_margin: 0.5,
      scheduler: { enabled: false, interval_minutes: 60, sunday_interval_minutes: 15, auto_init_wednesday: true },
    };
    const server = mockServer();
    server.on("GET /api/settings", () => settings);
    server.on("PUT /api/settings", () => ({ ...settings, odds_api_key_set: false, odds_api_key_hint: null }));
    const { user } = renderApp("/settings");
    const topic = await screen.findByLabelText("ntfy topic");
    await user.type(topic, "my-cover5-topic");
    const swap = screen.getByLabelText("Swap margin (points)");
    await user.clear(swap);
    await user.type(swap, "1.5");
    await user.click(screen.getByRole("button", { name: "Clear key" }));
    expect(await screen.findByText("Odds API key cleared.")).toBeInTheDocument();
    expect(server.mutations()[0]!.body).toEqual({ odds_api_key: "" });
    expect(screen.getByLabelText("ntfy topic")).toHaveValue("my-cover5-topic");
    expect(screen.getByLabelText("Swap margin (points)")).toHaveValue("1.5");
  });
});

describe("dialog focus", () => {
  it("gives focus back to the opener once the request that disabled it is done", async () => {
    const btn = document.createElement("button");
    btn.textContent = "Edit league line";
    document.body.appendChild(btn);
    btn.disabled = true; // every button is disabled while the dialog's request runs
    restoreFocus(btn);
    expect(document.activeElement).toBe(document.body);
    await new Promise((r) => setTimeout(r, 120));
    act(() => {
      btn.disabled = false;
    });
    await waitFor(() => expect(document.activeElement).toBe(btn));
    btn.remove();
  });

  it("returns focus to the line cell after saving a line from the keyboard", async () => {
    const { user, server } = await openWeek();
    const cell = screen.getByRole("button", { name: "Edit league line for IND @ WAS" });
    cell.focus();
    await user.keyboard("{Enter}");
    const dlg = await screen.findByRole("dialog", { name: "League line: IND @ WAS" });
    const input = within(dlg).getByLabelText("WAS spread");
    await user.clear(input);
    await user.type(input, "-3.5{Enter}");
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit league line for IND @ WAS" })).toHaveFocus());
  });
});

describe("game chart with a single snapshot", () => {
  it("draws the frozen league line across the chart", async () => {
    const g = view.games![1]!; // IND @ WAS
    const sg: SnapshotGame = {
      game_id: g.game_id, away: g.away, home: g.home, kickoff_utc: g.kickoff_utc, league_home_spread: -3.5,
      series: [{ t: "2026-10-04T20:00:00+00:00", home_spread: -5, books: 4, source: "espn" } as SnapshotGame["series"][number]],
    };
    const server = mockServer();
    server.on("GET /api/week/2026/4/snapshots", () => ({ games: [sg] }));
    const user = userEvent.setup();
    const { container } = render(
      <Providers pollMs={0}>
        <MovementChart season={2026} week={4} games={view.games!} picks={picks} version={0} />
      </Providers>,
    );
    const select = await screen.findByLabelText("Game");
    await waitFor(() => expect(screen.queryByText(/Loading line history/)).toBeNull());
    await user.selectOptions(select, g.game_id);
    await waitFor(() => expect(container.querySelector(".recharts-reference-line line")).not.toBeNull());
    const line = container.querySelector(".recharts-reference-line line")!;
    expect(line.getAttribute("stroke-dasharray")).toBe("5 4");
    expect(Number(line.getAttribute("x2")) - Number(line.getAttribute("x1"))).toBeGreaterThan(100);
  });
});

describe("notifications for an outcome with several messages", () => {
  it("shows every message of one outcome together, even next to an alert", async () => {
    const { user, server } = await openWeek();
    const msgs = [
      "You entered: ATL, LAC, PIT, PHI.",
      "Locked DET, so the model keeps it. Unlock it to let the model manage that pick.",
      "The model suggests CAR (edge +0.0) for the open slot(s).",
      "The model's picks are now: ATL, LAC, PIT, PHI, CAR.",
      "Removed your lock on HOU (it isn't in the picks you entered).",
    ];
    server.on("POST /api/week/2026/4/update", () => ({
      week: clone(samples.weekView),
      alert: { title: "Cover 5 wk4: picks changed", body: "DO THIS", changed: true, sent: false },
      messages: msgs,
    }));
    await user.click(screen.getByRole("button", { name: "Fetch lines" }));
    const region = screen.getByRole("region", { name: "Notifications" });
    for (const m of msgs) expect(await within(region).findByText(m)).toBeInTheDocument();
    expect(within(region).getByText("Cover 5 wk4: picks changed")).toBeInTheDocument();
  });
});
