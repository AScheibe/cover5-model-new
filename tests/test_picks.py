from datetime import datetime, timedelta, timezone

import pandas as pd

from cover5.picks import build_board, recommend, diff_picks, Pick
from cover5.providers import MarketLine
from cover5.scoring import HOME, AWAY

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SOON = NOW + timedelta(days=1)
PAST = NOW - timedelta(hours=3)


def league_df(rows):
    return pd.DataFrame([{"game_id": gid, "away": a, "home": h, "kickoff_utc": k, "home_spread": hs}
                         for gid, a, h, k, hs in rows])


def market(rows):
    return [MarketLine(a, h, k, hs, 3, "test", NOW) for a, h, k, hs in rows]


def test_board_edges_and_sides():
    lg = league_df([("g1", "DEN", "KC", SOON, -3.0), ("g2", "SF", "LA", SOON, 2.5), ("g3", "DAL", "WAS", SOON, 0.0)])
    mk = market([("DEN", "KC", SOON, -6.0), ("SF", "LA", SOON, 1.0), ("DAL", "WAS", SOON, 0.0)])
    b = build_board(lg, mk, now=NOW).set_index("game_id")
    assert b.loc["g1", "best_team"] == "KC" and b.loc["g1", "edge"] == 3.0
    assert b.loc["g2", "best_team"] == "LA" and b.loc["g2", "edge"] == 1.5   # LA +2.5 league, +1 market
    assert b.loc["g3", "edge"] == 0.0


def _slate():
    rows, mrows = [], []
    moves = [3.0, 2.0, 1.5, 1.0, 0.5, 0.0, -0.2]   # league - market; last game favours away by 0.2
    for i, mv in enumerate(moves):
        gid = f"g{i}"
        rows.append((gid, f"A{i}", f"H{i}", SOON, -3.0))
        mrows.append((f"A{i}", f"H{i}", SOON, -3.0 - mv))
    return league_df(rows), market(mrows)


def test_first_run_fills_five_best():
    lg, mk = _slate()
    picks = recommend(build_board(lg, mk, now=NOW), current=None, n=5)
    assert [p.game_id for p in picks] == ["g0", "g1", "g2", "g3", "g4"]
    assert all(p.side == HOME for p in picks)


def test_swap_requires_margin():
    lg, mk = _slate()
    board = build_board(lg, mk, now=NOW)
    current = recommend(board, None, n=5)
    # g5 improves to edge 0.9: beats g4 (0.5) by 0.4 < swap margin 0.5 -> no swap
    mk2 = [m if m.home != "H5" else MarketLine(m.away, m.home, m.kickoff_utc, -3.9, 3, "t", NOW) for m in mk]
    picks = recommend(build_board(lg, mk2, now=NOW), current, n=5, swap_margin=0.5)
    assert not diff_picks(current, picks)["changed"]
    # g5 to edge 1.2 -> swap in, drop g4
    mk3 = [m if m.home != "H5" else MarketLine(m.away, m.home, m.kickoff_utc, -4.2, 3, "t", NOW) for m in mk]
    picks = recommend(build_board(lg, mk3, now=NOW), current, n=5, swap_margin=0.5)
    d = diff_picks(current, picks)
    assert [p.game_id for p in d["added"]] == ["g5"] and [p.game_id for p in d["dropped"]] == ["g4"]


def test_flip_side_when_line_reverses():
    lg, mk = _slate()
    board = build_board(lg, mk, now=NOW)
    current = recommend(board, None, n=5)
    # g0 market swings from H0 -6 to H0 -1: home edge becomes -2 -> flip to away (edge +2)
    mk2 = [m if m.home != "H0" else MarketLine(m.away, m.home, m.kickoff_utc, -1.0, 3, "t", NOW) for m in mk]
    picks = recommend(build_board(lg, mk2, now=NOW), current, n=5)
    g0 = next(p for p in picks if p.game_id == "g0")
    assert g0.side == AWAY and g0.team == "A0" and g0.edge == 2.0
    assert [p.game_id for p in diff_picks(current, picks)["flipped"]] == ["g0"]


def test_locked_games_are_frozen_and_never_added():
    lg, mk = _slate()
    lg.loc[lg.game_id == "g0", "kickoff_utc"] = PAST       # picked, already kicked off
    lg.loc[lg.game_id == "g5", "kickoff_utc"] = PAST       # not picked, kicked off
    current = [Pick("g0", HOME, "H0", 3.0), Pick("g1", HOME, "H1", 2.0), Pick("g2", HOME, "H2", 1.5),
               Pick("g3", HOME, "H3", 1.0), Pick("g4", HOME, "H4", 0.5)]
    # g0 market reverses hard and g5 becomes huge; neither may change
    mk2 = []
    for m in mk:
        if m.home == "H0":
            m = MarketLine(m.away, m.home, PAST, 4.0, 3, "t", NOW)
        if m.home == "H5":
            m = MarketLine(m.away, m.home, PAST, -10.0, 3, "t", NOW)
        mk2.append(m)
    picks = recommend(build_board(lg, mk2, now=NOW), current, n=5)
    ids = {p.game_id: p for p in picks}
    assert ids["g0"].locked and ids["g0"].side == HOME
    assert "g5" not in ids
    assert not diff_picks(current, picks)["changed"]


def test_missing_market_line_is_neutral():
    lg = league_df([("g1", "DEN", "KC", SOON, -3.0), ("g2", "SF", "LA", SOON, 2.5)])
    mk = market([("DEN", "KC", SOON, -5.0)])
    picks = recommend(build_board(lg, mk, now=NOW), None, n=5)
    assert [p.game_id for p in picks] == ["g1"]       # g2 has no market line -> not pickable
