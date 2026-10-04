"""The local HTTP API (docs/api.md) end to end with FastAPI's TestClient."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cover5 import config, providers, service
from cover5 import state as st
from cover5.server import settings as settings_mod
from cover5.server.app import create_app

from tests.test_overrides import TEAMS, _market, week  # noqa: F401  (fixture)

UTC = timezone.utc
W = "/api/week/2026/4"


@pytest.fixture
def cfg_guard(tmp_root, monkeypatch):
    """Settings write onto cover5.config: restore it after the test and keep the real env out."""
    monkeypatch.setattr(config, "PROVIDER", "espn")
    monkeypatch.setattr(config, "ODDS_API_KEY", "")
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.sh")
    monkeypatch.setattr(config, "SWAP_MARGIN", 0.5)
    monkeypatch.setattr(config, "FLIP_MARGIN", 0.5)
    monkeypatch.setattr(config, "MARKET_FILE", "")
    monkeypatch.setattr(settings_mod, "ENV_DEFAULTS", settings_mod._env_defaults())
    return tmp_root


@pytest.fixture
def client(cfg_guard, tmp_path):
    return TestClient(create_app(frontend_dist=tmp_path / "no-dist"))


@pytest.fixture
def wc(week, cfg_guard, tmp_path):
    """Client over the synthetic week 4 from tests/test_overrides.py (initialised, no market yet)."""
    return TestClient(create_app(frontend_dist=tmp_path / "no-dist"))


def data_hash(root) -> str:
    h = hashlib.sha256()
    for p in sorted((root / "data").rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


# --------------------------------------------------------------------------- meta & schedule
def test_meta(wc):
    m = ok(wc.get("/api/meta"))
    assert m["current"] == {"season": 2026, "week": 4}
    assert 2026 in m["seasons"]
    assert m["config"] == {"n_picks": 5, "swap_margin": 0.5, "flip_margin": 0.5, "provider": "espn",
                           "odds_api_key_set": False, "ntfy_topic_set": False, "webhook_set": False}
    assert set(m["scheduler"]) == {"enabled", "running", "last_tick", "last_update_at", "last_result",
                                   "last_error", "next_due"}


def test_schedule(wc):
    rows = ok(wc.get("/api/schedule/2026"))
    assert len(rows) == 1
    r = rows[0]
    assert r["week"] == 4 and r["n_games"] == 8 and r["has_league_file"] and not r["has_record"]
    assert r["first_kickoff"] < r["last_kickoff"]
    ok(wc.post(f"{W}/update"))
    assert ok(wc.get("/api/schedule/2026"))[0]["has_record"]
    assert wc.get("/api/schedule/1990").status_code == 404


# --------------------------------------------------------------------------- week
def test_week_view_and_uninitialised(wc):
    v = ok(wc.get(W))
    assert v["has_league_file"] and len(v["games"]) == 8 and v["n_picks"] == 5
    v = ok(wc.get("/api/week/2026/9"))
    assert v == {"season": 2026, "week": 9, "has_league_file": False, "now": v["now"]}


def test_get_endpoints_never_write(wc, tmp_root):
    gets = [W, f"{W}/snapshots", f"{W}/runs", f"{W}/score", "/api/week/2026/9", "/api/meta",
            "/api/schedule/2026", "/api/history", "/api/history?season=2026", "/api/history/export",
            "/api/backtest", "/api/settings", "/api/scheduler", "/", "/history"]
    # before any history database exists, reads must not create it
    before = data_hash(tmp_root)
    for g in gets:
        assert wc.get(g).status_code in (200, 404), g
    assert not config.DB_PATH.exists()
    assert data_hash(tmp_root) == before
    # and with a populated week
    ok(wc.post(f"{W}/update"))
    ok(wc.put(f"{W}/points", json={"team": "CLE", "points": 8, "live": True}))
    before = data_hash(tmp_root)
    for g in gets:
        assert wc.get(g).status_code in (200, 404), g
    assert data_hash(tmp_root) == before


def test_update_and_every_override(wc):
    out = ok(wc.post(f"{W}/update", json={"force_alert": False}))
    assert set(out) == {"week", "alert", "messages"}
    assert [p["team"] for p in out["week"]["picks"]][:4] == ["WAS", "BAL", "BUF", "CHI"]
    assert out["alert"]["changed"] and "Fetched" in out["messages"][0]
    json.dumps(out, allow_nan=False)

    out = ok(wc.put(f"{W}/picks", json={"teams": ["CLE", "WAS", "BAL", "DAL", "JAX"]}))
    assert {p["team"] for p in out["week"]["picks"]} == {"CLE", "WAS", "BAL", "BUF", "CHI"}
    assert set(out["alert"]["body"].split()) >= {"drop", "DAL", "JAX"}

    out = ok(wc.post(f"{W}/locks", json={"teams": ["JAX"]}))
    jax = next(p for p in out["week"]["picks"] if p["team"] == "JAX")
    assert jax["manual"] and jax["locked"]
    out = ok(wc.delete(f"{W}/locks/JAX"))
    assert out["week"]["overrides"]["locks"] == []

    out = ok(wc.put(f"{W}/lines", json={"team": "NYG", "spread": -7}))
    assert out["week"]["overrides"]["lines"][0]["value"] == -7.0
    out = ok(wc.put(f"{W}/lines", json={"team": "ARI", "spread": "PK"}))
    g = next(g for g in out["week"]["games"] if g["home"] == "NYG")
    assert g["league_home_spread"] == 0.0 and g["line_overridden"]

    out = ok(wc.put(f"{W}/scores", json={"team": "CLE", "team_points": 27, "opponent_points": 20,
                                          "live": True}))
    g = next(g for g in out["week"]["games"] if g["home"] == "CLE")
    assert g["result"] == {"home_score": 27.0, "away_score": 20.0, "final": False, "source": "score override"}

    out = ok(wc.put(f"{W}/points", json={"team": "CLE", "points": 13.5}))
    cle = next(p for p in out["week"]["picks"] if p["team"] == "CLE")
    assert cle["points"] == 13.5 and cle["status"] == "final" and cle["manual"]

    out = ok(wc.delete(f"{W}/overrides/CLE", params={"kind": ["score"]}))
    assert out["week"]["overrides"]["results"] == [] and out["week"]["overrides"]["points"]
    out = ok(wc.delete(f"{W}/overrides/CLE"))
    assert out["week"]["overrides"]["points"] == [] and out["week"]["overrides"]["locks"] == []
    out = ok(wc.delete(f"{W}/overrides/CLE"))
    assert out["messages"][0].startswith("Nothing to clear")


def test_user_errors_are_400(wc):
    ok(wc.post(f"{W}/update"))
    cases = [
        wc.put(f"{W}/picks", json={"teams": ["CLE", "PIT"]}),                     # same game
        wc.put(f"{W}/picks", json={"teams": ["CLE", "WAS", "BAL", "BUF", "CHI", "HOU"]}),
        wc.post(f"{W}/locks", json={"teams": ["SEA"]}),                           # not on slate
        wc.post(f"{W}/locks", json={"teams": ["XYZ"]}),                           # unknown team
        wc.post(f"{W}/locks", json={"teams": []}),
        wc.put(f"{W}/lines", json={"team": "NYG", "spread": "abc"}),
        wc.put(f"{W}/points", json={"team": "CLE", "points": "lots"}),           # validation
        wc.put(f"{W}/points", json={"team": "CLE"}),                              # missing field
        wc.put(f"{W}/scores", json={"team": "CLE", "team_points": -1, "opponent_points": 3}),
        wc.put(f"{W}/picks", json={"teams": ["CLE"], "extra": 1}),
        wc.delete(f"{W}/overrides/CLE", params={"kind": "bogus"}),
        wc.post(f"{W}/init"),                                                     # exists
        wc.put("/api/week/2026/9/picks", json={"teams": []}),                     # not initialised
        wc.post("/api/week/2026/9/update"),
        wc.get("/api/week/2026/9/score"),
        wc.post("/api/week/2026/12/init", json={"use_market": False}),           # no such games
        wc.get("/api/week/2026/x"),
    ]
    for r in cases:
        assert r.status_code == 400, (r.request.url, r.text)
        assert isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert "Initialise the week first" in wc.post("/api/week/2026/9/update").json()["detail"]
    assert "points" in wc.put(f"{W}/points", json={"team": "CLE", "points": "lots"}).json()["detail"]


def test_provider_failure_is_502(wc, monkeypatch):
    def boom(provider=None):
        raise RuntimeError("odds API said no")
    monkeypatch.setattr(service, "fetch_market", boom)
    r = wc.post(f"{W}/update")
    assert r.status_code == 502 and "odds API said no" in r.json()["detail"]
    # init falls back to nflverse spreads and reports it
    out = ok(wc.post(f"{W}/init", json={"overwrite": True}))
    assert any("Market fetch failed" in m for m in out["messages"])


def test_init_overwrite_and_market_seed(wc):
    out = ok(wc.post(f"{W}/init", json={"overwrite": True, "use_market": True}))
    assert out["week"]["has_league_file"] and out["alert"] is None
    assert st.load_snapshots(2026, 4) is not None                     # the market seed was logged
    out = ok(wc.post(f"{W}/init", json={"overwrite": True, "use_market": False}))
    assert any("Seeded 8 games" in m for m in out["messages"])


def test_unknown_api_route_is_404(wc):
    r = wc.get("/api/nope")
    assert r.status_code == 404 and r.json()["detail"]
    assert wc.post("/api/nope/deeper").status_code == 404


# --------------------------------------------------------------------------- snapshots, runs, score
def test_snapshots_grouped_by_game_with_league_line(wc, monkeypatch):
    ok(wc.post(f"{W}/update"))
    now = datetime.now(UTC)
    later = [m for m in _market(now, {"CLE": -6.5, "WAS": -5.5})]
    for m in later:
        m.fetched_at = now
    monkeypatch.setattr(service, "fetch_market", lambda provider=None: later)
    ok(wc.post(f"{W}/update"))
    ok(wc.put(f"{W}/lines", json={"team": "NYG", "spread": -7}))
    # a line for a game outside this week must not appear
    st.append_snapshot(2026, 4, [providers.MarketLine("SEA", "SF", now + timedelta(days=9), -2.0, 3, "t", now)])

    g = ok(wc.get(f"{W}/snapshots"))["games"]
    assert len(g) == 8 and [x["kickoff_utc"] for x in g] == sorted(x["kickoff_utc"] for x in g)
    by = {x["home"]: x for x in g}
    assert by["NYG"]["league_home_spread"] == -7.0          # after the override
    assert by["CLE"]["league_home_spread"] == -3.0
    was = by["WAS"]["series"]
    assert [s["home_spread"] for s in was] == [-5.0, -5.5]
    assert was[0]["t"] < was[1]["t"] and was[0]["books"] == 3 and was[0]["source"] == "test"
    assert set(by["WAS"]) == {"game_id", "away", "home", "kickoff_utc", "league_home_spread", "series"}
    assert "SF" not in by

    # an uninitialised week lists the schedule with no league line and no series
    assert ok(wc.get("/api/week/2026/9/snapshots")) == {"games": []}


def test_runs_and_score(wc):
    ok(wc.post(f"{W}/update"))
    ok(wc.put(f"{W}/picks", json={"teams": ["CLE", "WAS", "BAL", "BUF", "CHI"]}))
    runs = ok(wc.get(f"{W}/runs"))
    assert [r["reason"] for r in runs] == ["after you set your picks", "market update"]
    assert set(runs[0]) == {"id", "season", "week", "at", "reason", "changed", "sent", "diff", "picks",
                            "summary", "warnings", "title", "body"}
    assert len(ok(wc.get(f"{W}/runs", params={"limit": 1}))) == 1
    assert wc.get(f"{W}/runs", params={"limit": 0}).status_code == 400

    ok(wc.put(f"{W}/points", json={"team": "CLE", "points": 6}))
    sc = ok(wc.get(f"{W}/score"))
    cle = next(p for p in sc["picks"] if p["label"].startswith("CLE"))
    assert cle == {"label": "CLE -3 vs PIT", "status": "final", "points": 6.0, "source": "points override"}
    assert sc["summary"]["final"] == 6.0 and "line" in sc["summary"]


# --------------------------------------------------------------------------- history
def test_history_manual_fields_survive_refresh(wc):
    ok(wc.post(f"{W}/update"))
    ok(wc.put(f"{W}/points", json={"team": "WAS", "points": 10}))
    h = ok(wc.get("/api/history"))
    assert h["seasons"] == [2026] and len(h["weeks"]) == 1
    assert h["totals"] == {"weeks": 1, "complete_weeks": 0, "final_points": 10.0,
                           "movement_edge": h["weeks"][0]["movement_edge"], "avg_week": None}

    rec = ok(wc.put("/api/history/2026/4", json={"week_rank": 1, "entrants": 10, "overall_points": 42,
                                                  "overall_rank": 2, "app_points": 10, "notes": "nice"}))
    assert rec["week_rank"] == 1 and rec["overall_points"] == 42.0 and rec["notes"] == "nice"
    # partial update leaves the other fields; null clears one
    rec = ok(wc.put("/api/history/2026/4", json={"notes": None}))
    assert rec["notes"] is None and rec["week_rank"] == 1

    r = ok(wc.post("/api/history/refresh", json={"season": 2026}))
    assert r["updated"] == 1 and r["weeks"][0]["week_rank"] == 1 and r["weeks"][0]["overall_rank"] == 2
    r = ok(wc.post("/api/history/refresh"))
    assert r["updated"] == 1
    assert ok(wc.get("/api/history", params={"season": 2025}))["weeks"] == []

    # a week with no league file can still get standings typed in
    rec = ok(wc.put("/api/history/2026/3", json={"week_rank": 4, "entrants": 10}))
    assert rec["week"] == 3 and rec["n_picks"] == 0

    for bad in ({"week_rank": 0}, {"bogus": 1}, {"entrants": "many"}):
        r = wc.put("/api/history/2026/4", json=bad)
        assert r.status_code == 400 and r.json()["detail"], bad


def test_history_export_download(wc):
    ok(wc.post(f"{W}/update"))
    ok(wc.put("/api/history/2026/4", json={"week_rank": 3}))
    r = wc.get("/api/history/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and 'filename="cover5-history-' in cd and cd.endswith('.json"')
    d = r.json()
    assert d["exported_at"] and d["weeks"][0]["week_rank"] == 3 and d["runs"][0]["reason"] == "market update"


# --------------------------------------------------------------------------- backtest
def test_backtest(client, tmp_root):
    r = client.get("/api/backtest")
    assert r.status_code == 404 and "backtest" in r.json()["detail"]
    p = tmp_root / "data" / "history" / "backtest_results.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"seasons": [2024], "avg": NaN}')
    assert ok(client.get("/api/backtest")) == {"seasons": [2024], "avg": None}


# --------------------------------------------------------------------------- frontend, CORS
def test_spa_fallback(cfg_guard, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>APP</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    c = TestClient(create_app(frontend_dist=dist))
    for path in ("/", "/history", "/week/2026/4", "/settings/deep/link"):
        r = c.get(path)
        assert r.status_code == 200 and "APP" in r.text, path
    r = c.get("/assets/app.js")
    assert r.text == "console.log(1)" and "javascript" in r.headers["content-type"]
    assert "nope" not in c.get("/..%2Fsecret.txt").text
    assert c.get("/api/unknown").status_code == 404


def test_build_hint_without_dist(client):
    r = client.get("/")
    assert r.status_code == 200 and "npm --prefix frontend install" in r.text
    assert "npm --prefix frontend run build" in client.get("/any/page").text


def test_cors_for_vite_dev_server(client):
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        r = client.options("/api/settings", headers={"Origin": origin, "Access-Control-Request-Method": "PUT"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    r = client.get("/api/settings", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# --------------------------------------------------------------------------- scheduler endpoints
def test_scheduler_endpoints(wc):
    s = ok(wc.get("/api/scheduler"))
    assert s["enabled"] and not s["running"] and s["last_tick"] is None
    r = ok(wc.post("/api/scheduler/run"))
    assert r["ran"] and r["outcome"]["week"]["season"] == 2026 and "Updated 2026 week 4" in r["message"]
    s = ok(wc.get("/api/scheduler"))
    assert s["last_tick"] and s["last_update_at"] and s["last_result"].startswith("Updated")
    # Run now ignores the interval
    assert ok(wc.post("/api/scheduler/run"))["ran"]


def test_scheduler_run_with_nothing_to_do(client, monkeypatch, tmp_root):
    """Every game kicked off: Run now reports it and does nothing."""
    from tests.test_overrides import _write_games
    _write_games(tmp_root, datetime.now(UTC) - timedelta(days=1, hours=1))
    service._games_cache.update(mtime=None, df=None)
    r = ok(client.post("/api/scheduler/run"))
    assert not r["ran"] and "kicked off" in r["message"]


# --------------------------------------------------------------------------- file market provider
def test_file_provider_through_the_api(wc, monkeypatch, tmp_root):
    now = datetime.now(UTC)
    lines = [{"away": a, "home": h, "kickoff_utc": (now + timedelta(days=1, minutes=i)).isoformat(),
              "home_spread": -3.0} for i, (a, h) in enumerate(TEAMS)]
    lines[1]["home_spread"] = -6.0                                   # WAS
    lines[2].update(home_spread=None)                                 # skipped
    lines[3].update(away="Patriots", home="BUF", books=4, source="demo")
    (tmp_root / "data" / "market.json").write_text(json.dumps(lines))
    monkeypatch.setattr(service, "fetch_market", providers.fetch_market)   # undo the fixture's fake
    ok(wc.put("/api/settings", json={"provider": "file"}))
    assert config.PROVIDER == "file"
    out = ok(wc.post(f"{W}/update"))
    assert out["week"]["picks"][0]["team"] == "WAS" and out["week"]["picks"][0]["edge"] == 3.0
    assert "Fetched 7 market lines" in out["messages"][0]
    snaps = st.load_snapshots(2026, 4)
    assert set(snaps.source) == {"file", "demo"}

    monkeypatch.setattr(config, "MARKET_FILE", str(tmp_root / "missing.json"))
    r = wc.post(f"{W}/update")
    assert r.status_code == 502 and "missing.json" in r.json()["detail"]


def test_parse_file_rejects_bad_rows():
    with pytest.raises(RuntimeError, match="row 0"):
        providers.parse_file([{"away": "Nowhere", "home": "BUF", "kickoff_utc": "2026-10-04T17:00:00Z",
                               "home_spread": -3}])
    got = providers.parse_file({"lines": [{"away": "NE", "home": "BUF", "kickoff_utc": "2026-10-04T17:00:00Z",
                                           "home_spread": "-3.5"}]})
    assert got[0].home_spread == -3.5 and got[0].kickoff_utc.tzinfo is not None and got[0].books == 1
