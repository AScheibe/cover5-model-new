"""NFL schedule and results from nflverse (used for kickoff times, game ids, backtests)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from cover5 import config

ET = ZoneInfo("America/New_York")


def load_games(max_age_hours: float = 6.0, force: bool = False) -> pd.DataFrame:
    """Download (or reuse a cached copy of) the nflverse games file."""
    config.ensure_dirs()
    path = config.CACHE_DIR / "games.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    if force or not fresh:
        try:
            r = requests.get(config.NFLVERSE_GAMES_URL, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
        except requests.RequestException:
            if not path.exists():
                raise
    df = pd.read_csv(path, low_memory=False)
    df["kickoff_utc"] = _kickoffs(df)
    return df


def _kickoffs(df: pd.DataFrame) -> pd.Series:
    out = []
    for day, t in zip(df["gameday"], df["gametime"]):
        if isinstance(t, str) and t:
            dt = datetime.strptime(f"{day} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        else:
            dt = datetime.strptime(f"{day} 13:00", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        out.append(dt.astimezone(timezone.utc))
    return pd.Series(out, index=df.index, dtype="object")


def current_week(games: pd.DataFrame, now: datetime | None = None) -> tuple[int, int]:
    """The (season, week) a picker is working on right now.

    Each week's slate runs from the Tuesday before its first game through its last
    game. The current week is the earliest regular season week whose slate hasn't
    fully finished (with a few hours of slack after the last kickoff).
    """
    now = now or datetime.now(timezone.utc)
    reg = games[games["game_type"] == "REG"]
    grp = reg.groupby(["season", "week"])["kickoff_utc"].agg(["min", "max"]).reset_index()
    grp = grp.sort_values(["season", "week"])
    for _, row in grp.iterrows():
        if now < row["max"] + timedelta(hours=4):
            return int(row["season"]), int(row["week"])
    last = grp.iloc[-1]
    return int(last["season"]), int(last["week"])


def week_slate(games: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    s = games[(games["season"] == season) & (games["week"] == week) & (games["game_type"] == "REG")]
    cols = ["game_id", "season", "week", "away_team", "home_team", "kickoff_utc", "spread_line",
            "result", "home_score", "away_score"]
    return s[cols].sort_values("kickoff_utc").reset_index(drop=True)
