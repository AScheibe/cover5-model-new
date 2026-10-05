"""Regression tests for the confirmed findings of the web app review (backend side)."""
import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from cover5 import alerts, config, history, providers, service
from cover5 import overrides as ovr
from cover5 import state as st
from cover5.league import league_path
from cover5.server.app import create_app
from cover5.server.scheduler import Scheduler, results_due

from tests.test_api import cfg_guard  # noqa: F401  (fixture)
from tests.test_overrides import ET, TEAMS, _market, week  # noqa: F401  (fixture)

UTC = timezone.utc
BASE = {"CLE": -6.0, "WAS": -5.0, "BAL": -4.5, "BUF": -4.0, "CHI": -3.5}
# A finished week 3. NE@BUF is also on week 4's slate, a week later, to check
# that one week's market lines are never logged for another.
WEEK3 = [("KC", "LV", 27, 20), ("SF", "LA", 17, 24), ("GB", "DET", 21, 21), ("MIA", "NO", 10, 13),
         ("SEA", "TB", 30, 3), ("NE", "BUF", 14, 31)]


def _feed(monkeypatch, moves):
    m = _market(datetime.now(UTC), moves)
    monkeypatch.setattr(service, "fetch_market", lambda provider=None: m)
    return m


def _add_week3(now=None, results=True):
    """Append a finished week 3 (kickoffs about six days ago) to the cached nflverse file."""
    now = now or datetime.now(UTC)
    path = config.CACHE_DIR / "games.csv"
    df = pd.read_csv(path)
    rows = []
    for i, (a, h, ap, hp) in enumerate(WEEK3):
        k = (now - timedelta(days=6) + timedelta(minutes=i)).astimezone(ET)
        rows.append({"game_id": f"2026_03_{a}_{h}", "season": 2026, "game_type": "REG", "week": 3,
                     "gameday": k.strftime("%Y-%m-%d"), "gametime": k.strftime("%H:%M"),
                     "away_team": a, "home_team": h, "spread_line": 3.0,
                     "result": float(hp - ap) if results else float("nan"),
                     "home_score": float(hp) if results else float("nan"),
                     "away_score": float(ap) if results else float("nan")})
    df = pd.concat([df[df.week != 3], pd.DataFrame(rows)], ignore_index=True)
    df.to_csv(path, index=False)
    _touch(path)


def _set_result(game_id, away, home):
    path = config.CACHE_DIR / "games.csv"
    df = pd.read_csv(path)
    m = df.game_id == game_id
    df.loc[m, "home_score"], df.loc[m, "away_score"], df.loc[m, "result"] = home, away, home - away
    df.to_csv(path, index=False)
    _touch(path)


def _touch(path):
    """Make sure the schedule cache sees a new mtime even within the same clock tick."""
    t = time.time() + 1
    import os
    os.utime(path, (t, t))


def _teams(view):
    return [p["team"] for p in view["picks"]]


# --------------------------------------------------------------------------- 1. the to-do list
def test_todo_nets_every_change_since_you_confirmed(week, monkeypatch):
    out = service.update(2026, 4)
    picks = _teams(out.view)
    todo = out.view["todo"]
    # Nothing confirmed yet this week: add every pick (none has kicked off).
    assert todo["confirmed"] is None and sorted(todo["added"]) == sorted(picks) and not todo["dropped"]

    service.confirm_picks(2026, 4)
    assert service.week_view(2026, 4)["todo"]["changed"] is False

    _feed(monkeypatch, {**BASE, "HOU": -6.0})                     # run A: HOU +3 beats NYG (0)
    a = service.update(2026, 4).view
    _feed(monkeypatch, {**BASE, "HOU": -6.0, "CIN": -6.5})        # run B: CIN +3.5 beats CHI (.5)
    b = service.update(2026, 4).view
    assert (b["last_change"]["added"], b["last_change"]["dropped"]) == (["CIN"], ["CHI"])
    todo = b["todo"]
    assert sorted(todo["added"]) == ["CIN", "HOU"] and sorted(todo["dropped"]) == ["CHI", "NYG"]
    assert todo["confirmed"] == picks and todo["since"]
    assert a["todo"]["added"] == ["HOU"]

    # An add that a later run takes back cancels out.
    _feed(monkeypatch, {**BASE, "CIN": -6.5})                    # HOU back to flat: still held (hysteresis)
    service.update(2026, 4)
    service.confirm_picks(2026, 4)
    assert service.week_view(2026, 4)["todo"]["changed"] is False


