"""Hand-checked synthetic weeks for cover5.backtest_movement."""
import numpy as np
import pandas as pd
import pytest

from cover5 import backtest_movement as bm

# (home, away, open, close, home_score, away_score, gametime)
# home_pts = home_margin + open ; edge = open - close ; move side = sign(edge), HOME when 0
WEEK = [
    ("H1", "A1", -3.0, -6.0, 26, 20, "13:00"),   # edge +3 HOME, wins by 6 -> +3
    ("H2", "A2", -3.0, -6.0, 23, 20, "13:00"),   # edge +3 HOME, wins by the open spread -> 0
    ("H3", "A3", 2.0, 4.0, 10, 20, "16:25"),     # edge -2 AWAY, home -10: home_pts -8 -> +8
    ("H4", "A4", -7.0, -6.0, 24, 10, "16:25"),   # edge -1 AWAY, home +14: home_pts +7 -> -7
    ("H5", "A5", -1.0, -1.0, 10, 12, "13:00"),   # no move, home_pts -3
    ("BBB", "A6", -10.0, -10.0, 30, 10, "20:20"),  # no move, home_pts +10
    ("AAA", "A7", 2.5, 2.5, 20, 21, "20:20"),    # no move, home_pts +1.5
    ("H8", "A8", -4.0, -4.0, 24, 20, "13:00"),   # no move, home_pts 0
]


def make_df(rows, season=2020, week=1, gameday="2020-09-13"):
    out = []
    for home, away, o, c, hs, as_, t in rows:
        out.append(dict(season=season, week=week, gameday=gameday, gametime=t, away=away, home=home,
                        open_home_spread=o, close_home_spread=c, home_score=hs, away_score=as_,
                        game_id=f"{season}_{week:02d}_{away}_{home}"))
    return pd.DataFrame(out)


def weeks_of(df):
    return [w for _, w in bm.prepare(df).groupby(["season", "week"])]


def test_single_game_moved_three_toward_home():
    d = bm.prepare(make_df(WEEK))
    g1 = d.set_index("home").loc["H1"]
    assert g1.edge == 3.0 and g1.move_pts == 3.0      # wins by the close: covers open by 3
    assert d.set_index("home").loc["H2"].move_pts == 0.0  # wins by exactly the open spread


def test_movement_top5_with_tiebreaks():
    # moved games H1,H2,H3,H4 = 3+0+8-7 = 4; 5th slot among zero moves goes to the
    # latest kickoff (20:20: AAA, BBB), then alphabetical home -> AAA (+1.5)
    w = weeks_of(make_df(WEEK))
    assert bm.strat_movement_top5(w)[0, 0] == pytest.approx(5.5)


def test_min1_fills_randomly_with_zero_expectation():
    w = weeks_of(make_df(WEEK))
    m = bm.strat_movement_min1(w, reps=20000, rng=np.random.default_rng(0))
    assert m.min() == pytest.approx(4 - 10) and m.max() == pytest.approx(4 + 10)
    assert m.mean() == pytest.approx(4.0, abs=0.15)


def test_favorites_and_underdogs():
    # biggest |open|: BBB 10, H4 7, H8 4, H1 3, H2 3 -> 10+7+0+3+0
    w = weeks_of(make_df(WEEK))
    assert bm.strat_favorites(w)[0, 0] == pytest.approx(20.0)
    assert bm.strat_favorites(w, pick_dog=True)[0, 0] == pytest.approx(-20.0)


def test_random_is_zero_mean():
    w = weeks_of(make_df(WEEK))
    m = bm.strat_random(w, reps=40000, rng=np.random.default_rng(0))
    assert abs(m.mean()) < 0.15


def test_sequential_has_no_lookahead():
    # Thursday game moved 1 point; Sunday has five games that moved 2.
    rows = [("TH", "TA", -3.0, -4.0, 20, 10, "20:15")]
    rows += [(f"S{i}", f"X{i}", -3.0, -5.0, 20, 10, "13:00") for i in range(5)]
    rows += [(f"Z{i}", f"Y{i}", -3.0, -3.0, 20, 10, "16:25") for i in range(2)]
    df = make_df(rows)
    df.loc[0, "gameday"] = "2020-09-10"
    (w,) = weeks_of(df)
    top = set(bm._ranked(w, "abs_edge").head(5).home)
    seq = set(bm.sequential_picks(w, 1.0).home)
    assert top == {f"S{i}" for i in range(5)}
    assert "TH" in seq and len(seq) == 5 and len(seq & top) == 4


def test_oracle_buckets_and_run(tmp_path):
    df = pd.concat([make_df(WEEK, week=1), make_df(WEEK, week=2, gameday="2020-09-20")])
    res = bm.run(df=df, reps=500, trials=2000, out_path=tmp_path / "r.json")
    b = {x["bucket"]: x for x in res["oracle_check"]["buckets"]}
    assert b["2.5+"]["n"] == 4 and b["2.5+"]["mean_move_side_pts"] == pytest.approx(1.5)
    assert b["2"]["mean_move_side_pts"] == pytest.approx(8.0)
    assert b["1"]["mean_move_side_pts"] == pytest.approx(-7.0)
    assert res["strategies"]["MOVEMENT-TOP5"]["mean_week"] == pytest.approx(5.5)
    assert res["strategies"]["MOVEMENT-TOP5"]["mean_season"] == pytest.approx(5.5 * 18)
    assert res["weeks_used"] == 2 and (tmp_path / "r.json").exists()


def test_thin_weeks_are_dropped():
    assert bm.prepare(make_df(WEEK[:7])).empty
