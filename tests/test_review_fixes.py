"""Regression tests for the confirmed findings of the overrides review."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from cover5 import overrides as ovr
from cover5 import service
from cover5 import state as st
from cover5.picks import Pick, apply_locks, build_board, lock_time_market, recommend
from cover5.providers import MarketLine
from cover5.scoring import AWAY, HOME
from cover5.tally import tally_picks

from tests.test_overrides import TEAMS, _market, week  # noqa: F401  (fixture)

UTC = timezone.utc


def _picks():
    return [p["team"] for p in st.load_state(2026, 4)["recommended"]]


def _ov():
    return ovr.load_overrides(2026, 4)


def _feed(monkeypatch, moves, drop=()):
    now = datetime.now(UTC)
    m = [x for x in _market(now, moves) if x.home not in drop]
    monkeypatch.setattr(service, "fetch_market", lambda provider=None: m)


BASE = {"CLE": -6.0, "WAS": -5.0, "BAL": -4.5, "BUF": -4.0, "CHI": -3.5}


# 0 -------------------------------------------------------------------------
def test_dropped_game_keeps_last_line_and_does_not_flip_flop(week, monkeypatch, capsys):
    week("update")
    assert "WAS" in _picks()
    # the feed loses IND@WAS for a run, while HOU and CIN move 3.6 points
    _feed(monkeypatch, {**BASE, "HOU": -6.6, "CIN": -6.6}, drop=("WAS",))
    week("update")
    after_update = set(_picks())
    assert "WAS" in after_update                       # valued at its last line (+2.0), not 0
    week("lock", "BAL")                               # offline recompute must agree with update
    assert set(_picks()) == after_update
    week("update")
    assert set(_picks()) == after_update


def test_open_pick_with_no_line_keeps_previous_edge():
    lg = pd.DataFrame([{"game_id": "g0", "away": "A", "home": "H", "kickoff_utc": datetime.now(UTC) + timedelta(days=1),
                        "home_spread": -3.0}])
    board = build_board(lg, [])
    picks = recommend(board, [Pick("g0", HOME, "H", 2.0)], n=5)
    assert picks[0].edge == 2.0


# 1 -------------------------------------------------------------------------
def test_picks_clears_stale_locks(week, capsys):
    week("update")
    week("lock", "DAL")
    capsys.readouterr()
    week("picks", "WAS", "BAL", "BUF", "CHI", "NYG")
    out = capsys.readouterr().out
    assert set(_picks()) == {"WAS", "BAL", "BUF", "CHI", "NYG"}
    assert "2026_04_DAL_HOU" not in _ov()["locks"]
    assert "Removed your lock on DAL" in out and "drop" not in out


# 2 + 6 ---------------------------------------------------------------------
def test_points_for_a_non_pick_with_full_set_is_refused(week):
    week("update")
    before = list(_picks())
    with pytest.raises(SystemExit, match="isn't one of your 5 picks"):
        week("set-points", "CLE", "8")                # CLE kicked off, never a pick
    assert _picks() == before and not _ov()["points"] and not _ov()["locks"]


def test_points_fill_an_open_slot_and_clear_removes_them(week, capsys):
    week("update")
    week("picks", "WAS", "BAL", "BUF", "CHI", "--no-recompute")   # one open slot, not yet refilled
    week("set-points", "CLE", "8", "--live")
    assert "CLE" in _picks() and len(_picks()) == 5
    v = service.week_view(2026, 4)
    assert v["summary"]["n_live"] == 1 and len(v["picks"]) == 5
    week("clear", "CLE", "--kind", "points")          # also removes the auto lock
    assert "2026_04_PIT_CLE" not in _ov()["locks"]
    assert "CLE" not in _picks()


# 3 -------------------------------------------------------------------------
def test_live_override_gives_way_to_final_result():
    lg = pd.DataFrame([{"game_id": g, "away": f"A{g}", "home": f"H{g}", "kickoff_utc": datetime.now(UTC),
                        "home_spread": -3.0} for g in ("a", "b", "c")])
    picks = [Pick(g, HOME, f"H{g}", 1.0) for g in ("a", "b", "c")]
    results = pd.DataFrame({"result": [10.0, 10.0, 10.0]}, index=["a", "b", "c"])
    ov = {"points": {"a": {"team": "Ha", "value": 3.0, "final": False},
                     "c": {"team": "Hc", "value": 99.0, "final": True}},
          "results": {"b": {"home_score": 7, "away_score": 7, "final": False}}}
    t = {x.pick.game_id: x for x in tally_picks(picks, lg, ov, results)}
    assert (t["a"].status, t["a"].points) == ("final", 7.0) and "superseded" in t["a"].source
    assert (t["b"].status, t["b"].points) == ("final", 7.0) and "superseded" in t["b"].source
    assert (t["c"].points, t["c"].source) == (99.0, "points override")      # final override still wins


# 4 -------------------------------------------------------------------------
def test_week_flag_after_subcommand_and_future_game_rejected(week, capsys):
    from cover5 import cli
    week("update")
    # --week after the subcommand targets that week (the phone workflow appends it this way)
    assert cli.main(["--season", "2026", "status", "--week", "4"]) == 0
    with pytest.raises(SystemExit, match="hasn't kicked off yet"):
        week("set-points", "WAS", "5")
    with pytest.raises(SystemExit, match="hasn't kicked off yet"):
        week("set-score", "WAS", "20", "10")
    assert not _ov()["points"] and not _ov()["results"]


# 5 -------------------------------------------------------------------------
def test_clearing_points_puts_back_the_pick_it_replaced(week, capsys):
    week("update")
    week("picks", "CLE", "WAS", "BAL", "BUF", "CHI")
    week("set-points", "PIT", "-4")                   # other side of a kicked-off pick
    assert "PIT" in _picks() and "CLE" not in _picks()
    capsys.readouterr()
    week("clear", "PIT")
    out = capsys.readouterr().out
    assert "Put CLE back" in out
    assert "CLE" in _picks() and "PIT" not in _picks() and len(_picks()) == 5


def test_clearing_your_own_lock_keeps_the_team(week):
    week("update")
    week("lock", "BAL")
    week("clear", "BAL", "--kind", "lock")
    assert "BAL" in _picks()


# 7 -------------------------------------------------------------------------
def test_in_game_line_never_used_as_lock_line():
    ko = datetime.now(UTC) - timedelta(hours=2)
    lg = pd.DataFrame([{"game_id": "g0", "away": "A", "home": "H", "kickoff_utc": ko, "home_spread": -3.0}])
    feed = [MarketLine("A", "H", ko, 10.5, 3, "t", datetime.now(UTC))]   # fetched after kickoff
    snaps = pd.DataFrame([{"fetched_at": (ko + timedelta(hours=1)).isoformat(), "away": "A", "home": "H",
                           "kickoff_utc": ko.isoformat(), "home_spread": 10.5, "books": 3, "source": "t"}])
    m = lock_time_market(feed, snaps, lg)
    assert pd.isna(m[0].home_spread)
    assert pd.isna(build_board(lg, m).loc[0, "edge"])


# 8 -------------------------------------------------------------------------
def test_swap_drops_the_earlier_kickoff_on_tied_edges():
    now = datetime.now(UTC)
    kicks = [now + timedelta(days=1, hours=h) for h in (0, 1, 2, 3, 8, 9)]
    lg = pd.DataFrame([{"game_id": f"g{i}", "away": f"A{i}", "home": f"H{i}", "kickoff_utc": k,
                        "home_spread": -3.0} for i, k in enumerate(kicks)])
    moves = [3.0, 2.0, 1.5, 1.0, 1.0, 2.0]             # g3 (early) and g4 (late) tie at 1.0; g5 is new
    mk = [MarketLine(f"A{i}", f"H{i}", kicks[i], -3.0 - mv, 3, "t", now) for i, mv in enumerate(moves)]
    current = [Pick(f"g{i}", HOME, f"H{i}", 0.0) for i in range(5)]
    picks = recommend(build_board(lg, mk), current, n=5, swap_margin=0.5)
    ids = {p.game_id for p in picks}
    assert "g5" in ids and "g3" not in ids and "g4" in ids


# 9 -------------------------------------------------------------------------
def test_lock_both_teams_of_a_game_rejected(week):
    week("update")
    with pytest.raises(SystemExit, match="pick one side per game"):
        week("lock", "IND", "WAS")
    assert not _ov()["locks"]


def test_lock_kicked_off_non_pick_with_full_set_rejected(week):
    week("update")
    with pytest.raises(SystemExit, match="kicked off"):
        week("lock", "CLE")


# 10 ------------------------------------------------------------------------
def test_apply_locks_keeps_the_edge_of_the_same_pick():
    out = apply_locks([Pick("g1", HOME, "H1", 1.5)], {"g1": {"team": "H1", "side": HOME}})
    assert out[0].edge == 1.5 and out[0].manual
    out = apply_locks([Pick("g1", HOME, "H1", 1.5)], {"g1": {"team": "A1", "side": AWAY}})
    assert out[0].edge == 0.0


# 11 ------------------------------------------------------------------------
def test_score_without_league_line_warns(week, capsys):
    week("update")
    import cover5.league as league
    p = league.league_path(2026, 4)
    df = pd.read_csv(p)
    df.loc[df.home == "CLE", "home_spread"] = float("nan")
    df.to_csv(p, index=False)
    week("picks", "CLE", "WAS", "BAL", "BUF", "CHI")
    capsys.readouterr()
    week("set-score", "CLE", "27", "24")
    out = capsys.readouterr().out
    assert "no league line" in out
    res = service.score_week(2026, 4, refresh=False)
    assert any("no league line" in w for w in res["warnings"])
    assert next(p for p in res["picks"] if p["label"].startswith("CLE"))["source"] == "no league line"


# 12 ------------------------------------------------------------------------
@pytest.mark.parametrize("args", [("set-points", "CLE", "nan"), ("set-points", "CLE", "inf"),
                                  ("set-line", "TEN", "inf"), ("set-line", "TEN", "nan"),
                                  ("set-score", "CLE", "-3", "5"), ("set-score", "CLE", "nan", "5")])
def test_non_finite_and_negative_inputs_rejected(week, args):
    week("update")
    week("picks", "CLE", "WAS", "BAL", "BUF", "CHI")
    with pytest.raises(SystemExit):
        week(*args)
    ov = _ov()
    assert not ov["points"] and not ov["results"] and not ov["lines"]


# 13 ------------------------------------------------------------------------
def test_workflow_passes_extra_args_for_update_and_init_week():
    import yaml
    from pathlib import Path
    wf = yaml.safe_load((Path(__file__).parent.parent / ".github/workflows/update.yml").read_text())
    run = next(s for s in wf["jobs"]["run"]["steps"] if s.get("name") == "Run tracker")["run"]
    init = run.split("init-week)")[1].split(";;")[0]
    upd = run.split("\n  update)")[1].split(";;")[0]
    assert '"${ARGS[@]}"' in init and '"${ARGS[@]}"' in upd
