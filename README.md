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

# Wednesday: seed the week's league lines from the current market, then open
# data/league_lines/<season>_wk<NN>.csv and fix any number that differs from
# the league sheet. home_spread is the number next to the home team (-3 = home
# favored by 3).
python -m cover5 init-week

# Any time after: fetch the market, recompute, alert on changes.
python -m cover5 update

# Or type the league sheet in the way the app shows it, from the named team's
# point of view (LAR, WSH and other app abbreviations are accepted):
python -m cover5 set-line IND -3.5
python -m cover5 set-line TEN 11.5

# If you deviated from the recommendation on the league site, say so, so the
# tracker reasons from what you actually have in.
python -m cover5 confirm KC

# After the games: score the week the way the app does, to check both agree.
python -m cover5 score-week

python -m cover5 status
python -m cover5 backtest
```

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
3. After the Wednesday run, edit the league CSV in `data/league_lines/` if the
   league sheet differs from the market snapshot, and commit.
4. Use the workflow's "Run workflow" button to trigger `update` on demand.

The polling cadence uses roughly 330 Odds API requests a month, under the free
tier's 500.

## Layout

```
cover5/
  scoring.py    league scoring rule and edge arithmetic
  providers.py  Odds API and ESPN fetch + parse
  schedule.py   nflverse schedule (kickoffs, ids, results, current week)
  league.py     the frozen Wednesday lines, one CSV per week
  picks.py      board, greedy pick update with hysteresis and locks
  state.py      per-week state json + market snapshot log
  alerts.py     message formatting and delivery
  backtest.py   historical checks; replay_snapshots() for your own logged data
  cli.py
data/
  league_lines/ <season>_wk<NN>.csv   (edit to match the league sheet)
  state/        <season>_wk<NN>.json  (last recommendation, confirmations)
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
