import { expect, test, type Page } from "@playwright/test";
import {
  clearToasts,
  currentWeek,
  expectToast,
  fmtPoints,
  gameRow,
  getWeek,
  gotoWeek,
  kickedOff,
  pickTile,
  statValue,
  tilePoints,
  waitIdle,
  type Pick,
} from "./helpers";

test.describe.configure({ mode: "serial" });

async function enterPoints(page: Page, p: Pick, value: string, live: boolean) {
  await clearToasts(page);
  await pickTile(page, p.team).getByRole("button", { name: `Enter points for ${p.team}` }).click();
  const dlg = page.getByRole("dialog", { name: new RegExp(`^Points: ${p.team} `) });
  await dlg.getByLabel("Points", { exact: true }).fill(value);
  await dlg.getByLabel("Game still in progress (live)").setChecked(live);
  await dlg.getByRole("button", { name: "Save points" }).click();
  await waitIdle(page);
}

test("live points then final points for a pick change the week total", async ({ page, request }) => {
  const cur = await currentWeek(request);
  // A pick whose game is under way (kicked off, no final yet) takes live points as they are.
  const inPlay = cur.view.picks!.find((p) => p.status !== "final" && kickedOff(p.kickoff_utc, cur.view.now));
  const season = cur.season;
  const week = inPlay ? cur.week : cur.week - 1;
  const v0 = inPlay ? cur.view : await getWeek(request, season, week);
  const p = inPlay ?? v0.picks!.find((x) => x.status === "final")!;
  expect(p, "a pick to enter points for").toBeTruthy();
  const s0 = v0.summary!;
  await gotoWeek(page, season, week);
  await expect(statValue(page, "Final")).toHaveText(fmtPoints(s0.final, 1));

  // Live points.
  await enterPoints(page, p, "7", true);
  const tile = pickTile(page, p.team);
  if (inPlay) {
    await expectToast(page, `${p.team} scored +7 (live)`);
    await expect(page.getByTestId(`points-${p.team}`)).toHaveText("+7");
    await expect(tile).toContainText("live pts");
    await expect(tile.locator(".badge", { hasText: "LIVE" })).toBeVisible();
    await expect(statValue(page, "Live")).toHaveText(fmtPoints(s0.live + 7, 1));
    const prior = p.status === "open" ? p.points : 0;
    await expect(statValue(page, "Projected")).toHaveText(fmtPoints(s0.projected - prior + 7, 1));
  } else {
    // nflverse already has the final, which beats a live number typed mid-game.
    await expectToast(page, `${p.team} scored +7 (live)`);
    await expect(tile).toContainText("nflverse (live override superseded)");
    await expect(page.getByTestId(`points-${p.team}`)).toHaveText(tilePoints(p));
    await expect(statValue(page, "Final")).toHaveText(fmtPoints(s0.final, 1));
  }
  await expect(tile).toContainText("LOCKED by you");

  // Final points: always win, exactly as the app shows them.
  await enterPoints(page, p, "10.5", false);
  await expectToast(page, `${p.team} scored +10.5 (final)`);
  await expect(page.getByTestId(`points-${p.team}`)).toHaveText("+10.5");
  await expect(tile.locator(".badge", { hasText: "FINAL" })).toBeVisible();
  const banked = p.status === "final" ? p.points : 0;
  await expect(statValue(page, "Final")).toHaveText(fmtPoints(s0.final - banked + 10.5, 1));
  const v1 = await getWeek(request, season, week);
  expect(v1.summary!.final).toBeCloseTo(s0.final - banked + 10.5, 5);
  expect(v1.picks!.find((x) => x.team === p.team)!.source).toBe("points override");

  // Clearing the entered points restores the graded value and the earlier total.
  await pickTile(page, p.team).getByRole("button", { name: `Enter points for ${p.team}` }).click();
  await page.getByRole("button", { name: "Clear entered points" }).click();
  await expectToast(page, /Cleared points/);
  await waitIdle(page);
  await expect(statValue(page, "Final")).toHaveText(fmtPoints(s0.final, 1));
  await expect(page.getByTestId(`points-${p.team}`)).toHaveText(tilePoints(p));
});

test("enters a final score for a picked game, then reverts to nflverse", async ({ page, request }) => {
  const { season, week: cur } = await currentWeek(request);
  const week = cur - 1; // a completed week: every game has kicked off
  const v0 = await getWeek(request, season, week);
  const p = v0.picks!.find((x) => x.status === "final" && x.league_spread != null)!;
  const g = v0.games!.find((x) => x.game_id === p.game_id)!;
  const r = g.result!;
  // Give the picked team 7 more points than it really scored.
  const teamScore = (p.is_home ? r.home_score : r.away_score) + 7;
  const oppScore = p.is_home ? r.away_score : r.home_score;
  const away = p.is_home ? oppScore : teamScore;
  const home = p.is_home ? teamScore : oppScore;
  const expected = teamScore - oppScore + p.league_spread!;

  await gotoWeek(page, season, week);
  const row = gameRow(page, g.game_id);
  await row.getByRole("button", { name: `Enter score for ${g.away} @ ${g.home}` }).click();
  const dlg = page.getByRole("dialog", { name: `Score: ${g.away} @ ${g.home}` });
  await dlg.getByLabel(`${g.away} (away)`).fill(String(away));
  await dlg.getByLabel(`${g.home} (home)`).fill(String(home));
  await dlg.getByLabel("Game still in progress (live score)").setChecked(false);
  await dlg.getByRole("button", { name: "Save score" }).click();
  await expectToast(page, `Score recorded: ${g.away} ${away}, ${g.home} ${home} (final)`);
  await waitIdle(page);

  await expect(row.locator(".c-result")).toContainText(`${g.away} ${away} – ${home} ${g.home}`);
  await expect(row.locator(".c-result")).toContainText("★");
  await expect(page.getByTestId(`points-${p.team}`)).toHaveText(fmtPoints(expected));
  await expect(pickTile(page, p.team)).toContainText("score override");
  await expect(statValue(page, "Final")).toHaveText(fmtPoints(v0.summary!.final - p.points + expected, 1));

  await row.getByRole("button", { name: `Enter score for ${g.away} @ ${g.home}` }).click();
  await page.getByRole("button", { name: "Revert to nflverse" }).click();
  await expectToast(page, `Cleared results for ${g.away}@${g.home}`);
  await waitIdle(page);
  await expect(row.locator(".c-result")).toContainText(`${g.away} ${r.away_score} – ${r.home_score} ${g.home}`);
  await expect(row.locator(".c-result")).not.toContainText("★");
  await expect(page.getByTestId(`points-${p.team}`)).toHaveText(tilePoints(p));
});
