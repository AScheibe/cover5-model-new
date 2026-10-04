"""Overrides: locks, line/score/points overrides, and how the model plans around them."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from cover5 import cli, config
from cover5 import overrides as ovr
from cover5 import state as st
from cover5.picks import Pick, apply_locks, build_board, lock_time_market, recommend
from cover5.providers import MarketLine
from cover5.scoring import AWAY, HOME
from cover5.tally import FINAL, LIVE, OPEN, summarize, tally_picks

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SOON = NOW + timedelta(days=1)
PAST = NOW - timedelta(hours=3)


# --------------------------------------------------------------------------- unit level
def _league(moves, kicks=None):
    """Games g0..gN, league home_spread -3 each; market moved by `moves` toward home."""
    kicks = kicks or [SOON] * len(moves)
    lg = pd.DataFrame([{"game_id": f"g{i}", "away": f"A{i}", "home": f"H{i}", "kickoff_utc": k,
                        "home_spread": -3.0} for i, k in enumerate(kicks)])
    mk = [MarketLine(f"A{i}", f"H{i}", kicks[i], -3.0 - mv, 3, "t", NOW) for i, mv in enumerate(moves)]
    return lg, mk


MOVES = [3.0, 2.0, 1.5, 1.0, 0.5, 0.0, -0.2]


def test_manual_lock_takes_a_slot_and_drops_weakest_open_pick():
    lg, mk = _league(MOVES)
    board = build_board(lg, mk, now=NOW)
    current = recommend(board, None, n=5)                       # g0..g4
    locked = apply_locks(current, {"g6": {"team": "A6", "side": AWAY}})
    picks = recommend(board, locked, n=5)
    ids = {p.game_id: p for p in picks}
    assert set(ids) == {"g0", "g1", "g2", "g3", "g6"}            # g4 (weakest) dropped
    assert ids["g6"].locked and ids["g6"].manual and ids["g6"].team == "A6"
    assert ids["g6"].edge == pytest.approx(0.2)


def test_manual_lock_is_never_flipped_or_swapped():
    lg, mk = _league(MOVES)
    board = build_board(lg, mk, now=NOW)
    # lock the WRONG side of the biggest mover: edge -3, would normally flip
    current = apply_locks(recommend(board, None, n=5), {"g0": {"team": "A0", "side": AWAY}})
    picks = recommend(board, current, n=5)
    g0 = next(p for p in picks if p.game_id == "g0")
    assert g0.side == AWAY and g0.edge == -3.0 and g0.locked
    assert len(picks) == 5


def test_lock_replaces_the_other_side_in_the_same_game():
    current = [Pick("g1", HOME, "H1", 2.0)]
    out = apply_locks(current, {"g1": {"team": "A1", "side": AWAY}})
    assert [(p.game_id, p.side, p.manual) for p in out] == [("g1", AWAY, True)]


def test_unlock_clears_stale_lock_flags_from_disk():
    current = [Pick("g1", HOME, "H1", 2.0, locked=True, manual=True)]
    out = apply_locks(current, {})
    assert not out[0].locked and not out[0].manual


def test_more_locks_than_slots_are_all_kept():
    lg, mk = _league(MOVES)
    board = build_board(lg, mk, now=NOW)
    locks = {f"g{i}": {"team": f"H{i}", "side": HOME} for i in range(6)}
    picks = recommend(board, apply_locks([], locks), n=5)
    assert len(picks) == 6 and all(p.locked for p in picks)


def test_kicked_off_pick_is_graded_at_its_last_pre_kickoff_line():
    lg, mk = _league([1.0, 0.0], kicks=[PAST, SOON])
    snaps = pd.DataFrame([
        {"fetched_at": (PAST - timedelta(hours=30)).isoformat(), "away": "A0", "home": "H0",
         "kickoff_utc": PAST.isoformat(), "home_spread": -3.5, "books": 3, "source": "t"},
        {"fetched_at": (PAST - timedelta(minutes=20)).isoformat(), "away": "A0", "home": "H0",
         "kickoff_utc": PAST.isoformat(), "home_spread": -5.0, "books": 3, "source": "t"},
        {"fetched_at": (PAST + timedelta(hours=1)).isoformat(), "away": "A0", "home": "H0",
         "kickoff_utc": PAST.isoformat(), "home_spread": +7.0, "books": 3, "source": "t"},  # in-game
    ])
    live_feed = [MarketLine("A0", "H0", PAST, 7.0, 3, "t", NOW)] + mk[1:]
    market = lock_time_market(live_feed, snaps, lg, now=NOW)
    g0 = next(m for m in market if m.home == "H0")
    assert g0.home_spread == -5.0 and g0.source.endswith("@lock")
    board = build_board(lg, market, now=NOW).set_index("game_id")
    assert board.loc["g0", "edge"] == 2.0 and board.loc["g0", "locked"]


def test_tally_precedence_and_summary():
    lg = pd.DataFrame([{"game_id": g, "away": f"A{g}", "home": f"H{g}", "kickoff_utc": PAST,
                        "home_spread": -3.0} for g in ("a", "b", "c", "d", "e")])
    picks = [Pick(g, HOME, f"H{g}", 1.5) for g in ("a", "b", "c", "d", "e")]
    results = pd.DataFrame({"result": [10.0, 10.0, 10.0, float("nan"), float("nan")]},
                           index=["a", "b", "c", "d", "e"])
    ov = {"lines": {}, "locks": {},
          "points": {"a": {"team": "Ha", "value": 13.5, "final": True}},          # beats everything
          "results": {"a": {"home_score": 0, "away_score": 50, "final": True},
                      "b": {"home_score": 20, "away_score": 19, "final": True},    # beats nflverse
                      "e": {"home_score": 14, "away_score": 7, "final": False}}}   # live
    t = {x.pick.game_id: x for x in tally_picks(picks, lg, ov, results)}
    assert (t["a"].status, t["a"].points, t["a"].source) == (FINAL, 13.5, "points override")
    assert (t["b"].status, t["b"].points) == (FINAL, -2.0)       # won by 1 at -3
    assert (t["c"].status, t["c"].points, t["c"].source) == (FINAL, 7.0, "nflverse")
    assert (t["d"].status, t["d"].points) == (OPEN, 1.5)         # expected = edge
    assert (t["e"].status, t["e"].points) == (LIVE, 4.0)
    s = summarize(list(t.values()))
    assert s["final"] == 18.5 and s["live"] == 4.0 and s["expected"] == 1.5 and s["projected"] == 24.0


def test_line_override_feeds_the_board():
    lg, mk = _league([0.0])
    ov = {"lines": {"g0": -6.0}}
    board = build_board(ovr.apply_line_overrides(lg, ov), mk, now=NOW).set_index("game_id")
    # league now H0 -6 vs market H0 -3: the away team is getting 3 extra points
    assert board.loc["g0", "best_team"] == "A0" and board.loc["g0", "edge"] == 3.0
    assert board.loc["g0", "line_overridden"]


# --------------------------------------------------------------------------- CLI scenario
TEAMS = [("PIT", "CLE"), ("IND", "WAS"), ("TEN", "BAL"), ("NE", "BUF"), ("NYJ", "CHI"),
         ("JAX", "CIN"), ("DAL", "HOU"), ("ARI", "NYG")]


def _write_games(root, now):
    """A synthetic week 4: the first game kicked off 3 hours ago, the rest tomorrow."""
    rows = []
    for i, (a, h) in enumerate(TEAMS):
        k = (now - timedelta(hours=3)) if i == 0 else (now + timedelta(days=1, minutes=i))
        k = k.astimezone(ET)
        rows.append({"game_id": f"2026_04_{a}_{h}", "season": 2026, "game_type": "REG", "week": 4,
                     "gameday": k.strftime("%Y-%m-%d"), "gametime": k.strftime("%H:%M"),
                     "away_team": a, "home_team": h, "spread_line": 3.0, "result": float("nan"),
                     "home_score": float("nan"), "away_score": float("nan")})
    pd.DataFrame(rows).to_csv(config.CACHE_DIR / "games.csv", index=False)


def _market(now, moves):
    """League lines are all home -3 (seeded from spread_line 3). `moves` maps home team to market spread."""
    out = []
    for i, (a, h) in enumerate(TEAMS):
        k = (now - timedelta(hours=3)) if i == 0 else (now + timedelta(days=1, minutes=i))
        k = k.replace(second=0, microsecond=0)
        out.append(MarketLine(a, h, k, moves.get(h, -3.0), 3, "test", now - timedelta(hours=5)))
    return out


@pytest.fixture
def week(tmp_root, monkeypatch, capsys):
    now = datetime.now(UTC)
    _write_games(tmp_root, now)
    run = lambda *a: cli.main(["--season", "2026", "--week", "4", *a])
    assert run("init-week", "--no-market") == 0
    # market: CLE -6 (edge 3 home), WAS -5 (2), BAL -4.5 (1.5), BUF -4 (1), CHI -3.5 (.5), others flat
    m = _market(now, {"CLE": -6.0, "WAS": -5.0, "BAL": -4.5, "BUF": -4.0, "CHI": -3.5})
    monkeypatch.setattr(cli, "fetch_market", lambda provider=None: m)
    capsys.readouterr()
    return run


def _state():
    return st.load_state(2026, 4)


def _ov():
    return ovr.load_overrides(2026, 4)


def test_scenario_first_update_skips_kicked_off_game(week, capsys):
    # CLE's game kicked off before any snapshot existed and was never a pick: not addable.
    assert week("update") == 0
    teams = [p["team"] for p in _state()["recommended"]]
    assert "CLE" not in teams and teams[:4] == ["WAS", "BAL", "BUF", "CHI"]


def test_scenario_picks_lock_and_points(week, capsys):
    week("update")
    # You actually entered CLE on Thursday (it has kicked off) plus four others.
    assert week("picks", "CLE", "WAS", "BAL", "DAL", "JAX") == 0
    out = capsys.readouterr().out
    rec = {p["team"]: p for p in _state()["recommended"]}
    assert rec["CLE"]["locked"] and not rec["CLE"]["manual"]   # kickoff lock
    # The model keeps CLE (locked) and swaps the two flat games for BUF and CHI
    assert set(rec) == {"CLE", "WAS", "BAL", "BUF", "CHI"}
    assert "DO THIS" in out and "drop  DAL" in out and "drop  JAX" in out

    # The app shows CLE's pick at +8 so far: override the locked-in score.
    assert week("set-points", "CLE", "8", "--live") == 0
    out = capsys.readouterr().out
    assert "[LIVE +8 (override)]" in out
    assert "+8.0 live (1)" in out
    # 4 open picks with edges 2 + 1.5 + 1 + .5 = 5 expected
    assert "+5.0 expected from line movement (4 open)" in out and "Projected +13.0" in out

    # Lock JAX before kickoff: the model must keep it and drop the weakest open pick (CHI).
    assert week("lock", "JAX") == 0
    out = capsys.readouterr().out
    rec = {p["team"]: p for p in _state()["recommended"]}
    assert set(rec) == {"CLE", "WAS", "BAL", "BUF", "JAX"} and rec["JAX"]["manual"]
    assert "drop  CHI" in out and "LOCKED by you" in out

    # Final score comes in: set-points again as final; the projection uses it.
    week("set-points", "CLE", "13.5")
    out = capsys.readouterr().out
    assert "+13.5 final (1 pick)" in out

    # Overrides persist through a re-seed of the league file.
    week("init-week", "--no-market", "--overwrite")
    assert _ov()["points"]["2026_04_PIT_CLE"]["value"] == 13.5
    assert _ov()["locks"]["2026_04_JAX_CIN"]["team"] == "JAX"


def test_scenario_set_line_changes_recommendation(week, capsys):
    week("update")
    capsys.readouterr()
    # Fifth slot went to NYG on a zero-edge tie (latest kickoff wins ties).
    assert [p["team"] for p in _state()["recommended"]][-1] == "NYG"
    # The app's real line was NYG -7 (sheet seeded NYG -3), market NYG -3: ARI +7 is worth 4,
    # so the model flips that pick rather than adding a game.
    assert week("set-line", "NYG", "-7") == 0
    out = capsys.readouterr().out
    assert "flip to ARI +7* @ NYG (market +3, edge +4.0)" in out
    assert _ov()["lines"]["2026_04_ARI_NYG"] == -7.0
    # A second override pushes HOU's league line to HOU +2 (market HOU -3): HOU worth 5,
    # which beats the weakest pick (CHI, 0.5) by more than the swap margin.
    week("set-line", "HOU", "2")
    out = capsys.readouterr().out
    assert "drop  CHI" in out and "add   HOU +2* vs DAL" in out
    # status is read-only: it must not change state
    before = json.dumps(_state(), sort_keys=True)
    week("status")
    assert json.dumps(_state(), sort_keys=True) == before
    assert "line    ARI@NYG: NYG -7" in capsys.readouterr().out


def test_scenario_set_score_grades_with_overridden_line(week, capsys):
    week("update")
    week("picks", "CLE", "WAS", "BAL", "BUF", "CHI")
    week("set-line", "CLE", "-2.5")
    week("set-score", "CLE", "27", "24")              # CLE won by 3 at -2.5
    capsys.readouterr()
    week("score-week")
    out = capsys.readouterr().out
    assert "CLE -2.5 vs PIT" in out and "+0.5  [score override]" in out


def test_scenario_unlock_and_clear(week, capsys):
    week("update")
    week("lock", "DAL")
    assert "2026_04_DAL_HOU" in _ov()["locks"]
    week("unlock", "DAL")
    assert "2026_04_DAL_HOU" not in _ov()["locks"]
    week("set-line", "HOU", "-1")
    week("set-points", "HOU", "3")
    week("clear", "HOU", "--kind", "line")
    ov = _ov()
    assert "2026_04_DAL_HOU" not in ov["lines"] and "2026_04_DAL_HOU" in ov["points"]
    week("clear", "HOU")
    ov = _ov()
    assert all("2026_04_DAL_HOU" not in ov[k] for k in ovr.KINDS)


def test_scenario_points_for_wrong_team_is_ignored_with_warning(week, capsys):
    week("update")
    week("set-points", "CHI", "6")                     # auto-locks CHI
    week("unlock", "CHI")
    week("lock", "NYJ")                                # now your pick in that game is NYJ
    capsys.readouterr()
    week("status")
    out = capsys.readouterr().out
    assert "points override was entered for CHI but your pick in that game is NYJ" in out
    assert "[FINAL +6" not in out


def test_scenario_rejects_bad_picks(week):
    with pytest.raises(SystemExit):
        week("picks", "CLE", "PIT")                    # same game
    with pytest.raises(SystemExit):
        week("picks", "CLE", "WAS", "BAL", "BUF", "CHI", "HOU")   # six
    with pytest.raises(SystemExit):
        week("lock", "SEA")                            # not on slate
