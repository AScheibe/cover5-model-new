import { expect, test, type Page } from "@playwright/test";
import { expectToast, getHistory, getMeta } from "./helpers";

test.describe.configure({ mode: "serial" });

const recordRow = (page: Page, week: number) =>
  page.locator(".history-table tbody tr", { has: page.getByRole("link", { name: `Wk ${week}`, exact: true }) });

/** The inline editors are labelled per week ("Week rank, week 3"). */
const editBtn = (page: Page, week: number, label: string) =>
  recordRow(page, week).getByRole("button", { name: `Edit ${label}, week ${week}`, exact: true });
const editBox = (page: Page, week: number, label: string) =>
  recordRow(page, week).getByRole("textbox", { name: `${label}, week ${week}`, exact: true });

async function edit(page: Page, week: number, label: string, value: string) {
  await editBtn(page, week, label).click();
  const input = editBox(page, week, label);
  await input.fill(value);
  await input.press("Enter");
  await expect(editBtn(page, week, label)).toHaveText(value);
}

test("lists the saved weeks with picks, totals and standings", async ({ page, request }) => {
  const { current } = await getMeta(request);
  const h = await getHistory(request, current.season);
  expect(h.weeks.length).toBeGreaterThanOrEqual(2);
  await page.goto("/history");
  await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
  await expect(page.locator(".history-table tbody tr")).toHaveCount(h.weeks.length);
  const older = h.weeks[0]!;
  const row = recordRow(page, older.week);
  for (const p of older.picks) await expect(row.locator(".chip-team", { hasText: p.team })).toBeVisible();
  if (older.week_rank != null) await expect(editBtn(page, older.week, "Week rank")).toHaveText(String(older.week_rank));
});

test("edits week rank and overall points, refreshes records, and they persist after reload", async ({ page, request }) => {
  const { current } = await getMeta(request);
  const week = current.week - 1;
  await page.goto("/history");
  await expect(recordRow(page, week)).toBeVisible();

  await edit(page, week, "Week rank", "4");
  await edit(page, week, "Entrants", "10");
  await edit(page, week, "Overall points", "61.5");
  await edit(page, week, "Overall rank", "2");
  await edit(page, week, "Notes", "tied for 3rd on points");

  await page.getByRole("button", { name: "Refresh records" }).click();
  await expectToast(page, /Refreshed \d+ week records?\./);

  await page.reload();
  await expect(editBtn(page, week, "Week rank")).toHaveText("4");
  await expect(editBtn(page, week, "Entrants")).toHaveText("10");
  await expect(editBtn(page, week, "Overall points")).toHaveText("61.5");
  await expect(editBtn(page, week, "Overall rank")).toHaveText("2");
  await expect(editBtn(page, week, "Notes")).toHaveText("tied for 3rd on points");

  const rec = (await getHistory(request, current.season)).weeks.find((w) => w.week === week)!;
  expect(rec).toMatchObject({ week_rank: 4, entrants: 10, overall_points: 61.5, overall_rank: 2, notes: "tied for 3rd on points" });

  // Clearing a field stores null.
  const notes = editBtn(page, week, "Notes");
  await notes.click();
  await editBox(page, week, "Notes").fill("");
  await editBox(page, week, "Notes").press("Enter");
  await expect(notes).toHaveText("—");
  expect((await getHistory(request, current.season)).weeks.find((w) => w.week === week)!.notes).toBeNull();
});

test("rejects a rank that isn't a whole number", async ({ page, request }) => {
  const { current } = await getMeta(request);
  const week = current.week - 1;
  await page.goto("/history");
  const row = recordRow(page, week);
  await editBtn(page, week, "Week rank").click();
  const input = editBox(page, week, "Week rank");
  await input.fill("2.5");
  await input.press("Enter");
  await expect(row.getByText("Whole number")).toBeVisible();
  await input.press("Escape");
  await expect(editBtn(page, week, "Week rank")).toHaveText("4");
});

test("exports every record as a JSON download", async ({ page, request }) => {
  await page.goto("/history");
  const link = page.getByRole("link", { name: "Export JSON" });
  await expect(link).toHaveAttribute("href", "/api/history/export");
  const res = await request.get("/api/history/export");
  expect(res.headers()["content-disposition"]).toMatch(/attachment; filename="cover5-history-\d{8}\.json"/);
  const body = await res.json();
  expect(body.weeks.length).toBeGreaterThanOrEqual(3);
  expect(body.runs.length).toBeGreaterThan(0);
});
