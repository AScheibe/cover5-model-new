import { expect, test } from "@playwright/test";
import {
  clearToasts,
  confirmIfKickedOff,
  currentWeek,
  edgeText,
  escapeRe,
  expectToast,
  fmtPoints,
  fmtSpread,
  gameRow,
  getWeek,
  gotoWeek,
  kickedOff,
  pickTile,
  statValue,
  tilePoints,
  waitIdle,
  watchConsole,
} from "./helpers";

// The specs share one server and run in order; each reads the state it needs first.
test.describe.configure({ mode: "serial" });

test("opens the current week from / with its picks, total and games", async ({ page, request }) => {
  const con = watchConsole(page);
  const { season, week, view } = await currentWeek(request);
  await page.goto("/");
  await expect(page).toHaveURL(new RegExp(`/week/${season}/${week}$`));

  const strip = page.getByRole("region", { name: "Your picks" });
  await expect(strip.getByRole("article")).toHaveCount(view.picks!.length);
  for (const p of view.picks!) {
    await expect(pickTile(page, p.team)).toContainText(p.label);
    await expect(page.getByTestId(`points-${p.team}`)).toHaveText(tilePoints(p));
  }
  await expect(statValue(page, "Projected")).toHaveText(fmtPoints(view.summary!.projected, 1));
  await expect(statValue(page, "Final")).toHaveText(fmtPoints(view.summary!.final, 1));
  await expect(page.locator(".games-table tbody tr")).toHaveCount(view.games!.length);
  for (const g of view.games!) await expect(gameRow(page, g.game_id).locator(".c-edge")).toHaveText(edgeText(g));
  await expect(page.getByRole("combobox", { name: "Week" })).toHaveValue(String(week));
  con.check();
});

test("fetches lines from the market and logs a new snapshot", async ({ page, request }) => {
  const { season, week } = await currentWeek(request);
  const before = await (await request.get(`/api/week/${season}/${week}/snapshots`)).json();
  const n0 = before.games.reduce((a: number, g: { series: unknown[] }) => a + g.series.length, 0);
  await gotoWeek(page, season, week);
  await page.getByRole("button", { name: "Fetch lines" }).click();
  await expectToast(page, /Fetched \d+ market lines\./);
  await expect(page.locator(".fetched")).toContainText(/Lines live just now|Lines fetched just now/);
  const after = await (await request.get(`/api/week/${season}/${week}/snapshots`)).json();
  const n1 = after.games.reduce((a: number, g: { series: unknown[] }) => a + g.series.length, 0);
  expect(n1).toBeGreaterThan(n0);
});

