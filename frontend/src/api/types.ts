// Types from docs/api.md (the contract). Keep in sync with the backend.

export type Side = "HOME" | "AWAY";
export type PickStatus = "final" | "live" | "open";

export interface GameResult {
  home_score: number;
  away_score: number;
  final: boolean;
  source: "nflverse" | "score override";
}

export interface Game {
  game_id: string;
  away: string;
  home: string;
  kickoff_utc: string;
  locked: boolean;
  league_home_spread: number | null;
  sheet_home_spread: number | null;
  line_overridden: boolean;
  market_home_spread: number | null;
  books: number;
  best_side: Side | null;
  best_team: string | null;
  edge: number | null;
  result: GameResult | null;
  picked_side: Side | null;
  picked_team: string | null;
  lock: { team: string; side: Side; auto?: boolean; added?: boolean; replaced?: { team: string; side: Side } | null } | null;
}

export interface Pick {
  game_id: string;
  team: string;
  opponent: string;
  side: Side;
  is_home: boolean;
  league_spread: number | null;
  market_spread: number | null;
  label: string;
  kickoff_utc: string;
  edge: number;
  locked: boolean;
  manual: boolean;
  status: PickStatus;
  points: number;
  source: "points override" | "score override" | "nflverse" | "nflverse (live override superseded)" | "no league line" | "edge";
  line_overridden: boolean;
}

/** History records keep a subset of Pick fields (see docs/samples/week_record.json). */
export interface RecordPick {
  game_id: string;
  team: string;
  opponent: string;
  is_home: boolean;
  label: string;
  status: PickStatus;
  points: number;
  league_spread?: number | null;
  market_spread?: number | null;
  edge?: number;
  source?: string;
  locked?: boolean;
  manual?: boolean;
}

export interface Summary {
  final: number;
  live: number;
  expected: number;
  projected: number;
  n_final: number;
  n_live: number;
  n_open: number;
  line: string;
}

export interface Diff {
  changed: boolean;
  added: string[];
  dropped: string[];
  flipped: string[];
}

export interface OverrideItem<V> {
  game_id: string;
  away: string | null;
  home: string | null;
  value: V;
}

export interface Overrides {
  lines: OverrideItem<number>[];
  locks: OverrideItem<{ team: string; side: Side }>[];
  results: OverrideItem<{ home_score: number; away_score: number; final: boolean }>[];
  points: OverrideItem<{ team: string; value: number; final: boolean }>[];
}

export type LastChange = Diff & { at: string; reason: string; sent: boolean; body: string };

/**
 * What to change in the league app: the model's picks versus the picks you
 * last confirmed (every change since then, netted out), for games that
 * haven't kicked off. `confirmed` is null until you confirm picks this week,
 * in which case the baseline is an empty set.
 */
export type Todo = Diff & { since: string | null; confirmed: string[] | null };

export interface WeekView {
  season: number;
  week: number;
  now: string;
  has_league_file: boolean;
  market?: { kind: "snapshot" | "live"; fetched_at: string | null };
  last_run?: string | null;
  n_picks?: number;
  games?: Game[];
  picks?: Pick[];
  summary?: Summary;
  /** Changes the model would make to the presumed picks; empty right after a saved run. */
  diff?: Diff;
  todo?: Todo;
  warnings?: string[];
  overrides?: Overrides;
  last_change?: LastChange | null;
}

export interface Alert {
  title: string;
  body: string;
  changed: boolean;
  /** True only when ntfy or the webhook accepted the push. */
  sent: boolean;
  /** One line per channel that failed (no URLs or topics). */
  failures?: string[];
}

export interface Outcome {
  week: WeekView;
  alert: Alert | null;
  messages: string[];
}

export interface Run {
  id: number;
  season: number;
  week: number;
  at: string;
  reason: string;
  changed: boolean;
  sent: boolean;
  diff: Diff;
  picks: Pick[];
  summary: Summary;
  warnings: string[];
  title: string;
  body: string;
}

export interface WeekRecordUserFields {
  week_rank: number | null;
  entrants: number | null;
  overall_points: number | null;
  overall_rank: number | null;
  app_points: number | null;
  notes: string | null;
}

export interface WeekRecord extends WeekRecordUserFields {
  season: number;
  week: number;
  updated_at: string;
  picks: RecordPick[];
  n_picks: number;
  final_points: number;
  live_points: number;
  expected_open: number;
  projected: number;
  movement_edge: number;
  n_final: number;
  n_live: number;
  n_open: number;
  complete: boolean;
}

export interface HistoryTotals {
  weeks: number;
  complete_weeks: number;
  final_points: number;
  movement_edge: number;
  avg_week: number | null;
}

export interface HistoryResponse {
  seasons: number[];
  weeks: WeekRecord[];
  totals: HistoryTotals;
}

export type Provider = "oddsapi" | "espn" | "file";

export interface SchedulerSettings {
  enabled: boolean;
  interval_minutes: number;
  sunday_interval_minutes: number;
  auto_init_wednesday: boolean;
}

export interface Settings {
  provider: Provider;
  odds_api_key_set: boolean;
  odds_api_key_hint: string | null;
  ntfy_topic: string;
  ntfy_server: string;
  webhook_url: string;
  swap_margin: number;
  flip_margin: number;
  scheduler: SchedulerSettings;
}

export type SettingsUpdate = Partial<Omit<Settings, "odds_api_key_set" | "odds_api_key_hint" | "scheduler">> & {
  scheduler?: Partial<SchedulerSettings>;
  odds_api_key?: string;
};

export interface SchedulerStatus {
  enabled: boolean;
  running: boolean;
  last_tick: string | null;
  last_update_at: string | null;
  last_result: string | null;
  last_error: string | null;
  next_due: string | null;
}

export interface Meta {
  current: { season: number; week: number };
  now: string;
  seasons: number[];
  config: {
    n_picks: number;
    swap_margin: number;
    flip_margin: number;
    provider: string;
    odds_api_key_set: boolean;
    ntfy_topic_set: boolean;
    webhook_set: boolean;
  };
  scheduler: SchedulerStatus;
  /** When nflverse results were last downloaded. */
  results?: ResultsInfo;
}

export interface ResultsInfo {
  downloaded_at: string | null;
  offline: boolean;
}

export interface BacktestStatus {
  state: "idle" | "running" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  started?: boolean;
}

export interface ScheduleWeek {
  week: number;
  n_games: number;
  first_kickoff: string | null;
  last_kickoff: string | null;
  has_league_file: boolean;
  has_record: boolean;
}

export interface SnapshotPoint {
  t: string;
  home_spread: number | null;
  books: number;
  source: string;
}

export interface SnapshotGame {
  game_id: string;
  away: string;
  home: string;
  kickoff_utc: string;
  league_home_spread: number | null;
  series: SnapshotPoint[];
}

export interface Snapshots {
  games: SnapshotGame[];
}

export interface ScoreResponse {
  picks: { label: string; status: PickStatus; points: number; source: string }[];
  summary: Summary;
}

export interface SchedulerRunResult {
  ran: boolean;
  message: string;
  outcome?: Outcome;
}

export type OverrideKind = "line" | "lock" | "score" | "points";

/** Backtest results are rendered generically; only "it's JSON" is assumed. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
