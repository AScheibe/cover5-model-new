# Cover 5 local API

The web app is a React frontend (`frontend/`) talking to a FastAPI backend
(`cover5/server/`) that wraps `cover5/service.py`. Everything runs on the
user's machine: `python -m cover5 serve` starts the API on
`http://127.0.0.1:8765`, serves the built frontend from `frontend/dist`, and
runs the background scheduler.

Real example payloads live in `docs/samples/` and are generated from the
service layer, so they are the ground truth for shapes.

## Conventions

* JSON everywhere. Times are ISO 8601 strings with a UTC offset.
* `home_spread` values are sportsbook style: negative means the home team is
  favored. Fields named `*_spread` without `home` (on picks) are from the
  picked team's point of view, the way the league app shows them
  (`IND -3.5 @ WAS` has `league_spread: -3.5`).
* `season`/`week` are integers, regular season only.
* Teams are nflverse codes (`LA` is the Rams). Inputs also accept app
  abbreviations like `LAR` and `WSH`, full names, and nicknames.
* Errors: HTTP 400 with `{"detail": "<message for the user>"}` for anything
  the user can fix (unknown team, two picks in one game, week not
  initialised, bad number). 404 for unknown routes/resources. 502 with
  `{"detail": ...}` when the odds provider fails.
* Every mutating week endpoint returns an **Outcome**.

## Types

```ts
type Side = "HOME" | "AWAY";
type PickStatus = "final" | "live" | "open";

interface Game {
  game_id: string; away: string; home: string;
  kickoff_utc: string;
  locked: boolean;                       // kicked off
  league_home_spread: number | null;     // after overrides
  sheet_home_spread: number | null;      // the seeded league sheet, before overrides
  line_overridden: boolean;
  market_home_spread: number | null;     // last line before kickoff once kicked off
  books: number;
  best_side: Side | null; best_team: string | null;
  edge: number | null;                   // expected league points on best side
  result: { home_score: number; away_score: number; final: boolean;
            source: "nflverse" | "score override" } | null;
  picked_side: Side | null; picked_team: string | null;
  lock: { team: string; side: Side;            // a user lock on this game
          auto?: boolean;                      // added by entering points, not by "lock"
          added?: boolean;                     // auto lock that filled an open slot
          replaced?: { team: string; side: Side } | null } | null;  // the pick it replaced in this game
}

interface Pick {
  game_id: string; team: string; opponent: string; side: Side; is_home: boolean;
  league_spread: number | null;          // picked team's view
  market_spread: number | null;          // picked team's view
  label: string;                         // "IND -3.5 @ WAS", "TEN +11.5 @ BAL", "CHI PK vs NYJ"
  kickoff_utc: string;
  edge: number;
  locked: boolean;                       // kicked off or user lock
  manual: boolean;                       // user lock
  status: PickStatus;
  points: number;                        // graded points (final/live) or expected edge (open)
  source: "points override" | "score override" | "nflverse"
        | "nflverse (live override superseded)" | "no league line" | "edge";
  line_overridden: boolean;
}

interface Summary {
  final: number; live: number; expected: number; projected: number;
  n_final: number; n_live: number; n_open: number;
  line: string;                          // "Week total: +13.5 final (1 pick), ... Projected +18.5"
}

interface Diff { changed: boolean; added: string[]; dropped: string[]; flipped: string[]; }

interface OverrideItem<V> { game_id: string; away: string | null; home: string | null; value: V; }

interface WeekView {
  season: number; week: number; now: string;
  has_league_file: boolean;              // false: only season/week/now present
  market?: { kind: "snapshot" | "live"; fetched_at: string | null };
  last_run?: string | null;
  n_picks?: number;
  games?: Game[];                        // sorted by kickoff
  picks?: Pick[];                        // sorted by edge, desc
  summary?: Summary;
  diff?: Diff;                           // changes still pending vs. the picks you have (usually empty)
  warnings?: string[];
  overrides?: {
    lines:   OverrideItem<number>[];                                    // home_spread
    locks:   OverrideItem<{ team: string; side: Side }>[];
    results: OverrideItem<{ home_score: number; away_score: number; final: boolean }>[];
    points:  OverrideItem<{ team: string; value: number; final: boolean }>[];
  };
  last_change?: (Diff & { at: string; reason: string; sent: boolean; body: string }) | null;
                                         // the most recent run that changed picks: what you were told to do
}

interface Alert { title: string; body: string; changed: boolean; sent: boolean; }
interface Outcome { week: WeekView; alert: Alert | null; messages: string[]; }

interface Run {                          // one saved recompute (alert log)
  id: number; season: number; week: number; at: string; reason: string;
  changed: boolean; sent: boolean; diff: Diff; picks: Pick[]; summary: Summary;
  warnings: string[]; title: string; body: string;
}

interface WeekRecord {                   // one row of history
  season: number; week: number; updated_at: string;
  picks: Pick[];                         // subset of Pick fields, see docs/samples/week_record.json
  n_picks: number;
  final_points: number; live_points: number; expected_open: number; projected: number;
  movement_edge: number;                 // sum of pick edges: expected points from line movement
  n_final: number; n_live: number; n_open: number;
  complete: boolean;                     // all picks final
  // entered by the user from the league app; null until set
  week_rank: number | null; entrants: number | null;
  overall_points: number | null; overall_rank: number | null;
  app_points: number | null;             // the week score the app shows, to compare
  notes: string | null;
}

interface Settings {
  provider: "oddsapi" | "espn" | "file";   // file: COVER5_MARKET_FILE (offline demos, e2e tests)
  odds_api_key_set: boolean; odds_api_key_hint: string | null;   // e.g. "…1a2b"; never the key
  ntfy_topic: string; ntfy_server: string; webhook_url: string;
  swap_margin: number; flip_margin: number;
  scheduler: { enabled: boolean; interval_minutes: number;
               sunday_interval_minutes: number; auto_init_wednesday: boolean };
}

interface SchedulerStatus {
  enabled: boolean; running: boolean;
  last_tick: string | null; last_update_at: string | null;
  last_result: string | null; last_error: string | null;
  next_due: string | null;
}
```