# --------------------------------------------------------------------------- 3/19. no duplicate pending block
def test_saved_run_reports_nothing_pending_but_logs_its_diff(week, monkeypatch):
    service.update(2026, 4)
    _feed(monkeypatch, {**BASE, "HOU": -6.0})
    out = service.update(2026, 4)
    assert out.alert["changed"] is True
    assert out.view["diff"]["changed"] is False                   # already the presumed picks
    assert out.view["last_change"]["added"] == ["HOU"]
    run = history.list_runs(2026, 4, limit=1)[0]
    assert run["diff"]["added"] == ["HOU"] and run["diff"]["dropped"] == ["NYG"]


# --------------------------------------------------------------------------- 4/16. picks you enter stay yours
def test_entered_pick_the_model_dislikes_is_explained_or_kept_with_a_lock(week):
    service.update(2026, 4)
    # NE is the other side of BUF (market moved a point toward BUF): the model flips it back...
    out = service.set_picks(2026, 4, ["WAS", "BAL", "NE", "CHI", "NYG"])
    assert "BUF" in _teams(out.view) and "NE" not in _teams(out.view)
    assert out.messages[0] == "You entered: WAS, BAL, NE, CHI, NYG."
    assert any("switch NE to BUF" in m and "Lock NE" in m for m in out.messages)
    assert out.view["todo"]["flipped"] == ["BUF"]                 # and Do this says so

    # ...unless you lock it, which is what picking it by hand in the web app does.
    out = service.set_picks(2026, 4, ["WAS", "BAL", "NE", "CHI", "NYG"], lock=["NE"])
    assert sorted(_teams(out.view)) == sorted(["WAS", "BAL", "NE", "CHI", "NYG"])
    assert out.messages[0] == "Your picks are now: WAS, BAL, NE, CHI, NYG"
    ne = next(p for p in out.view["picks"] if p["team"] == "NE")
    assert ne["manual"] and ne["locked"]
    assert out.view["todo"]["changed"] is False
    rec = history.get_week(2026, 4)
    assert "NE" in [p["team"] for p in rec["picks"]]              # the record grades what you hold

    with pytest.raises(service.UserError, match="isn't in the picks you entered"):
        service.set_picks(2026, 4, ["WAS"], lock=["BAL"])


def test_removing_a_pick_says_what_the_model_puts_in_the_slot(week):
    service.update(2026, 4)
    out = service.set_picks(2026, 4, ["WAS", "BAL", "BUF", "CHI"])
    added = [t for t in _teams(out.view) if t not in ("WAS", "BAL", "BUF", "CHI")]
    assert len(added) == 1
    assert any(m.startswith(f"The model suggests {added[0]}") for m in out.messages)
    assert out.view["todo"]["added"] == added


def test_lock_and_points_confirm_what_you_hold(week):
    service.update(2026, 4)
    service.confirm_picks(2026, 4)
    service.lock(2026, 4, ["DAL"])                                # you put DAL in, the model drops the weakest
    todo = service.week_view(2026, 4)["todo"]
    assert "DAL" in todo["confirmed"] and not todo["added"] and len(todo["dropped"]) == 1


# --------------------------------------------------------------------------- 2/11. records pick up late finals
def test_history_record_syncs_with_nflverse_finals_after_the_last_update(week, cfg_guard, tmp_path):
    _add_week3()
    path = config.CACHE_DIR / "games.csv"
    df = pd.read_csv(path)
    df.loc[df.game_id == "2026_03_NE_BUF", ["result", "home_score", "away_score"]] = float("nan")
    df.to_csv(path, index=False)
    _touch(path)
    service.init_week(2026, 3, use_market=False)
    service.set_picks(2026, 3, ["KC", "LA", "DET", "NO", "BUF"])
    service.set_points(2026, 3, "BUF", 9, live=True)               # typed mid-game
    rec = history.get_week(2026, 3)
    assert rec["complete"] is False and rec["n_live"] == 1

    _set_result("2026_03_NE_BUF", 14, 31)                          # nflverse final arrives on Tuesday
    c = TestClient(create_app(frontend_dist=tmp_path / "no-dist"))
    h = c.get("/api/history?season=2026").json()
    w3 = next(w for w in h["weeks"] if w["week"] == 3)
    assert w3["complete"] is True and w3["n_live"] == 0
    buf = next(p for p in w3["picks"] if p["team"] == "BUF")
    assert buf["status"] == "final" and "superseded" in buf["source"]
    assert h["totals"]["complete_weeks"] == 1
    before = history.get_week(2026, 3)["updated_at"]
    c.get("/api/history?season=2026")                              # nothing changed: no rewrite
    assert history.get_week(2026, 3)["updated_at"] == before


