import { vi } from "vitest";
import weekView from "../../../docs/samples/week_view.json";
import weekViewUninit from "../../../docs/samples/week_view_uninitialised.json";
import outcomeSetLine from "../../../docs/samples/outcome_set_line.json";
import outcomeSetPoints from "../../../docs/samples/outcome_set_points.json";
import runs from "../../../docs/samples/runs.json";
import weekRecord from "../../../docs/samples/week_record.json";
import type { HistoryResponse, Meta, Outcome, Run, WeekRecord, WeekView } from "../api/types";

export const samples = {
  weekView: weekView as unknown as WeekView,
  weekViewUninit: weekViewUninit as unknown as WeekView,
  outcomeSetLine: outcomeSetLine as unknown as Outcome,
  outcomeSetPoints: outcomeSetPoints as unknown as Outcome,
  runs: runs as unknown as Run[],
  weekRecord: weekRecord as unknown as WeekRecord,
};

export const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

export const meta: Meta = {
  current: { season: 2026, week: 4 },
  now: "2026-10-04T21:05:07+00:00",
  seasons: [2026, 2025],
  config: { n_picks: 5, swap_margin: 0.5, flip_margin: 0.5, provider: "espn", odds_api_key_set: false, ntfy_topic_set: true, webhook_set: false },
  scheduler: { enabled: true, running: true, last_tick: null, last_update_at: null, last_result: null, last_error: null, next_due: null },
};

export interface Call {
  method: string;
  path: string;
  query: string;
  body: unknown;
}

type Handler = (call: Call) => unknown;

export interface MockServer {
  calls: Call[];
  /** Override or add a route: key "METHOD /path" (no query). */
  on: (key: string, h: Handler) => void;
  mutations: () => Call[];
}

/**
 * Mocks global fetch with sample data from docs/samples. Routes not set up
 * fall back to sensible defaults; mutating week routes return the sample
 * outcome unless overridden.
 */
export function mockServer(opts: { week?: WeekView } = {}): MockServer {
  const routes = new Map<string, Handler>();
  const calls: Call[] = [];
  const week = opts.week ?? clone(samples.weekView);
  const history: HistoryResponse = {
    seasons: [2026],
    weeks: [clone(samples.weekRecord)],
    totals: { weeks: 1, complete_weeks: 0, final_points: 0, movement_edge: 11.5, avg_week: null },
  };
  const outcome = (): Outcome => ({ week: clone(week), alert: null, messages: ["ok"] });

  routes.set("GET /api/meta", () => meta);
  routes.set(`GET /api/schedule/2026`, () =>
    Array.from({ length: 18 }, (_, i) => ({ week: i + 1, n_games: 16, first_kickoff: null, last_kickoff: null, has_league_file: i + 1 === 4, has_record: i + 1 === 4 })),
  );
  routes.set(`GET /api/week/${week.season}/${week.week}`, () => week);
  routes.set(`GET /api/week/${week.season}/${week.week}/runs`, () => samples.runs);
  routes.set(`GET /api/week/${week.season}/${week.week}/snapshots`, () => ({ games: [] }));
  routes.set("GET /api/history", () => history);

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const call: Call = { method, path: url.pathname, query: url.search, body };
    calls.push(call);
    const key = `${method} ${url.pathname}`;
    let h = routes.get(key);
    if (!h && method !== "GET" && url.pathname.startsWith("/api/week/")) h = () => outcome();
    if (!h) return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
    const r = (await h(call)) as { status?: number; body?: unknown } | unknown;
    if (r && typeof r === "object" && "status" in (r as object) && "body" in (r as object)) {
      const rr = r as { status?: number; body: unknown };
      return new Response(JSON.stringify(rr.body), { status: rr.status ?? 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify(r), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    on: (key, h) => routes.set(key, h),
    mutations: () => calls.filter((c) => c.method !== "GET"),
  };
}
