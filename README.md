# Cover 5 line tracker

A pick assistant for a "Cover 5" league: pick 5 NFL games against the spread each
week and score the margin by which each pick covers (negative when it doesn't).

## Why line movement is the model

The league publishes its spreads on Wednesday and freezes them. Picks lock at
each game's kickoff. The sportsbook market keeps moving all week, and the
closing line is the best available estimate of the real expected margin.
Backtesting 2010-2025 with nflverse closing lines shows:

* The closing line is unbiased: mean error +0.06 points, standard deviation 13.
* A simple power-rating model adds nothing once the market line is known
  (the best blend puts 100% weight on the market).
* Any fixed rule (favorites, dogs, home, away, high totals) averages near zero.

Because the scoring rule is linear in margin, every point the market moves away
from the league's frozen number is worth one expected league point to whoever is
on the right side of it. So the tracker:

1. Records the league's Wednesday spread for every game.
2. Polls current market spreads every couple of hours.
3. Computes each game's edge = how far the market has moved from the league
   number, on the better side.
4. Keeps the 5 best edges as your picks, with hysteresis so half-point jitter
   doesn't make you churn, and freezes picks whose game has kicked off.
5. Alerts you (phone push via ntfy, or Slack/Discord webhook) only when the
   recommended set changes: add, drop, or flip a side.

Expect it to tilt the odds, not guarantee anything. A 1 point per pick edge is
worth about +90 points a season against roughly 134 points of random noise, or
about a 68% chance of out-scoring a no-edge picker.

## Quick start (local)

```bash
pip install -r requirements.txt

# Wednesday: seed the week's league lines from the current market.
python -m cover5 init-week

# Any time after: fetch the market, recompute, alert on changes.
python -m cover5 update

# Correct any league line that differs from the app, from that team's view
# (LAR, WSH and other app abbreviations are accepted):
python -m cover5 set-line IND -3.5

# After the games: grade the week the way the app does.
python -m cover5 score-week

python -m cover5 status
python -m cover5 backtest
```

## Overrides: telling the tracker what is true

The tracker can't see the league app, so it assumes you follow its alerts.
When reality differs, tell it. Overrides live in `data/overrides/`, are never
erased by scheduled runs or by re-seeding the week, and every one immediately
recomputes the picks from the last logged lines and alerts if they change.

| command | what it does | how the model uses it |
| --- | --- | --- |
| `picks IND CHI LAR TEN JAX` | the picks you actually have in the app | replaces what the tracker thought you had; anything kicked off is locked |
| `lock TEN` / `unlock TEN` | pin a pick, before or after kickoff | takes one of the five slots; never swapped or flipped; the weakest open pick makes room |
| `set-line IND -3.5` | the league's line, from that team's view | edges, picks and grading all use it; marked `*` in alerts |
| `set-score IND 30 13 [--live]` | a game's score, that team's points first | grades whichever side you picked, using the league line |
| `set-points TEN 5.5 [--live]` | a pick's score exactly as the app shows it | beats any score; also locks TEN as your pick in that game |
| `overrides`, `clear IND [--kind line]` | list or remove overrides | |

Scores resolve in this order: points override, then score override, then the
nflverse final, then the pick's expected edge if the game isn't graded yet.
Alerts show each pick as `[FINAL +13.5]`, `[LIVE +8]`, `[LOCKED]` or
`[LOCKED by you]`, and end with a running week total:

```
Week total: +13.5 final (1 pick), +5.0 expected from line movement (4 open). Projected +18.5
```

Picks that have kicked off are valued at the last line logged before kickoff,
because the odds feeds switch to in-game lines once a game starts.

Banked points change the slots, the alerts and the week total. They don't
change which open games are best: each point of movement is worth the same
whether you're up 30 or down 30, and every pick carries about the same 13
points of game-to-game noise, so there is no safer or riskier pick to switch to.

From your phone, use the repo's Actions tab, open "cover5 line tracker", tap
"Run workflow", and type any of these commands (e.g. `lock TEN`). The workflow
records it, then runs a fresh update.

### Odds source

| provider | env | notes |
| --- | --- | --- |
| `oddsapi` | `ODDS_API_KEY` | The Odds API, free tier is 500 requests/month; median across US books. Default when the key is set. |
| `espn` | none | ESPN scoreboard, a single book. Default fallback. |

Pick one with `--provider` or `COVER5_PROVIDER`.

### Alerts

| env | effect |
| --- | --- |
| `NTFY_TOPIC` | pushes to `https://ntfy.sh/<topic>`; install the ntfy app and subscribe to the same topic |
| `ALERT_WEBHOOK_URL` | POSTs a Slack/Discord compatible JSON payload |

Console output is always printed. `--force-alert` pushes even when nothing changed.

### Tuning

| env | default | meaning |
| --- | --- | --- |
| `COVER5_SWAP_MARGIN` | 0.5 | a new game must beat the weakest current pick by this many points to replace it |
| `COVER5_FLIP_MARGIN` | 0.5 | flip a pick's side once its edge drops below minus this |
| `COVER5_N_PICKS` | 5 | picks per week |

## Running it unattended (GitHub Actions)

`.github/workflows/update.yml` runs `init-week` Wednesday at noon ET and
`update` every 2 hours Wednesday through Monday, plus every 30 minutes Sunday
morning ET. It commits `data/` back to the repo so state persists between runs.

1. Repo Settings, Secrets and variables, Actions: add `ODDS_API_KEY` and
   `NTFY_TOPIC` (and/or `ALERT_WEBHOOK_URL`).
2. Repo Settings, Actions, General: allow workflows read and write permissions.
3. After the Wednesday run, compare the alert to the league app and run
   `set-line` for any game that differs (from the "Run workflow" button).
4. Use the same button to run `update` or any override command on demand.

The polling cadence uses roughly 330 Odds API requests a month, under the free
tier's 500.

## Layout

```
cover5/
  scoring.py    league scoring rule and edge arithmetic
  providers.py  Odds API and ESPN fetch + parse
  schedule.py   nflverse schedule (kickoffs, ids, results, current week)
  league.py     the frozen Wednesday lines, one CSV per week
  overrides.py  your lines, locks, scores and points; never touched by the bot
  picks.py      board, slot-aware pick update with hysteresis and locks
  tally.py      grades picks: final, live, or expected
  state.py      per-week state json + market snapshot log
  alerts.py     message formatting and delivery
  backtest.py   historical checks; replay_snapshots() for your own logged data
  cli.py
data/
  league_lines/ <season>_wk<NN>.csv   (edit to match the league sheet)
  state/        <season>_wk<NN>.json  (picks the tracker believes you have)
  overrides/    <season>_wk<NN>.json  (your overrides)
  snapshots/    <season>_wk<NN>.csv   (every market read; grows a real
                                        Wednesday-vs-close dataset over time)
tests/
```

## Conventions

* `home_spread` is sportsbook style: negative means the home team is favored.
* Edge for the home side = league_home_spread - market_home_spread.
  League KC -3, market KC -6 gives KC an edge of +3.
* nflverse's `spread_line` is the expected home margin (opposite sign); it is
  converted on load.
