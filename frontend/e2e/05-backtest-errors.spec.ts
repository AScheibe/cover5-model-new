import { expect, test } from "@playwright/test";
import { currentWeek, gameRow, getWeek, gotoWeek, kickedOff, pickTile, watchConsole } from "./helpers";

test("backtest page renders results or explains how to create them", async ({ page, request }) => {
  const con = watchConsole(page, [/status of 404/]);
  const res = await request.get("/api/backtest");
  await page.goto("/backtest");
  await expect(page.getByRole("heading", { name: "Backtest", level: 1 })).toBeVisible();
  if (res.status() === 404) {
    await expect(page.getByText("No backtest results yet")).toBeVisible();
    await expect(page.getByText("python -m cover5.backtest_movement")).toBeVisible();
  } else {
    expect(res.ok()).toBeTruthy();
    await expect(page.locator("table").first()).toBeVisible();
  }
  con.check();
});

test("a sixth pick is refused with the backend's message", async ({ request }) => {
  const { season, week, view } = await currentWeek(request);
  const taken = new Set(view.picks!.map((p) => p.game_id));
  const extra = view.games!.find((g) => !taken.has(g.game_id))!;
  const teams = [...view.picks!.map((p) => p.team), extra.home];
  expect(teams.length).toBe(view.n_picks! + 1);
  const res = await request.put(`/api/week/${season}/${week}/picks`, { data: { teams } });
  expect(res.status()).toBe(400);
  expect(await res.json()).toEqual({ detail: `${teams.length} teams given; the league allows ${view.n_picks}` });
  // Nothing changed.
  expect((await getWeek(request, season, week)).picks!.map((p) => p.team)).toEqual(view.picks!.map((p) => p.team));
});

test("the app shows backend errors as alerts and keeps the week unchanged", async ({ page, request }) => {
  const { season, week, view } = await currentWeek(request);
  await gotoWeek(page, season, week);
  const g = view.games![0]!;
  await gameRow(page, g.game_id).getByRole("button", { name: `Edit league line for ${g.away} @ ${g.home}` }).click();
  const dlg = page.getByRole("dialog", { name: `League line: ${g.away} @ ${g.home}` });
  await dlg.locator("label.seg-opt", { hasText: new RegExp(`^${g.home}$`) }).click();
  await dlg.getByLabel(`${g.home} spread`).fill("-75");
  await dlg.getByRole("button", { name: "Save line" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "spread must be a finite number between -60 and 60" })).toBeVisible();
  expect((await getWeek(request, season, week)).overrides!.lines).toEqual([]);

  // Points for a pick that hasn't kicked off: the button is disabled and says why.
  const open = view.picks!.find((p) => !kickedOff(p.kickoff_utc, view.now));
  if (open) {
    const btn = pickTile(page, open.team).getByRole("button", { name: `Enter points for ${open.team}` });
    await expect(btn).toBeDisabled();
    await expect(btn).toHaveAttribute("title", /once the game kicks off/);
  }
  // The API still refuses it, without naming a CLI flag.
  if (open) {
    const res = await request.put(`/api/week/${season}/${week}/points`, { data: { team: open.team, points: 3 } });
    expect(res.status()).toBe(400);
    const detail = (await res.json()).detail as string;
    expect(detail).toContain("hasn't kicked off yet");
    expect(detail).not.toContain("--week");
  }
});

test("API errors: unknown team, unknown route, bad body; SPA deep links and dev CORS work", async ({ page, request }) => {
  const { season, week } = await currentWeek(request);
  let res = await request.post(`/api/week/${season}/${week}/locks`, { data: { teams: ["XYZ"] } });
  expect(res.status()).toBe(400);
  expect((await res.json()).detail).toBe("unknown team 'XYZ'");
  res = await request.put(`/api/week/${season}/${week}/lines`, { data: { team: "KC" } });
  expect(res.status()).toBe(400);
  expect(typeof (await res.json()).detail).toBe("string");
  res = await request.get("/api/nope");
  expect(res.status()).toBe(404);
  expect(await res.json()).toEqual({ detail: "Not found: /api/nope" });
  res = await request.get(`/api/week/${season}/${week + 1 > 18 ? 1 : week + 1}/runs`);
  expect(res.ok()).toBeTruthy();

  res = await request.fetch(`/api/week/${season}/${week}/picks`, {
    method: "OPTIONS",
    headers: { Origin: "http://localhost:5173", "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type" },
  });
  expect(res.headers()["access-control-allow-origin"]).toBe("http://localhost:5173");

  // Deep links load the app (SPA fallback), and assets are served with the right type.
  await page.goto("/history");
  await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
  const html = await (await request.get("/settings")).text();
  const asset = /src="(\/assets\/[^"]+\.js)"/.exec(html)![1]!;
  const js = await request.get(asset);
  expect(js.headers()["content-type"]).toMatch(/javascript/);
});

test("setting up a week outside the schedule shows the backend's error", async ({ page, request }) => {
  await page.goto("/week/2099/3");
  const setup = page.getByRole("region", { name: "Set up this week" });
  await expect(setup).toContainText("2099 week 3 isn't set up yet");
  await setup.getByRole("radio", { name: /nflverse spreads/ }).check();
  await setup.getByRole("button", { name: "Set up this week" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "No regular season games found for 2099 week 3" })).toBeVisible();
  await expect(setup).toBeVisible();
  expect((await getWeek(request, 2099, 3)).has_league_file).toBe(false);
});
