"""Per-week state: last recommendation, confirmed picks, and a market snapshot log."""
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
        return {"recommended": [], "confirmed": {}, "history": [], "last_run": None}
    return json.loads(p.read_text())


def save_state(season: int, week: int, st: dict) -> None:
    config.ensure_dirs()
    state_path(season, week).write_text(json.dumps(st, indent=2, default=str))


def picks_from_state(st: dict) -> list[Pick]:
    """Current picks = recommendation, overridden by anything the user confirmed."""
    picks = [Pick(**{k: p[k] for k in ("game_id", "side", "team", "edge", "locked")}) for p in st.get("recommended", [])]
    for game_id, c in st.get("confirmed", {}).items():
        # a confirmed pick replaces the recommendation for that game (or adds it)
        picks = [p for p in picks if p.game_id != game_id]
        picks.append(Pick(game_id, c["side"], c["team"], 0.0))
    return picks


def picks_to_state(picks: list[Pick]) -> list[dict]:
    return [{"game_id": p.game_id, "side": p.side, "team": p.team,
             "edge": round(p.edge, 2), "locked": p.locked} for p in picks]


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
