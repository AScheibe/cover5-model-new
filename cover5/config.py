"""Paths and environment driven settings."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("COVER5_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
LEAGUE_DIR = DATA / "league_lines"
STATE_DIR = DATA / "state"
SNAPSHOT_DIR = DATA / "snapshots"
CACHE_DIR = DATA / "cache"

NFLVERSE_GAMES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
)

# Odds provider: "oddsapi" (needs ODDS_API_KEY) or "espn" (no key).
PROVIDER = os.environ.get("COVER5_PROVIDER", "oddsapi" if os.environ.get("ODDS_API_KEY") else "espn")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Alert channels. Console output is always on.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")           # e.g. "cover5-alex-8f3k"
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")  # Slack/Discord compatible

# Pick-selection hysteresis, in points. A new game replaces a current pick only
# when its edge beats the outgoing pick's edge by SWAP_MARGIN; a side flips only
# once the current side's edge is below -FLIP_MARGIN.
SWAP_MARGIN = float(os.environ.get("COVER5_SWAP_MARGIN", "0.5"))
FLIP_MARGIN = float(os.environ.get("COVER5_FLIP_MARGIN", "0.5"))
N_PICKS = int(os.environ.get("COVER5_N_PICKS", "5"))


def week_key(season: int, week: int) -> str:
    return f"{season}_wk{week:02d}"


def ensure_dirs() -> None:
    for d in (LEAGUE_DIR, STATE_DIR, SNAPSHOT_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