def test_scheduler_syncs_records_even_when_nothing_is_due(week, cfg_guard):
    _add_week3(results=False)
    service.init_week(2026, 3, use_market=False)
    service.set_picks(2026, 3, ["KC", "LA", "DET", "NO", "BUF"])
    assert history.get_week(2026, 3)["n_final"] == 0
    _add_week3(results=True)
    sch = Scheduler()
    sch.tick(force=False)                                          # scheduler is off by default in tests
    rec = history.get_week(2026, 3)
    assert rec["complete"] is True and rec["n_final"] == 5


def test_scheduler_redownloads_results_when_finals_are_overdue(week, cfg_guard, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(service, "refresh_results", lambda: calls.append(1) or (True, "ok"))
    now = datetime.now(UTC)
    # PIT@CLE kicked off 3 hours ago; with no result 4 hours after kickoff a download is due.
    g = service.games()
    assert not results_due(g, 2026, [4], now)
    assert results_due(g, 2026, [4], now + timedelta(hours=1))
    future = now + timedelta(hours=1)
    path = config.CACHE_DIR / "games.csv"
    old = time.time() - 3600
    import os
    os.utime(path, (old, old))
    sch = Scheduler()
    sch.maintain_records(future)
    sch.maintain_records(future + timedelta(minutes=5))           # rate limited
    assert len(calls) == 1
    sch.maintain_records(future + timedelta(minutes=31))
    assert len(calls) == 2


# --------------------------------------------------------------------------- 6. forcing a results download
def test_results_endpoint_and_history_refresh_force_a_download(wc_online, monkeypatch):
    c, calls = wc_online
    r = c.post("/api/week/2026/4/results")
    assert r.status_code == 200, r.text
    assert r.json()["messages"] == ["Downloaded the latest nflverse results."]
    assert r.json()["week"]["week"] == 4
    assert c.post("/api/history/refresh", json={}).status_code == 200
    assert len(calls) == 2
    assert c.get("/api/meta").json()["results"]["downloaded_at"]


@pytest.fixture
def wc_online(week, cfg_guard, tmp_path, monkeypatch):
    path = config.CACHE_DIR / "games.csv"
    content = path.read_bytes()
    calls = []

    class Resp:
        def __init__(self):
            self.content = content

        def raise_for_status(self):
            pass

    def fake_get(url, timeout=60):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(config, "OFFLINE", False)
    from cover5 import schedule
    monkeypatch.setattr(schedule.requests, "get", fake_get)
    # a fresh cache: only a forced download fetches
    t = time.time() - 5
    import os
    os.utime(path, (t, t))
    return TestClient(create_app(frontend_dist=tmp_path / "no-dist")), calls


# --------------------------------------------------------------------------- 10. the odds API key never leaks
KEY = "SECRETKEY-abcdef-12345678"


def test_failing_odds_api_never_returns_the_key(week, cfg_guard, tmp_path, monkeypatch):
    c = TestClient(create_app(frontend_dist=tmp_path / "no-dist"))
    monkeypatch.setattr(service, "fetch_market", providers.fetch_market)
    r = c.put("/api/settings", json={"provider": "oddsapi", "odds_api_key": KEY})
    assert r.status_code == 200

    def boom(url, params=None, timeout=None):
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.the-odds-api.com', port=443): Max retries exceeded with url: "
            f"/v4/sports/americanfootball_nfl/odds?apiKey={params['apiKey']}&regions=us")

    monkeypatch.setattr(providers.requests, "get", boom)
    r = c.post("/api/week/2026/4/update", json={})
    assert r.status_code == 502 and KEY not in r.text and "***" in r.text
    r = c.post("/api/week/2026/5/init", json={})
    assert KEY not in r.text
    r = c.post("/api/scheduler/run")
    assert KEY not in r.text
    assert KEY not in c.get("/api/meta").text and KEY not in c.get("/api/scheduler").text

    class Bad:
        status_code = 401

        def raise_for_status(self):
            raise requests.HTTPError(f"401 Client Error: Unauthorized for url: https://x/odds?apiKey={KEY}",
                                     response=self)

    monkeypatch.setattr(providers.requests, "get", lambda url, params=None, timeout=None: Bad())
    r = c.post("/api/week/2026/4/update", json={})
    assert r.status_code == 502 and KEY not in r.text and "key was rejected" in r.text


