"""Grade a week's picks: banked points from finished games, running points from
games in progress, and expected points (edge) for picks still to be decided.

Precedence for a pick's score: points override > score override > nflverse
final result > not yet graded (expected = edge).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cover5.picks import Pick
from cover5.scoring import HOME, pick_score

FINAL, LIVE, OPEN = "final", "live", "open"


@dataclass
class PickTally:
    pick: Pick
    status: str          # FINAL, LIVE or OPEN
    points: float        # graded points for FINAL/LIVE, expected edge for OPEN
    source: str          # "points override", "score override", "nflverse", "edge"


def _nflverse_margin(results: pd.DataFrame | None, game_id: str) -> float | None:
    if results is None or results.empty or game_id not in results.index:
        return None
    v = results.loc[game_id, "result"]
    return None if pd.isna(v) else float(v)


def tally_picks(picks: list[Pick], league: pd.DataFrame, ov: dict,
                results: pd.DataFrame | None = None) -> list[PickTally]:
    """``league`` must already have line overrides applied. ``results`` is the
    nflverse games frame indexed by game_id (only the ``result`` column is used)."""
    lg = league.set_index("game_id")
    out = []
    for p in picks:
        pts = ov.get("points", {}).get(p.game_id)
        if pts is not None:
            out.append(PickTally(p, FINAL if pts.get("final", True) else LIVE,
                                 float(pts["value"]), "points override"))
            continue
        hs = lg.loc[p.game_id, "home_spread"] if p.game_id in lg.index else float("nan")
        res = ov.get("results", {}).get(p.game_id)
        if res is not None and pd.notna(hs):
            margin = float(res["home_score"]) - float(res["away_score"])
            out.append(PickTally(p, FINAL if res.get("final", True) else LIVE,
                                 pick_score(margin, float(hs), p.side), "score override"))
            continue
        margin = _nflverse_margin(results, p.game_id)
        if margin is not None and pd.notna(hs):
            out.append(PickTally(p, FINAL, pick_score(margin, float(hs), p.side), "nflverse"))
            continue
        out.append(PickTally(p, OPEN, float(p.edge), "edge"))
    return out


def summarize(tallies: list[PickTally]) -> dict:
    final = sum(t.points for t in tallies if t.status == FINAL)
    live = sum(t.points for t in tallies if t.status == LIVE)
    expected = sum(t.points for t in tallies if t.status == OPEN)
    return {
        "final": final, "live": live, "expected": expected,
        "n_final": sum(t.status == FINAL for t in tallies),
        "n_live": sum(t.status == LIVE for t in tallies),
        "n_open": sum(t.status == OPEN for t in tallies),
        "projected": final + live + expected,
    }


def summary_line(s: dict) -> str:
    parts = []
    if s["n_final"]:
        parts.append(f"{s['final']:+.1f} final ({s['n_final']} pick{'s' if s['n_final'] != 1 else ''})")
    if s["n_live"]:
        parts.append(f"{s['live']:+.1f} live ({s['n_live']})")
    if s["n_open"]:
        parts.append(f"{s['expected']:+.1f} expected from line movement ({s['n_open']} open)")
    body = ", ".join(parts) if parts else "no picks"
    return f"Week total: {body}. Projected {s['projected']:+.1f}"
