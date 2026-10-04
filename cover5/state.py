"""Per-week bot state (the picks the tracker believes you have) and the market snapshot log."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

from cover5 import config
from cover5.picks import Pick
from cover5.providers import MarketLine


def state_path(season: int, week: int):
    return config.STATE_DIR / f"{config.week_key(season, week)}.json"


def load_state(season: int, week: int) -> dict:
    p = state_path(season, week)
    if not p.exists():
        return {"recommended": [], "history": [], "last_run": None}
    return json.loads(p.read_text())


def save_state(season: int, week: int, st: dict) -> None:
    config.ensure_dirs()
    state_path(season, week).write_text(json.dumps(st, indent=2, default=str))


def picks_from_state(st: dict) -> list[Pick]:
    """The picks the tracker believes you have entered in the league app.

    That is the last recommendation (it assumes you follow alerts) unless you
    told it otherwise with the ``picks`` command, which rewrites this list.
    Lock flags are not trusted from disk: kickoff locks come from the board and
    manual locks from the overrides file on every run.
    """
    return [Pick(p["game_id"], p["side"], p["team"], float(p.get("edge", 0.0)))
            for p in st.get("recommended", [])]


def picks_to_state(picks: list[Pick]) -> list[dict]:
    return [{"game_id": p.game_id, "side": p.side, "team": p.team,
             "edge": round(p.edge, 2), "locked": p.locked, "manual": p.manual} for p in picks]


def append_snapshot(season: int, week: int, market: list[MarketLine]) -> None:
    """Log every market read so open-vs-close movement can be backtested later."""
    config.ensure_dirs()
    p = config.SNAPSHOT_DIR / f"{config.week_key(season, week)}.csv"
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fetched_at", "away", "home", "kickoff_utc", "home_spread", "books", "source"])
        if new:
            w.writeheader()
        for m in market:
            w.writerow(m.to_row())


def load_snapshots(season: int, week: int):
    import pandas as pd
    p = config.SNAPSHOT_DIR / f"{config.week_key(season, week)}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