# --------------------------------------------------------------------------- 12. backfilled weeks
def test_backfilled_week_gets_a_record_and_never_logs_another_weeks_lines(week, monkeypatch):
    _add_week3()
    m4 = _feed(monkeypatch, BASE)                                  # the market is week 4's board
    out = service.init_week(2026, 3, use_market=True)
    snaps = st.load_snapshots(2026, 3)
    assert snaps is None                                           # NE@BUF is a week later: not logged
    assert len(m4) == len(TEAMS)
    out = service.set_picks(2026, 3, ["KC", "LA", "DET", "NO", "BUF"])
    rec = history.get_week(2026, 3)
    assert rec is not None and rec["n_final"] == 5 and rec["complete"] is True
    assert not any("Fetch lines" in m for m in out.messages)
    with pytest.raises(service.UserError, match="has kicked off"):
        service.update(2026, 3)
    assert st.load_snapshots(2026, 3) is None and history.list_runs(2026, 3) == []

    # Week 4 keeps only its own games from a feed that also has another week's lines.
    from cover5.providers import MarketLine
    k = datetime.now(UTC) + timedelta(days=8)
    extra = MarketLine("KC", "LV", k, -3.0, 3, "test", datetime.now(UTC))
    monkeypatch.setattr(service, "fetch_market", lambda provider=None: m4 + [extra])
    service.update(2026, 4)
    assert ("KC", "LV") not in set(zip(st.load_snapshots(2026, 4).away, st.load_snapshots(2026, 4).home))


# --------------------------------------------------------------------------- 13. re-seeding keeps frozen lines
def test_reseed_keeps_frozen_lines_of_started_games_and_backs_up_the_sheet(week, monkeypatch):
    service.update(2026, 4)
    p = league_path(2026, 4)
    df = pd.read_csv(p)
    df.loc[df.home == "CLE", "home_spread"] = -6.5                # hand edit: PIT@CLE has kicked off
    df.loc[df.home == "HOU", "home_spread"] = -1.0                # hand edit: DAL@HOU, not in the next feed
    df.to_csv(p, index=False)
    m = [x for x in _market(datetime.now(UTC), BASE) if x.home != "HOU"]
    monkeypatch.setattr(service, "fetch_market", lambda provider=None: m)
    out = service.init_week(2026, 4, overwrite=True, use_market=True)
    new = pd.read_csv(p).set_index("home")
    assert new.loc["CLE", "home_spread"] == -6.5 and new.loc["HOU", "home_spread"] == -1.0
    assert new.loc["WAS", "home_spread"] == -5.0                  # unplayed: from the market
    assert any("kicked off" in msg for msg in out.messages)
    backups = list((config.LEAGUE_DIR / "backup").glob("2026_wk04-*.csv"))
    assert len(backups) == 1 and pd.read_csv(backups[0]).set_index("home").loc["CLE", "home_spread"] == -6.5

    # From nflverse only: unplayed games go back to the nflverse spread, played ones still stay.
    time.sleep(1)
    service.init_week(2026, 4, overwrite=True, use_market=False)
    new = pd.read_csv(p).set_index("home")
    assert new.loc["CLE", "home_spread"] == -6.5 and new.loc["HOU", "home_spread"] == -3.0