## Endpoints

| method | path | body | returns |
| --- | --- | --- | --- |
| GET | `/api/meta` | | `{current: {season, week}, now, seasons: number[], config: {n_picks, swap_margin, flip_margin, provider, odds_api_key_set, ntfy_topic_set, webhook_set}, scheduler: SchedulerStatus}` |
| GET | `/api/schedule/{season}` | | `[{week, n_games, first_kickoff, last_kickoff, has_league_file, has_record}]` for every regular season week |
| GET | `/api/week/{season}/{week}` | | `WeekView` (read only, uses the last logged lines) |
| POST | `/api/week/{season}/{week}/init` | `{overwrite?: bool, use_market?: bool}` | Outcome |
| POST | `/api/week/{season}/{week}/update` | `{force_alert?: bool}` | Outcome (fetches the market) |
| PUT | `/api/week/{season}/{week}/picks` | `{teams: string[]}` (0 to n_picks) | Outcome (also removes locks on games not listed) |
| POST | `/api/week/{season}/{week}/locks` | `{teams: string[]}` | Outcome |
| DELETE | `/api/week/{season}/{week}/locks/{team}` | | Outcome |
| PUT | `/api/week/{season}/{week}/lines` | `{team, spread: number \| "PK"}` (team's view) | Outcome |
| PUT | `/api/week/{season}/{week}/scores` | `{team, team_points, opponent_points, live?: bool, force?: bool}` | Outcome; 400 before kickoff unless `force` |
| PUT | `/api/week/{season}/{week}/points` | `{team, points, live?: bool, force?: bool}` | Outcome (also locks that team); 400 before kickoff unless `force`, and 400 if the team isn't one of your picks while all slots are full |
| DELETE | `/api/week/{season}/{week}/overrides/{team}` | query `kind=line\|lock\|score\|points` (repeatable; none = all) | Outcome. Clearing points also removes the lock they added and restores the pick it replaced |
| GET | `/api/week/{season}/{week}/snapshots` | | `{games: [{game_id, away, home, kickoff_utc, league_home_spread, series: [{t, home_spread, books, source}]}]}` |
| GET | `/api/week/{season}/{week}/runs` | query `limit` | `Run[]`, newest first |
| GET | `/api/week/{season}/{week}/score` | | `{picks: [{label, status, points, source}], summary: Summary, warnings: string[]}` |
| GET | `/api/history` | query `season` | `{seasons: number[], weeks: WeekRecord[], totals: {weeks, complete_weeks, final_points, movement_edge, avg_week}}` |
| POST | `/api/history/refresh` | `{season?: number}` | `{updated: number, weeks: WeekRecord[]}` |
| PUT | `/api/history/{season}/{week}` | any of the user fields of WeekRecord | `WeekRecord` |
| GET | `/api/history/export` | | `{exported_at, weeks: WeekRecord[], runs: Run[]}` as a download |
| GET | `/api/backtest` | | contents of `data/history/backtest_results.json`, or 404 |
| GET | `/api/settings` | | `Settings` |
| PUT | `/api/settings` | partial Settings, plus `odds_api_key?: string` (empty string clears) | `Settings` |
| GET | `/api/scheduler` | | `SchedulerStatus` |
| POST | `/api/scheduler/run` | | `{ran: boolean, message: string, outcome?: Outcome}` (one tick now) |

## Scheduler

A background thread wakes every minute. When enabled it:

1. Initialises the current week (seeding from the market) if it has no league
   file, `auto_init_wednesday` is on, and it is at or after noon ET on the
   Wednesday before that week's first kickoff.
2. Runs an update when the time since the last update is at least the
   interval: `sunday_interval_minutes` on Sundays between 9:00 and 13:00 ET,
   otherwise `interval_minutes`. It skips weeks whose games have all kicked off.
3. Refreshes that week's history record after each update.

Errors are recorded in the status and never stop the thread.

## Storage

* `data/league_lines/`, `data/overrides/`, `data/state/`, `data/snapshots/`:
  the live week, shared with the CLI.
* `data/cover5.db`: SQLite history (`runs` and `weeks` tables, see
  `cover5/history.py`).
* `data/settings.json`: settings saved from the app, applied over environment
  variables at startup. Local only; ignored by git.

## Override rules

* **Picks** are the full truth for the week: setting them removes any lock on
  a game you didn't list, or on the other side of a game you did.
* **Locks** fill a slot. Locking a team that isn't one of your picks while all
  slots are full is allowed before its kickoff (the weakest open pick makes
  room, and the alert says so) and refused after it.
* **Points and scores** can only be entered once the game has kicked off
  (`force` overrides this). A score typed for a future game usually means the
  wrong week; the error names the previous week.
* **Live** overrides give way to the nflverse final once it exists; **final**
  overrides always win.
* A game missing from one market fetch keeps its last logged line, so a fetch
  and an override recompute always value every game the same way.
