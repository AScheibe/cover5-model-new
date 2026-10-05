import { expect, test } from "@playwright/test";
import { currentWeek, gameRow, gotoWeek, shot, watchConsole } from "./helpers";

const SIZES = [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
];

for (const size of SIZES) {
  test.describe(`screens ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test("every page renders without horizontal scroll or console errors", async ({ page, request }) => {
      const con = watchConsole(page, [/status of 404/]);
      const { season, week, view } = await currentWeek(request);
      const pages: [string, string][] = [
        [`/week/${season}/${week}`, "week"],
        [`/week/${season}/${week - 1}`, "week-previous"],
        [`/week/${season}/${week + 2}`, "week-setup"],
        ["/history", "history"],
        ["/backtest", "backtest"],
        ["/settings", "settings"],
      ];
      for (const [path, name] of pages) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(400); // charts animate in
        const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
        expect(sw, `${name} scrolls sideways`).toBeLessThanOrEqual(cw);
        await shot(page, name);
      }

      // A dialog and the single-game line chart.
      await gotoWeek(page, season, week);
      const g = view.games!.find((x) => x.market_home_spread != null)!;
      await page.getByRole("region", { name: "Line movement" }).getByLabel("Game").selectOption({ label: `${g.away} @ ${g.home}` });
      await page.waitForTimeout(300);
      await shot(page, "week-game-chart");
      await gameRow(page, g.game_id).getByRole("button", { name: `Edit league line for ${g.away} @ ${g.home}` }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.screenshot({ path: `${process.env.E2E_SCREENS ?? "test-results/screens"}/dialog-line-${size.width}x${size.height}.png` });
      con.check();
    });
  });
}
