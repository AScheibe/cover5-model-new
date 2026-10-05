import type {
  BacktestStatus,
  HistoryResponse,
  Json,
  Meta,
  Outcome,
  OverrideKind,
  ResultsInfo,
  Run,
  ScheduleWeek,
  SchedulerRunResult,
  SchedulerStatus,
  ScoreResponse,
  Settings,
  SettingsUpdate,
  Snapshots,
  WeekRecord,
  WeekRecordUserFields,
  WeekView,
} from "./types";

/** An error from the API; `message` is the server's `detail` when it sent one. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailOf(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    // FastAPI validation errors: [{loc, msg, type}, ...]
    if (Array.isArray(d)) {
      const parts = d.map((x) => {
        if (x && typeof x === "object" && "msg" in x) {
          const loc = Array.isArray((x as { loc?: unknown }).loc)
            ? ((x as { loc: unknown[] }).loc.filter((l) => l !== "body").join("."))
            : "";
          return (loc ? loc + ": " : "") + String((x as { msg: unknown }).msg);
        }
        return JSON.stringify(x);
      });
      return parts.join("; ");
    }
    if (d != null) return JSON.stringify(d);
  }
  return null;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, `Can't reach the Cover 5 server (${e instanceof Error ? e.message : String(e)}). Is "python -m cover5 serve" running?`);
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const msg = detailOf(data) ?? (text && text.length < 300 ? text : `${res.status} ${res.statusText || "error"}`);
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

const w = (season: number, week: number) => `/api/week/${season}/${week}`;

/** `force` enters a score or points for a game the schedule says hasn't kicked off. */
export interface ScoreBody { team: string; team_points: number; opponent_points: number; live: boolean; force?: boolean }
export interface PointsBody { team: string; points: number; live: boolean; force?: boolean }
const enc = encodeURIComponent;

export const api = {
  meta: () => request<Meta>("GET", "/api/meta"),
  schedule: (season: number) => request<ScheduleWeek[]>("GET", `/api/schedule/${season}`),

  week: (s: number, wk: number) => request<WeekView>("GET", w(s, wk)),
  init: (s: number, wk: number, body: { overwrite?: boolean; use_market?: boolean }) =>
    request<Outcome>("POST", `${w(s, wk)}/init`, body),
  update: (s: number, wk: number, body: { force_alert?: boolean } = {}) =>
    request<Outcome>("POST", `${w(s, wk)}/update`, body),
  /** `lock`: teams of the set to lock as well (picked by hand, so the model keeps them). */
  setPicks: (s: number, wk: number, teams: string[], lock: string[] = []) =>
    request<Outcome>("PUT", `${w(s, wk)}/picks`, lock.length ? { teams, lock } : { teams }),
  /** "I've made these changes": the model's picks become the picks you confirmed. */
  confirm: (s: number, wk: number) => request<Outcome>("POST", `${w(s, wk)}/confirm`),
  /** Download nflverse results now and regrade the week. */
  results: (s: number, wk: number) => request<Outcome>("POST", `${w(s, wk)}/results`),
  lock: (s: number, wk: number, teams: string[]) => request<Outcome>("POST", `${w(s, wk)}/locks`, { teams }),
  unlock: (s: number, wk: number, team: string) => request<Outcome>("DELETE", `${w(s, wk)}/locks/${enc(team)}`),
  setLine: (s: number, wk: number, team: string, spread: number | "PK") =>
    request<Outcome>("PUT", `${w(s, wk)}/lines`, { team, spread }),
  setScore: (s: number, wk: number, body: ScoreBody) => request<Outcome>("PUT", `${w(s, wk)}/scores`, body),
  setPoints: (s: number, wk: number, body: PointsBody) =>
    request<Outcome>("PUT", `${w(s, wk)}/points`, body),
  clearOverride: (s: number, wk: number, team: string, kinds: OverrideKind[] = []) => {
    const q = kinds.map((k) => `kind=${enc(k)}`).join("&");
    return request<Outcome>("DELETE", `${w(s, wk)}/overrides/${enc(team)}${q ? "?" + q : ""}`);
  },
  snapshots: (s: number, wk: number) => request<Snapshots>("GET", `${w(s, wk)}/snapshots`),
  runs: (s: number, wk: number, limit = 50) => request<Run[]>("GET", `${w(s, wk)}/runs?limit=${limit}`),
  score: (s: number, wk: number) => request<ScoreResponse>("GET", `${w(s, wk)}/score`),

  history: (season?: number) =>
    request<HistoryResponse>("GET", season == null ? "/api/history" : `/api/history?season=${season}`),
  refreshHistory: (season?: number) =>
    request<{ updated: number; weeks: WeekRecord[]; results?: ResultsInfo }>("POST", "/api/history/refresh", season == null ? {} : { season }),
  updateRecord: (s: number, wk: number, fields: Partial<WeekRecordUserFields>) =>
    request<WeekRecord>("PUT", `/api/history/${s}/${wk}`, fields),
  exportUrl: "/api/history/export",

  backtest: () => request<Json>("GET", "/api/backtest"),
  backtestStatus: () => request<BacktestStatus>("GET", "/api/backtest/status"),
  runBacktest: () => request<BacktestStatus>("POST", "/api/backtest/run"),

  settings: () => request<Settings>("GET", "/api/settings"),
  saveSettings: (body: SettingsUpdate) => request<Settings>("PUT", "/api/settings", body),
  scheduler: () => request<SchedulerStatus>("GET", "/api/scheduler"),
  runScheduler: () => request<SchedulerRunResult>("POST", "/api/scheduler/run"),
};

export type Api = typeof api;
