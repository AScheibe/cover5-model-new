"""Turn league lines + market lines into a ranked board and a 5-pick recommendation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from cover5 import config
from cover5.providers import MarketLine
from cover5.scoring import HOME, AWAY, best_side, side_edge, fmt_spread


@dataclass
class Pick:
    game_id: str
    side: str            # HOME or AWAY
    team: str
    edge: float
    locked: bool = False

    def key(self) -> tuple[str, str]:
        return (self.game_id, self.side)


def build_board(league: pd.DataFrame, market: list[MarketLine], now: datetime | None = None) -> pd.DataFrame:
    """One row per game: league spread, market spread, best side and its edge."""
    now = now or datetime.now(timezone.utc)
    by_pair = {(m.away, m.home): m for m in market}
    rows = []
    for g in league.itertuples():
        m = by_pair.get((g.away, g.home))
        league_hs = g.home_spread
        market_hs = m.home_spread if m else float("nan")
        has = pd.notna(league_hs) and pd.notna(market_hs)
        side, edge = best_side(league_hs, market_hs) if has else (HOME, 0.0)
        rows.append({
            "game_id": g.game_id, "away": g.away, "home": g.home,
            "kickoff_utc": g.kickoff_utc,
            "locked": now >= g.kickoff_utc,
            "league_home_spread": league_hs, "market_home_spread": market_hs,
            "move": (league_hs - market_hs) if has else float("nan"),
            "best_side": side, "best_team": g.home if side == HOME else g.away,
            "edge": edge if has else float("nan"),
            "books": m.books if m else 0,
            "league_str": fmt_spread(g.away, g.home, league_hs),
            "market_str": fmt_spread(g.away, g.home, market_hs),
        })
    board = pd.DataFrame(rows)
    # Ties go to the later kickoff: a game that locks later can still be flipped
    # if its line moves against you, so it carries option value.
    return board.sort_values(["edge", "kickoff_utc"], ascending=[False, False], na_position="last").reset_index(drop=True)


def _edge_for(board_row, side: str) -> float:
    if pd.isna(board_row.league_home_spread) or pd.isna(board_row.market_home_spread):
        return 0.0
    return side_edge(side, board_row.league_home_spread, board_row.market_home_spread)


def recommend(board: pd.DataFrame, current: list[Pick] | None,
              n: int = config.N_PICKS,
              swap_margin: float = config.SWAP_MARGIN,
              flip_margin: float = config.FLIP_MARGIN) -> list[Pick]:
    """Greedy update of the pick set with hysteresis.

    * Picks whose game has kicked off are frozen as they were.
    * An unlocked current pick flips side if its edge has gone below -flip_margin.
    * A non-picked game displaces the weakest unlocked pick only if its edge beats
      that pick's edge by at least swap_margin. Games already locked and not
      picked can never be added.
    * If fewer than n picks exist (first run of the week), fill with the best
      available unlocked games.
    """
    rows = {r.game_id: r for r in board.itertuples()}
    current = list(current or [])
    picks: list[Pick] = []

    for p in current:
        r = rows.get(p.game_id)
        if r is None:
            continue
        if r.locked:
            picks.append(Pick(p.game_id, p.side, p.team, p.edge, locked=True))
            continue
        side = p.side
        e = _edge_for(r, side)
        if e < -flip_margin:
            side = AWAY if side == HOME else HOME
            e = _edge_for(r, side)
        picks.append(Pick(p.game_id, side, r.home if side == HOME else r.away, e, locked=False))

    picked_ids = {p.game_id for p in picks}
    candidates = [r for r in board.itertuples()
                  if r.game_id not in picked_ids and not r.locked and pd.notna(r.edge)]
    candidates.sort(key=lambda r: (-r.edge, -r.kickoff_utc.timestamp()))

    # fill empty slots first
    while len(picks) < n and candidates:
        r = candidates.pop(0)
        picks.append(Pick(r.game_id, r.best_side, r.best_team, float(r.edge)))

    # then consider swaps against the weakest unlocked pick
    changed = True
    while changed and candidates:
        changed = False
        unlocked = [p for p in picks if not p.locked]
        if not unlocked:
            break
        weakest = min(unlocked, key=lambda p: p.edge)
        cand = candidates[0]
        if cand.edge >= weakest.edge + swap_margin:
            picks.remove(weakest)
            candidates.pop(0)
            picks.append(Pick(cand.game_id, cand.best_side, cand.best_team, float(cand.edge)))
            changed = True

    picks.sort(key=lambda p: (-p.edge))
    return picks


def diff_picks(old: list[Pick], new: list[Pick]) -> dict:
    """Describe what a picker has to change on the league site."""
    old_by_game = {p.game_id: p for p in old}
    new_by_game = {p.game_id: p for p in new}
    added = [p for g, p in new_by_game.items() if g not in old_by_game]
    dropped = [p for g, p in old_by_game.items() if g not in new_by_game]
    flipped = [new_by_game[g] for g in new_by_game if g in old_by_game and old_by_game[g].side != new_by_game[g].side]
    return {"added": added, "dropped": dropped, "flipped": flipped,
            "changed": bool(added or dropped or flipped)}