# --------------------------------------------------------------------------- 14. alert delivery
def test_alert_marked_sent_only_when_a_channel_accepts_it(week, monkeypatch):
    service.update(2026, 4)
    monkeypatch.setattr(config, "NTFY_TOPIC", "cover5-test")
    monkeypatch.setattr(config, "NTFY_SERVER", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hooks.example/secret-token")

    def refuse(url, **kw):
        if "hooks.example" in url:
            class R:
                ok, status_code = False, 404
            return R()
        raise requests.ConnectionError(f"HTTPConnectionPool(host='127.0.0.1', port=9) url {url}")

    monkeypatch.setattr(alerts.requests, "post", refuse)
    out = service.update(2026, 4, force_alert=True)
    assert out.alert["sent"] is False
    assert out.alert["failures"] == ["ntfy: ConnectionError (could not reach the server)",
                                     "webhook: the server answered HTTP 404"]
    run = history.list_runs(2026, 4, limit=1)[0]
    assert run["sent"] is False and any("Alert not delivered" in w for w in run["warnings"])
    assert "secret-token" not in json.dumps(run) and "cover5-test" not in json.dumps(out.alert)

    class OK:
        ok, status_code = True, 200
    monkeypatch.setattr(alerts.requests, "post", lambda url, **kw: OK())
    assert service.update(2026, 4, force_alert=True).alert["sent"] is True
    assert history.list_runs(2026, 4, limit=1)[0]["sent"] is True


# --------------------------------------------------------------------------- 15. local only
def test_cross_site_writes_and_foreign_hosts_are_refused(week, cfg_guard, tmp_path):
    c = TestClient(create_app(frontend_dist=tmp_path / "no-dist"))
    n = len(history.list_runs(2026, 4))
    evil = {"Origin": "https://evil.example"}
    assert c.post("/api/week/2026/4/update", headers=evil).status_code == 403
    assert c.post("/api/scheduler/run", headers={**evil, "Content-Type": "application/x-www-form-urlencoded"},
                  content="a=b").status_code == 403
    assert c.post("/api/history/refresh", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert len(history.list_runs(2026, 4)) == n
    assert c.get("/api/settings", headers={"Host": "attacker.example:8841"}).status_code == 403
    # The app itself, the Vite dev server, IP literals and plain API clients still work.
    assert c.post("/api/week/2026/4/update", headers={"Origin": "http://testserver"}).status_code == 200
    assert c.post("/api/week/2026/4/update", headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert c.get("/api/settings", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert c.get("/api/settings", headers={"Host": "[::1]:8765"}).status_code == 200
    assert c.get("/api/meta", headers=evil).status_code == 200      # reads are fine (CORS keeps them private)


# --------------------------------------------------------------------------- 17. a missed slot
def test_finished_week_with_a_missed_slot_is_complete(week):
    _add_week3()
    service.init_week(2026, 3, use_market=False)
    out = service.set_picks(2026, 3, ["KC", "LA", "DET", "NO"])
    assert not any("will suggest" in m for m in out.messages)
    assert any("every other game has kicked off" in m for m in out.messages)
    rec = history.get_week(2026, 3)
    assert rec["complete"] is True and len(rec["picks"]) == 4 and rec["n_picks"] == 5


# --------------------------------------------------------------------------- 22. no CLI flags in errors
def test_kickoff_error_does_not_name_a_cli_flag(week):
    service.update(2026, 4)
    with pytest.raises(service.UserError) as e:
        service.set_points(2026, 4, "WAS", 3)
    assert "--week" not in str(e.value) and "enter it in week 3" in str(e.value)


# --------------------------------------------------------------------------- 9. backtest from the app
def test_backtest_runs_in_the_background(cfg_guard, tmp_path, monkeypatch):
    from cover5 import backtest_movement
    monkeypatch.setattr(backtest_movement, "run",
                        lambda out_path=None, **kw: out_path.write_text(json.dumps({"ok": 1})) and {"ok": 1})
    c = TestClient(create_app(frontend_dist=tmp_path / "no-dist"))
    assert c.get("/api/backtest").status_code == 404
    r = c.post("/api/backtest/run")
    assert r.status_code == 202 and r.json()["started"] is True
    for _ in range(100):
        s = c.get("/api/backtest/status").json()
        if s["state"] != "running":
            break
        time.sleep(0.05)
    assert s["state"] == "done" and s["error"] is None
    assert c.get("/api/backtest").json() == {"ok": 1}


def test_standings_only_record_for_an_untracked_week(cfg_guard, tmp_path):
    c = TestClient(create_app(frontend_dist=tmp_path / "no-dist"))
    r = c.put("/api/history/2026/1", json={"week_rank": 2, "entrants": 10, "app_points": 31.5})
    assert r.status_code == 200 and r.json()["picks"] == []
    w = c.get("/api/history?season=2026").json()["weeks"]
    assert [x["week"] for x in w] == [1] and w[0]["app_points"] == 31.5
