import { expect, test } from "@playwright/test";
import { expectToast, getSettings } from "./helpers";

test.describe.configure({ mode: "serial" });

test("saves a setting that persists after reload", async ({ page, request }) => {
  await page.goto("/settings");
  await expect(page.getByLabel("Provider")).toHaveValue("file");
  const sunday = page.getByLabel("Sunday interval (minutes)");
  await expect(sunday).toHaveValue("15");
  await sunday.fill("20");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expectToast(page, "Settings saved.");
  await page.reload();
  await expect(page.getByLabel("Sunday interval (minutes)")).toHaveValue("20");
  await expect(page.getByLabel("Provider")).toHaveValue("file");
  expect((await getSettings(request)).scheduler.sunday_interval_minutes).toBe(20);

  // Saving with nothing changed says so instead of sending a request.
  await page.getByRole("button", { name: "Save settings" }).click();
  await expectToast(page, "Nothing changed.");

  await page.getByLabel("Sunday interval (minutes)").fill("15");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expectToast(page, "Settings saved.");
});

test("stores the odds API key masked and never sends it back", async ({ page, request }) => {
  const secret = "e2e-secret-odds-key-5678";
  await page.goto("/settings");
  const key = page.getByLabel("Odds API key");
  await expect(key).toHaveAttribute("type", "password");
  await key.fill(secret);
  await page.getByRole("button", { name: "Save settings" }).click();
  await expectToast(page, "Settings saved.");

  await page.reload();
  const key2 = page.getByLabel("Odds API key");
  await expect(key2).toHaveValue("");
  await expect(key2).toHaveAttribute("type", "password");
  await expect(page.getByText(/A key is saved \(…5678\)/)).toBeVisible();
  const raw = await (await request.get("/api/settings")).text();
  expect(raw).not.toContain(secret);
  const s = JSON.parse(raw);
  expect(s.odds_api_key_set).toBe(true);
  expect(s.odds_api_key_hint).toBe("…5678");
  const meta = await (await request.get("/api/meta")).text();
  expect(meta).not.toContain(secret);

  await page.getByRole("button", { name: "Clear key" }).click();
  await expectToast(page, "Odds API key cleared.");
  await expect(page.getByText("No key saved.")).toBeVisible();
  expect((await getSettings(request)).odds_api_key_set).toBe(false);
});

test("shows a server validation error for a bad setting", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("ntfy server").fill("ntfy.example");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("alert")).toContainText(/ntfy_server/);
  await page.reload();
  await expect(page.getByLabel("ntfy server")).toHaveValue("https://ntfy.sh");
});

test("runs the scheduler once on demand", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("button", { name: "Run scheduler now" }).click();
  await expect(page.locator(".run-msg")).not.toBeEmpty();
});