test("the Do this banner lists every change since you confirmed, once, and clears when marked done", async ({ page, request }) => {
  const { season, week, view } = await currentWeek(request);
  expect(view.last_change, "the demo week has a run that changed picks").toBeTruthy();
  const todo = view.todo!;
  // Only steps you can still act on: the game hasn't kicked off.
  const open = (t: string) => !kickedOff(view.games!.find((g) => g.home === t || g.away === t)!.kickoff_utc, view.now);
  const adds = todo.added.filter(open);
  const drops = todo.dropped.filter(open);
  await gotoWeek(page, season, week);
  const banner = page.getByRole("region", { name: "Do this" });
  if (adds.length + drops.length + todo.flipped.filter(open).length === 0) {
    test.info().annotations.push({ type: "clock", description: "nothing left to change before kickoff" });
    await expect(banner).toHaveCount(0);
    return;
  }
  await expect(banner).toBeVisible();
  await expect(banner.locator(".step-add")).toHaveCount(adds.length);
  await expect(banner.locator(".step-drop")).toHaveCount(drops.length);
  for (const t of adds) await expect(banner.locator(".step-add .step-text", { hasText: new RegExp(`^${t} `) })).toBeVisible();
  for (const t of drops) await expect(banner.locator(".step-drop .step-text", { hasText: new RegExp(`^${t} `) })).toBeVisible();
  // One list only: no second "pending change" block repeating the same steps.
  await expect(page.getByText(/Pending change/i)).toHaveCount(0);
  await banner.getByRole("button", { name: "Show alert text" }).click();
  await expect(banner.locator(".alert-body")).toContainText("DO THIS");
  await expect(banner.locator(".alert-body")).toContainText("CURRENT PICKS");

  // Fetch lines (a saved run): the banner still shows a single list.
  await page.getByRole("button", { name: "Fetch lines" }).click();
  await expectToast(page, /Fetched \d+ market lines\./);
  await waitIdle(page);
  await expect(page.getByText(/Pending change/i)).toHaveCount(0);

  // "I've made these changes": the model's picks become your confirmed picks.
  const confirmBtn = page.getByRole("button", { name: /I've made these changes/ });
  if (await confirmBtn.count()) {
    await confirmBtn.click();
    await expectToast(page, /Noted: your picks in the app are/);
    await waitIdle(page);
    await expect(banner).toHaveCount(0);
    const after = await getWeek(request, season, week);
    expect(after.todo!.changed).toBe(false);
    expect(after.todo!.confirmed).toEqual(after.picks!.map((p) => p.team));
  }
});

test("a completed week shows no Do this banner", async ({ page, request }) => {
  const { season, week } = await currentWeek(request);
  const prev = await getWeek(request, season, week - 1);
  expect(prev.last_change).toBeTruthy();
  await gotoWeek(page, season, week - 1);
  await expect(page.getByRole("region", { name: "Do this" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Week total" })).toBeVisible();
});

test("replaces a pick when the set is full, then removes it again", async ({ page, request }) => {
  const { season, week, view } = await currentWeek(request);
  const original = view.picks!.map((p) => p.team);
  expect(original.length).toBe(view.n_picks);

  // A team in a game you don't have. A kicked-off one can't be swapped back out by the model.
  const unpicked = view.games!.filter((g) => !g.picked_team && g.league_home_spread != null);
  const game = unpicked.find((g) => g.locked) ?? [...unpicked].sort((a, b) => (b.edge ?? 0) - (a.edge ?? 0))[0]!;
  const incoming = game.edge && game.edge > 0 && game.best_team ? game.best_team : game.away;
  const out = [...view.picks!].sort((a, b) => a.edge - b.edge).find((p) => !p.manual)!;

  await gotoWeek(page, season, week);
  await gameRow(page, game.game_id).getByRole("button", { name: `Pick ${incoming}` }).click();
  const dlg = page.getByRole("dialog", { name: `Replace which pick with ${incoming}?` });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole("button")).toHaveCount(view.picks!.length + 1); // one per pick + close
  await dlg.getByRole("button", { name: new RegExp(escapeRe(out.label)) }).click();
  await confirmIfKickedOff(page);
  await expectToast(page, new RegExp(`(Your picks are now|You entered): .*${incoming}`));
  await waitIdle(page);

  const after = await getWeek(request, season, week);
  const teams = after.picks!.map((p) => p.team);
  if (game.locked) {
    // A kicked-off pick is locked in: it stays, and the pick it replaced is gone.
    expect(teams).toContain(incoming);
    expect(teams).not.toContain(out.team);
    await expect(pickTile(page, incoming)).toBeVisible();
    await expect(pickTile(page, out.team)).toHaveCount(0);
    await expect(gameRow(page, game.game_id).getByRole("button", { name: `Pick ${incoming}` })).toHaveAttribute("aria-pressed", "true");

    // Clicking the picked team again removes it.
    await clearToasts(page);
    await gameRow(page, game.game_id).getByRole("button", { name: `Pick ${incoming}` }).click();
    await confirmIfKickedOff(page);
    // Either the slot stays empty, or the toast says which team the model puts in it.
    await expectToast(page, /Your picks are now:|The model suggests .* for the open slot/);
    await waitIdle(page);
    await expect(pickTile(page, incoming)).toHaveCount(0);
    expect((await getWeek(request, season, week)).picks!.map((p) => p.team)).not.toContain(incoming);
  } else {
    // Open game: the team you picked by hand is locked, so the model can't swap it straight back.
    expect(teams).toContain(incoming);
    expect(teams).not.toContain(out.team);
    expect(after.picks!.find((p) => p.team === incoming)?.manual).toBe(true);
    await expect(pickTile(page, incoming)).toContainText("LOCKED by you");
    await expect(gameRow(page, game.game_id).getByRole("button", { name: `Pick ${incoming}` })).toHaveAttribute("aria-pressed", "true");
    for (const t of teams) await expect(pickTile(page, t)).toBeVisible();
  }

  // Put the demo picks back for the next tests.
  const res = await request.put(`/api/week/${season}/${week}/picks`, { data: { teams: original } });
  expect(res.ok()).toBeTruthy();

  // The banner never asks for a team you no longer hold (or to drop one you hold again).
  const restored = await getWeek(request, season, week);
  const have = restored.picks!.map((p) => p.team);
  await gotoWeek(page, season, week);
  for (const text of await page.locator(".do-this .step-add .step-text").allTextContents()) {
    expect(have).toContain(text.split(" ")[0]);
  }
  for (const text of await page.locator(".do-this .step-drop .step-text").allTextContents()) {
    expect(have).not.toContain(text.split(" ")[0]);
  }
});

test("locks and unlocks a pick before kickoff", async ({ page, request }) => {
  const { season, week, view } = await currentWeek(request);
  const p = view.picks!.find((x) => !x.locked && !kickedOff(x.kickoff_utc, view.now));
  test.skip(!p, "every pick has kicked off right now, so none can be locked by hand");
  await gotoWeek(page, season, week);
  const tile = pickTile(page, p!.team);
  await tile.getByRole("button", { name: `Lock ${p!.team}` }).click();
  await expectToast(page, `Locked ${p!.team}.`);
  await expect(tile).toContainText("LOCKED by you");
  const row = gameRow(page, p!.game_id);
  await expect(row.getByRole("button", { name: `Lock ${p!.team}` })).toHaveAttribute("aria-pressed", "true");
  expect((await getWeek(request, season, week)).picks!.find((x) => x.team === p!.team)?.manual).toBe(true);

  // Unlock from the games table toggle.
  await row.getByRole("button", { name: `Lock ${p!.team}` }).click();
  await expectToast(page, new RegExp(`Unlocked ${p!.team}`));
  await expect(tile).not.toContainText("LOCKED by you");
  await expect(row.getByRole("button", { name: `Lock ${p!.team}` })).toHaveAttribute("aria-pressed", "false");
});

test("overrides a league line: the edge changes and the star shows; revert puts it back", async ({ page, request }) => {
  const { season, week, view } = await currentWeek(request);
  // An unpicked game that has kicked off keeps the picks stable; otherwise any game with a market line.
  const withMarket = view.games!.filter((g) => g.market_home_spread != null && g.league_home_spread != null);
  const g = withMarket.find((x) => x.locked && !x.picked_team) ?? withMarket.find((x) => !x.picked_team) ?? withMarket[0]!;
  const newHome = g.market_home_spread! + 4; // the home team gets 4 more points than the market: edge +4 on home
  await gotoWeek(page, season, week);
  const row = gameRow(page, g.game_id);
  const edgeBefore = edgeText(g);
  await expect(row.locator(".c-edge")).toHaveText(edgeBefore);

  await row.getByRole("button", { name: `Edit league line for ${g.away} @ ${g.home}` }).click();
  const dlg = page.getByRole("dialog", { name: `League line: ${g.away} @ ${g.home}` });
  await dlg.locator("label.seg-opt", { hasText: new RegExp(`^${g.home}$`) }).click();
  await dlg.getByLabel(`${g.home} spread`).fill(fmtSpread(newHome));
  await expect(dlg).toContainText(`Will read: ${g.home} ${fmtSpread(newHome)} vs ${g.away}`);
  await dlg.getByRole("button", { name: "Save line" }).click();
  await expectToast(page, `League line set: ${g.home} ${fmtSpread(newHome)} vs ${g.away}`);
  await waitIdle(page);

  await expect(row.locator(".c-edge")).toHaveText(`+4.0 ${g.home}`);
  await expect(row.locator(".c-line")).toContainText("★");
  await expect(row.locator(".c-line .sheet-note")).toContainText(`sheet ${fmtSpread(g.sheet_home_spread)}`);
  const v1 = await getWeek(request, season, week);
  expect(v1.games!.find((x) => x.game_id === g.game_id)!.line_overridden).toBe(true);
  expect(v1.overrides!.lines.map((o) => o.game_id)).toContain(g.game_id);
  const picked = v1.picks!.find((p) => p.game_id === g.game_id);
  if (picked) await expect(pickTile(page, picked.team).locator(".star")).toBeVisible();

  // Revert from the same dialog.
  await row.getByRole("button", { name: `Edit league line for ${g.away} @ ${g.home}` }).click();
  await page.getByRole("button", { name: /^Revert to sheet/ }).click();
  await expectToast(page, `Cleared lines for ${g.away}@${g.home}`);
  await waitIdle(page);
  await expect(row.locator(".c-edge")).toHaveText(edgeBefore);
  await expect(row.locator(".c-line")).not.toContainText("★");
  expect((await getWeek(request, season, week)).overrides!.lines).toEqual([]);
});

test("sets up a week that has no league lines yet", async ({ page, request }) => {
  const { season, week } = await currentWeek(request);
  const next = week + 1;
  const v0 = await getWeek(request, season, next);
  test.skip(v0.has_league_file, "next week already set up");
  await page.goto(`/week/${season}/${next}`);
  const setup = page.getByRole("region", { name: "Set up this week" });
  await expect(setup).toContainText(`${season} week ${next} isn't set up yet`);
  await setup.getByRole("radio", { name: /nflverse spreads/ }).check();
  await setup.getByRole("button", { name: "Set up this week" }).click();
  await expectToast(page, new RegExp(`Seeded \\d+ games for ${season} week ${next}`));
  const v1 = await getWeek(request, season, next);
  expect(v1.has_league_file).toBe(true);
  await expect(page.locator(".games-table tbody tr")).toHaveCount(v1.games!.length);
});
