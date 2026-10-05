import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clone, mockServer, samples } from "../test/mockApi";
import { renderApp } from "../test/render";

async function openWeek(server = mockServer()) {
  const r = renderApp("/week/2026/4");
  await screen.findByRole("article", { name: "Pick ARI +7 @ NYG" });
  return { ...r, server };
}

const tile = (label: string) => screen.getByRole("article", { name: `Pick ${label}` });

describe("picks strip", () => {
  it("renders labels, points and statuses from week_view.json", async () => {
    await openWeek();
    const strip = screen.getByRole("region", { name: "Your picks" });
    expect(within(strip).getAllByRole("article")).toHaveLength(5);

    const cle = tile("CLE -3 vs PIT");
    expect(within(cle).getByText("LIVE")).toBeInTheDocument();
    expect(within(cle).getByText("LOCKED by you")).toBeInTheDocument();
    expect(within(cle).getByTestId("points-CLE")).toHaveTextContent("+8");
    expect(within(cle).getByText("live pts")).toBeInTheDocument();
    expect(within(cle).getByRole("button", { name: "Unlock CLE" })).toBeInTheDocument();

    const ari = tile("ARI +7 @ NYG");
    expect(within(ari).getByText("open")).toBeInTheDocument();
    expect(within(ari).getByText("expected")).toBeInTheDocument();
    expect(within(ari).getByTestId("points-ARI")).toHaveTextContent("+4.0");
    expect(within(ari).getByLabelText("league line overridden")).toBeInTheDocument();
    expect(within(ari).getByText(/Market \+3/)).toBeInTheDocument();

    for (const label of ["WAS -3 vs IND", "BAL -3 vs TEN", "BUF -3 vs NE"]) {
      expect(within(tile(label)).getByText("open")).toBeInTheDocument();
    }
  });

  it("shows empty dashed slots when fewer picks than n_picks", async () => {
    const week = clone(samples.weekView);
    week.picks = week.picks!.slice(0, 3);
    mockServer({ week });
    renderApp("/week/2026/4");
    await screen.findByRole("article", { name: "Pick ARI +7 @ NYG" });
    expect(screen.getAllByLabelText("Empty pick slot")).toHaveLength(2);
  });

  it("renders the week total and the do-this banner", async () => {
    await openWeek();
    const total = screen.getByRole("region", { name: "Week total" });
    expect(within(total).getByText("+16.5")).toBeInTheDocument();
    expect(within(total).getByText("+8.0")).toBeInTheDocument();
    const banner = screen.getByRole("region", { name: "Do this" });
    expect(within(banner).getByText("Drop")).toBeInTheDocument();
    expect(within(banner).getByText("CHI -3 vs NYJ")).toBeInTheDocument();
    expect(within(banner).getByText(/ARI \+7 @ NYG \(market \+3, edge \+4.0\)/)).toBeInTheDocument();
    expect(within(banner).getByText(/after your line override/)).toBeInTheDocument();
  });

  it("locks and unlocks from the tile", async () => {
    const { user, server } = await openWeek();
    await user.click(within(tile("WAS -3 vs IND")).getByRole("button", { name: "Lock WAS" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/week/2026/4/locks", body: { teams: ["WAS"] } });

    await user.click(within(tile("CLE -3 vs PIT")).getByRole("button", { name: "Unlock CLE" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(2));
    expect(server.mutations()[1]).toMatchObject({ method: "DELETE", path: "/api/week/2026/4/locks/CLE" });
  });

  it("enters points with the live flag for a pick that has kicked off", async () => {
    const server = mockServer();
    server.on("PUT /api/week/2026/4/points", () => samples.outcomeSetPoints);
    const { user } = await openWeek(server);
    await user.click(within(tile("CLE -3 vs PIT")).getByRole("button", { name: "Enter points for CLE" }));
    const dlg = await screen.findByRole("dialog", { name: /Points: CLE -3 vs PIT/ });
    const input = within(dlg).getByLabelText("Points");
    await user.clear(input);
    await user.type(input, "-3.5");
    // Kicked off three hours ago: probably still being played, so live starts ticked.
    expect(within(dlg).getByLabelText(/live/)).toBeChecked();
    await user.click(within(dlg).getByRole("button", { name: "Save points" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "PUT", path: "/api/week/2026/4/points", body: { team: "CLE", points: -3.5, live: true } });
    expect(await screen.findByText("CLE scored +8 (live); locked CLE as your pick in that game.")).toBeInTheDocument();
  });

  it("disables Points until the pick's game has kicked off", async () => {
    await openWeek();
    const btn = within(tile("WAS -3 vs IND")).getByRole("button", { name: "Enter points for WAS" });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toMatch(/once the game kicks off/);
  });
});

describe("games table pick toggling", () => {
  it("removes a picked team (PUT with the remaining set)", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Pick WAS" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "PUT", path: "/api/week/2026/4/picks", body: { teams: ["ARI", "CLE", "BAL", "BUF"] } });
  });

  it("switches sides in place when the other team of a picked game is clicked", async () => {
    const { user, server } = await openWeek();
    expect(screen.getByRole("button", { name: "Pick WAS" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Pick IND" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    // The team you picked by hand is locked, so the model can't flip it straight back.
    expect(server.mutations()[0]!.body).toEqual({ teams: ["ARI", "CLE", "IND", "BAL", "BUF"], lock: ["IND"] });
  });

  it("asks which pick to replace when the set is full", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Pick CHI" }));
    const dlg = await screen.findByRole("dialog", { name: "Replace which pick with CHI?" });
    expect(server.mutations()).toHaveLength(0);
    await user.click(within(dlg).getByRole("button", { name: /BUF -3 vs NE/ }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]!.body).toEqual({ teams: ["ARI", "CLE", "WAS", "BAL", "CHI"], lock: ["CHI"] });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the replace popover with Escape without sending anything", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Pick CHI" }));
    await screen.findByRole("dialog", { name: "Replace which pick with CHI?" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(server.mutations()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Pick CHI" })).toHaveFocus();
  });

  it("adds a team when there is room", async () => {
    const week = clone(samples.weekView);
    week.picks = week.picks!.filter((p) => p.team !== "BUF");
    week.games = week.games!.map((g) => (g.picked_team === "BUF" ? { ...g, picked_team: null, picked_side: null } : g));
    const server = mockServer({ week });
    const { user } = renderApp("/week/2026/4");
    await screen.findByRole("article", { name: "Pick ARI +7 @ NYG" });
    await user.click(screen.getByRole("button", { name: "Pick JAX" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]!.body).toEqual({ teams: ["ARI", "CLE", "WAS", "BAL", "JAX"], lock: ["JAX"] });
  });

  it("confirms before changing a kicked-off game and unlocks your lock first", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Pick PIT" }));
    // The dialog names exactly what will happen.
    const dlg = await screen.findByRole("dialog", { name: "Switch from CLE to PIT?" });
    expect(dlg).toHaveTextContent("PIT @ CLE has kicked off.");
    expect(dlg).toHaveTextContent("This replaces CLE with PIT +3 @ CLE in that game.");
    expect(server.mutations()).toHaveLength(0);
    await user.click(within(dlg).getByRole("button", { name: "Switch to PIT" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(2));
    expect(server.mutations()[0]).toMatchObject({ method: "DELETE", path: "/api/week/2026/4/locks/CLE" });
    expect(server.mutations()[1]).toMatchObject({ method: "PUT", path: "/api/week/2026/4/picks", body: { teams: ["ARI", "PIT", "WAS", "BAL", "BUF"] } });
  });

  it("cancelling the kickoff confirmation sends nothing", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Pick CLE" }));
    const dlg = await screen.findByRole("dialog", { name: "Remove CLE from your picks?" });
    expect(dlg).toHaveTextContent("This removes CLE -3 vs PIT from your picks and its points from the week.");
    expect(within(dlg).getByRole("button", { name: "Remove CLE" })).toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: "Cancel" }));
    expect(server.mutations()).toHaveLength(0);
  });
});

describe("line and score overrides", () => {
  it("sends PUT lines with the spread from the chosen team's view", async () => {
    const server = mockServer();
    server.on("PUT /api/week/2026/4/lines", () => samples.outcomeSetLine);
    const { user } = await openWeek(server);
    await user.click(screen.getByRole("button", { name: "Edit league line for IND @ WAS" }));
    const dlg = await screen.findByRole("dialog", { name: "League line: IND @ WAS" });
    const input = within(dlg).getByLabelText("WAS spread");
    expect(input).toHaveValue("-3");
    await user.clear(input);
    await user.type(input, "-3.5");
    expect(dlg).toHaveTextContent("Will read: WAS -3.5 vs IND");
    // Switch to IND's view: the typed spread flips sign.
    await user.click(within(dlg).getByLabelText("IND"));
    expect(within(dlg).getByLabelText("IND spread")).toHaveValue("+3.5");
    expect(dlg).toHaveTextContent("Will read: IND +3.5 @ WAS");
    await user.click(within(dlg).getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "PUT", path: "/api/week/2026/4/lines", body: { team: "IND", spread: 3.5 } });
    expect(await screen.findByText(/League line set: NYG -7 vs ARI/)).toBeInTheDocument();
    // The outcome's alert changed picks: it shows as an alert toast too.
    expect(within(screen.getByRole("region", { name: "Notifications" })).getByText("Cover 5 wk4: picks changed")).toBeInTheDocument();
  });

  it("accepts PK and validates junk", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Edit league line for NYJ @ CHI" }));
    const dlg = await screen.findByRole("dialog", { name: "League line: NYJ @ CHI" });
    const input = within(dlg).getByLabelText("CHI spread");
    await user.clear(input);
    await user.type(input, "abc");
    await user.click(within(dlg).getByRole("button", { name: "Save line" }));
    expect(within(dlg).getByText(/Enter a spread like/)).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, "pk");
    await user.click(within(dlg).getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]!.body).toEqual({ team: "CHI", spread: "PK" });
  });

  it("shows the sheet value and reverts an overridden line", async () => {
    const { user, server } = await openWeek();
    const cell = screen.getByRole("button", { name: "Edit league line for ARI @ NYG" });
    expect(cell).toHaveTextContent("ARI +7");
    expect(cell.parentElement).toHaveTextContent("sheet +3");
    await user.click(cell);
    const dlg = await screen.findByRole("dialog", { name: "League line: ARI @ NYG" });
    expect(dlg).toHaveTextContent("Sheet: ARI +3 @ NYG");
    await user.click(within(dlg).getByRole("button", { name: /Revert to sheet/ }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "DELETE", path: "/api/week/2026/4/overrides/ARI", query: "?kind=line" });
  });

  it("score dialog sends PUT scores from the picked team's view with the live flag", async () => {
    const { user, server } = await openWeek();
    await user.click(screen.getByRole("button", { name: "Enter score for PIT @ CLE" }));
    const dlg = await screen.findByRole("dialog", { name: "Score: PIT @ CLE" });
    await user.type(within(dlg).getByLabelText(/PIT \(away\)/), "17");
    await user.type(within(dlg).getByLabelText(/CLE \(home\)/), "24");
    const live = within(dlg).getByLabelText(/live score/) as HTMLInputElement;
    if (!live.checked) await user.click(live);
    await user.click(within(dlg).getByRole("button", { name: "Save score" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({
      method: "PUT",
      path: "/api/week/2026/4/scores",
      body: { team: "CLE", team_points: 24, opponent_points: 17, live: true },
    });

    // Final score for an unpicked game goes in from the home team's view. The
    // schedule says DAL @ HOU hasn't kicked off: saving needs "enter it anyway".
    await user.click(screen.getByRole("button", { name: "Enter score for DAL @ HOU" }));
    const dlg2 = await screen.findByRole("dialog", { name: "Score: DAL @ HOU" });
    await user.type(within(dlg2).getByLabelText(/DAL \(away\)/), "20");
    await user.type(within(dlg2).getByLabelText(/HOU \(home\)/), "13");
    const live2 = within(dlg2).getByLabelText(/live score/) as HTMLInputElement;
    if (live2.checked) await user.click(live2);
    expect(within(dlg2).getByRole("button", { name: "Save score" })).toBeDisabled();
    await user.click(within(dlg2).getByLabelText(/enter it anyway/));
    await user.click(within(dlg2).getByRole("button", { name: "Save score" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(2));
    expect(server.mutations()[1]!.body).toEqual({ team: "HOU", team_points: 13, opponent_points: 20, live: false, force: true });
  });
});

describe("week states and errors", () => {
  it("uninitialised week shows set-up and sends POST init", async () => {
    const server = mockServer({ week: clone(samples.weekViewUninit) });
    server.on("POST /api/week/2026/9/init", () => ({ week: { ...samples.weekView, week: 9 }, alert: null, messages: ["Seeded 8 games for 2026 week 9."] }));
    const { user } = renderApp("/week/2026/9");
    const setup = await screen.findByRole("button", { name: "Set up this week" });
    expect(screen.getByText(/2026 week 9 isn't set up yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fetch lines" })).not.toBeInTheDocument();
    await user.click(screen.getByLabelText(/nflverse spreads/));
    await user.click(setup);
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "POST", path: "/api/week/2026/9/init", body: { use_market: false } });
    expect(await screen.findByText("Seeded 8 games for 2026 week 9.")).toBeInTheDocument();
    // The view is replaced by outcome.week: the picks strip appears without a refetch.
    expect(await screen.findByRole("region", { name: "Your picks" })).toBeInTheDocument();
    expect(server.calls.filter((c) => c.method === "GET" && c.path === "/api/week/2026/9")).toHaveLength(1);
  });

  it("shows the server's error detail as a toast", async () => {
    const server = mockServer();
    server.on("PUT /api/week/2026/4/picks", () => ({ status: 400, body: { detail: "two picks in IND@WAS; pick one side per game" } }));
    const { user } = await openWeek(server);
    await user.click(screen.getByRole("button", { name: "Pick WAS" }));
    const toast = await screen.findByRole("alert");
    expect(toast).toHaveTextContent("two picks in IND@WAS; pick one side per game");
    // Buttons are usable again after the failed request.
    expect(screen.getByRole("button", { name: "Pick WAS" })).toBeEnabled();
  });

  it("disables buttons while a request is in flight", async () => {
    const server = mockServer();
    let release: () => void = () => {};
    server.on("POST /api/week/2026/4/update", () => new Promise((r) => (release = () => r({ week: samples.weekView, alert: null, messages: ["Fetched 16 market lines."] }))));
    const { user } = await openWeek(server);
    await user.click(screen.getByRole("button", { name: "Fetch lines" }));
    expect(await screen.findByRole("button", { name: /Fetching/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pick CHI" })).toBeDisabled();
    release();
    expect(await screen.findByText("Fetched 16 market lines.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pick CHI" })).toBeEnabled();
  });

  it("redirects / to the current week from meta", async () => {
    mockServer();
    renderApp("/");
    expect(await screen.findByRole("article", { name: "Pick ARI +7 @ NYG" })).toBeInTheDocument();
  });

  it("lists runs in the alert log", async () => {
    const { user } = await openWeek();
    const log = screen.getByRole("region", { name: "Alert log" });
    await within(log).findByText(/4 runs|\d+ runs/);
    await user.click(within(log).getByRole("heading", { name: "Alert log" }));
    expect(within(log).getAllByText("after your line override").length).toBeGreaterThan(0);
  });
});
