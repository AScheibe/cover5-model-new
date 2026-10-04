"""Turn league lines + market lines into a ranked board and a 5-pick recommendation."""
from __future__ import annotations

from dataclasses import dataclass
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
    locked: bool = False   # cannot change: game kicked off, or the user locked it
    manual: bool = False   # locked by a user override rather than by kickoff

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
            "line_overridden": bool(getattr(g, "line_overridden", False)),
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


def lock_time_market(market: list[MarketLine], snapshots: pd.DataFrame | None,
                     league: pd.DataFrame, now: datetime | None = None) -> list[MarketLine]:
    """Replace the line for every game that has kicked off with the last line
    logged before its kickoff.

    After kickoff the odds feeds return in-game lines (or nothing), which say
    nothing about what the pick was worth when it locked. Games without a
    pre-kickoff snapshot keep whatever the feed returned.
    """
    now = now or datetime.now(timezone.utc)
    if snapshots is None or snapshots.empty:
        return list(market)
    snaps = snapshots.copy()
    snaps["fetched_at"] = pd.to_datetime(snaps["fetched_at"], utc=True, format="ISO8601")
    by_pair = {(m.away, m.home): m for m in market}
    for g in league.itertuples():
        if now < g.kickoff_utc:
            continue
        s = snaps[(snaps["away"] == g.away) & (snaps["home"] == g.home)
                  & (snaps["fetched_at"] < pd.Timestamp(g.kickoff_utc))]
        if s.empty:
            continue
        last = s.sort_values("fetched_at").iloc[-1]
        by_pair[(g.away, g.home)] = MarketLine(
            g.away, g.home, g.kickoff_utc, float(last["home_spread"]), int(last.get("books", 1) or 1),
            f"{last.get('source', 'snapshot')}@lock", last["fetched_at"].to_pydatetime())
    return list(by_pair.values())


def latest_snapshot_market(snapshots: pd.DataFrame | None) -> list[MarketLine]:
    """Most recent logged line per game, for recomputing without a network fetch."""
    if snapshots is None or snapshots.empty:
        return []
    snaps = snapshots.copy()
    snaps["fetched_at"] = pd.to_datetime(snaps["fetched_at"], utc=True, format="ISO8601")
    snaps["kickoff_utc"] = pd.to_datetime(snaps["kickoff_utc"], utc=True, format="ISO8601")
    last = snaps.sort_values("fetched_at").groupby(["away", "home"], as_index=False).tail(1)
    return [MarketLine(r.away, r.home, r.kickoff_utc.to_pydatetime(), float(r.home_spread),
                       int(r.books), str(r.source), r.fetched_at.to_pydatetime())
            for r in last.itertuples()]


def _edge_for(board_row, side: str) -> float:
    if pd.isna(board_row.league_home_spread) or pd.isna(board_row.market_home_spread):
        return 0.0
    return side_edge(side, board_row.league_home_spread, board_row.market_home_spread)


def _team(row, side: str) -> str:
    return row.home if side == HOME else row.away


def apply_locks(current: list[Pick], locks: dict) -> list[Pick]:
    """Overlay user locks on the presumed current picks.

    A lock replaces whatever pick the tracker thought you had in that game and
    is marked manual. Lock status from earlier runs is discarded so unlocking
    takes effect; kickoff locks are re-derived from the board on every run.
    """
    out = [Pick(p.game_id, p.side, p.team, p.edge) for p in current if p.game_id not in locks]
    for gid, lk in locks.items():
        out.append(Pick(gid, lk["side"], lk["team"], 0.0, locked=True, manual=True))
    return out


def recommend(board: pd.DataFrame, current: list[Pick] | None,
              n: int = config.N_PICKS,
              swap_margin: float = config.SWAP_MARGIN,
              flip_margin: float = config.FLIP_MARGIN) -> list[Pick]:
    """Update the pick set with locks, slots and hysteresis.

    * Pinned picks keep their game and side: anything already marked locked
      (a user lock) plus any current pick whose game has kicked off. Each one
      fills a slot, so only ``n - pinned`` slots stay open.
    * An open pick flips side if its edge has gone below -flip_margin.
    * If more open picks exist than open slots, the weakest are dropped.
    * Empty slots fill with the best unpicked games that have not kicked off.
    * A non-picked game displaces the weakest open pick only if its edge beats
      that pick's edge by at least swap_margin.
    More than ``n`` pinned picks are all kept; the caller should warn.
    """
    rows = {r.game_id: r for r in board.itertuples()}
    pinned: list[Pick] = []
    open_picks: list[Pick] = []
    seen: set[str] = set()

    for p in current or []:
        r = rows.get(p.game_id)
        if r is None or p.game_id in seen:
            continue
        seen.add(p.game_id)
        if p.locked or r.locked:
            pinned.append(Pick(p.game_id, p.side, _team(r, p.side), _edge_for(r, p.side),
                               locked=True, manual=p.manual))
            continue
        side = p.side
        e = _edge_for(r, side)
        if e < -flip_margin:
            side = AWAY if side == HOME else HOME
            e = _edge_for(r, side)
        open_picks.append(Pick(p.game_id, side, _team(r, side), e))

    slots = max(0, n - len(pinned))
    open_picks.sort(key=lambda p: (-p.edge, -rows[p.game_id].kickoff_utc.timestamp()))
    open_picks = open_picks[:slots]

    taken = {p.game_id for p in pinned} | {p.game_id for p in open_picks}
    candidates = [r for r in board.itertuples()
                  if r.game_id not in taken and not r.locked and pd.notna(r.edge)]
    candidates.sort(key=lambda r: (-r.edge, -r.kickoff_utc.timestamp()))

    while len(open_picks) < slots and candidates:
        r = candidates.pop(0)
        open_picks.append(Pick(r.game_id, r.best_side, r.best_team, float(r.edge)))

    while candidates and open_picks:
        weakest = min(open_picks, key=lambda p: p.edge)
        cand = candidates[0]
        if cand.edge < weakest.edge + swap_margin:
            break
        open_picks.remove(weakest)
        candidates.pop(0)
        open_picks.append(Pick(cand.game_id, cand.best_side, cand.best_team, float(cand.edge)))

    picks = pinned + open_picks
    picks.sort(key=lambda p: -p.edge)
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
