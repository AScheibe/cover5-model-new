import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import type { Game, HistoryResponse, Meta, Pick, Settings, WeekView } from "../src/api/types";

import { fmtPoints } from "../src/lib/format";

export { fmtPoints, fmtSpread, pickLabel, teamSpread } from "../src/lib/format";

/** Where the page screenshots go (E2E_SCREENS, or test-results/screens). */
export const SCREENS = process.env.E2E_SCREENS ?? join("test-results", "screens");

async function json<T>(res: Awaited<ReturnType<APIRequestContext["get"]>>): Promise<T> {
  expect(res.ok(), `${res.url()} -> ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export const getMeta = async (req: APIRequestContext) => json<Meta>(await req.get("/api/meta"));
export const getWeek = async (req: APIRequestContext, season: number, week: number) =>
  json<WeekView>(await req.get(`/api/week/${season}/${week}`));
export const getHistory = async (req: APIRequestContext, season: number) =>
  json<HistoryResponse>(await req.get(`/api/history?season=${season}`));
export const getSettings = async (req: APIRequestContext) => json<Settings>(await req.get("/api/settings"));

/** The current week per the server, and its view. */
export async function currentWeek(req: APIRequestContext) {
  const meta = await getMeta(req);
  const { season, week } = meta.current;
  return { meta, season, week, view: await getWeek(req, season, week) };
}

export const kickedOff = (iso: string, nowIso: string) => new Date(iso).getTime() <= new Date(nowIso).getTime();

/** Open a week and wait until its picks are on screen. */
export async function gotoWeek(page: Page, season: number, week: number) {
  await page.goto(`/week/${season}/${week}`);
  await expect(page.getByRole("region", { name: "Your picks" })).toBeVisible();
}

export const pickTile = (page: Page, team: string) => page.getByRole("article", { name: new RegExp(`^Pick ${team} `) });
export const gameRow = (page: Page, gameId: string) => page.locator(`tr[data-game="${gameId}"]`);
export const statValue = (page: Page, label: "Final" | "Live" | "Expected" | "Projected") =>
  page.getByRole("region", { name: "Week total" }).locator(".stat", { has: page.locator(".stat-label", { hasText: label }) }).locator(".stat-value");

/** Wait for a notification containing ``text`` (info, success or error). */
export async function expectToast(page: Page, text: string | RegExp) {
  await expect(page.getByRole("region", { name: "Notifications" }).getByText(text).first()).toBeVisible();
}

/** Dismiss every toast so the next assertion only sees new ones. */
export async function clearToasts(page: Page) {
  const x = page.getByRole("button", { name: "Dismiss notification" });
  while ((await x.count()) > 0) await x.first().click();
}

/** Mutations disable every button while in flight; wait until the week is idle again. */
export async function waitIdle(page: Page) {
  await expect(page.locator(".week-grid")).toHaveAttribute("aria-busy", "false");
}

/**
 * Confirm the "this game has kicked off" dialog if it appears. Its title and
 * button name the action ("Remove DAL from your picks?" / "Remove DAL").
 */
export async function confirmIfKickedOff(page: Page) {
  const dlg = page.getByRole("dialog").filter({ hasText: "The league app locks a pick at kickoff" });
  const btn = dlg.locator(".dialog-foot .btn-primary");
  try {
    await btn.waitFor({ state: "visible", timeout: 1500 });
  } catch {
    return false;
  }
  await btn.click();
  return true;
}

export function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Points text the way a pick tile shows it. */
export function tilePoints(p: Pick) {
  return p.status === "open" ? fmtPoints(p.points, 1) : fmtPoints(p.points);
}

/** The edge cell text for a game (mirrors GamesTable). */
export function edgeText(g: Game) {
  if (g.edge == null) return "—";
  if (!(g.edge > 0) || !g.best_team) return "0.0";
  return `${fmtPoints(g.edge, 1)} ${g.best_team}`;
}

export async function shot(page: Page, name: string) {
  mkdirSync(SCREENS, { recursive: true });
  const vp = page.viewportSize();
  await page.screenshot({ path: join(SCREENS, `${name}-${vp?.width}x${vp?.height}.png`), fullPage: true });
}

/** Fail the test on any console error or warning (network 404s the page expects are allowed by pattern). */
export function watchConsole(page: Page, allow: RegExp[] = []) {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() !== "error" && m.type() !== "warning") return;
    const text = m.text();
    if (allow.some((r) => r.test(text))) return;
    problems.push(`${m.type()}: ${text}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  return {
    check: () => expect(problems, problems.join("\n")).toEqual([]),
  };
}

export type { Game, Locator, Pick, WeekView };
