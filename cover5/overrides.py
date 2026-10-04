"""User overrides for one week, kept apart from bot state.

Scheduled runs rewrite data/state/ every time, so anything the user types in
lives here instead and is never erased by the bot:

data/overrides/<season>_wk<NN>.json
{
  "lines":   {"<game_id>": -3.5},                     league home_spread to use
  "locks":   {"<game_id>": {"team": "TEN", "side": "AWAY"}},
  "results": {"<game_id>": {"home_score": 24, "away_score": 18, "final": true}},
  "points":  {"<game_id>": {"value": 5.5, "final": true}}
}

* lines   replace the league's Wednesday spread for a game.
* locks   pin a pick: it fills one of the five slots, the model never swaps or
          flips it, and no other pick in that game can be recommended.
* results replace the game score used to grade a pick (nflverse otherwise).
* points  replace the graded score of a pick outright, e.g. the number the
          league app shows. Points beat results, results beat nflverse.

"final": false marks a score typed in mid-game. It counts toward the running
total but is labelled live.
"""
from __future__ import annotations

import json

import pandas as pd

from cover5 import config

KINDS = ("lines", "locks", "results", "points")


def overrides_path(season: int, week: int):
    return config.OVERRIDES_DIR / f"{config.week_key(season, week)}.json"


def load_overrides(season: int, week: int) -> dict:
    p = overrides_path(season, week)
    ov = json.loads(p.read_text()) if p.exists() else {}
    for k in KINDS:
        ov.setdefault(k, {})
    return ov


def save_overrides(season: int, week: int, ov: dict) -> None:
    config.ensure_dirs()
    clean = {k: ov.get(k, {}) for k in KINDS}
    overrides_path(season, week).write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n")


def apply_line_overrides(league: pd.DataFrame, ov: dict) -> pd.DataFrame:
    """Return a copy of the league lines with overridden spreads swapped in.

    Adds two columns: ``line_overridden`` and ``sheet_home_spread`` (the value
    from the league CSV before the override), so alerts can say what changed.
    """
    out = league.copy()
    out["sheet_home_spread"] = out["home_spread"]
    out["line_overridden"] = False
    for gid, hs in ov.get("lines", {}).items():
        mask = out["game_id"] == gid
        if not mask.any():
            print(f"[overrides] WARNING line override for unknown game {gid} ignored")
            continue
        out.loc[mask, "home_spread"] = float(hs)
        out.loc[mask, "line_overridden"] = True
    return out


def clear(ov: dict, game_id: str, kinds: tuple[str, ...] = KINDS) -> list[str]:
    """Remove the given override kinds for a game; return the kinds removed."""
    removed = []
    for k in kinds:
        if game_id in ov.get(k, {}):
            del ov[k][game_id]
            removed.append(k)
    return removed


def describe(ov: dict, league: pd.DataFrame) -> list[str]:
    """Human readable list of every override in effect."""
    from cover5.scoring import fmt_pick
    rows = {r.game_id: r for r in league.itertuples()}
    lines = []

    def game(gid):
        r = rows.get(gid)
        return f"{r.away}@{r.home}" if r is not None else gid

    for gid, hs in sorted(ov.get("lines", {}).items()):
        r = rows.get(gid)
        label = fmt_pick(r.home, r.away, float(hs), True) if r is not None else f"home {hs:+g}"
        lines.append(f"line    {game(gid)}: {label}")
    for gid, lk in sorted(ov.get("locks", {}).items()):
        lines.append(f"lock    {game(gid)}: {lk['team']}")
    for gid, res in sorted(ov.get("results", {}).items()):
        r = rows.get(gid)
        tag = "final" if res.get("final", True) else "live"
        if r is not None:
            lines.append(f"score   {game(gid)}: {r.away} {res['away_score']:g}, {r.home} {res['home_score']:g} ({tag})")
        else:
            lines.append(f"score   {gid}: {res} ({tag})")
    for gid, pt in sorted(ov.get("points", {}).items()):
        tag = "final" if pt.get("final", True) else "live"
        lines.append(f"points  {game(gid)}: {pt['value']:+g} ({tag})")
    return lines
