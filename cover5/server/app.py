"""The local HTTP API (docs/api.md) over cover5.service, plus the built frontend.

Every week operation goes through ``cover5.service``; this module only maps
HTTP to those calls and errors to status codes:

* ``service.UserError``             -> 400 ``{"detail"}``
* ``service.ProviderError``         -> 502 (odds provider failed)
* ``requests.RequestException``     -> 502 (schedule download failed)
* request validation errors         -> 400 with a readable ``detail`` string
* ``ValueError`` from history / settings -> 400

GET endpoints never write the live week's files: they read under
``service.LOCK`` so they never see a week half written by the scheduler. The
one exception is the history database: ``GET /api/history`` and the export
first bring unfinished week records up to date with nflverse results
(``service.sync_records``), writing only the records whose values changed.

The server is for this machine only. Requests whose Host isn't localhost or an
IP address are refused (DNS rebinding), and so are writes (any method but
GET/HEAD/OPTIONS) sent by a page from another site, judged by the Origin and
Sec-Fetch-Site headers, so a web page can't spend odds API quota or change
picks behind your back.
"""
from __future__ import annotations

import ipaddress
import json
import math
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Union

import numpy as np
import pandas as pd
import requests
from fastapi import Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from cover5 import config, history, service
from cover5 import overrides as ovr
from cover5.providers import redact
from cover5 import state as st
from cover5.league import league_path, load_week_file
from cover5.schedule import current_week, week_slate
from cover5.server import settings as settings_mod
from cover5.server.scheduler import Scheduler

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

BUILD_HINT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cover 5</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem}
code{background:#eee;padding:.1rem .3rem;border-radius:4px}</style></head>
<body><h1>Cover 5</h1>
<p>The API is running, but the web app has not been built yet. From the project folder run:</p>
<p><code>npm --prefix frontend install &amp;&amp; npm --prefix frontend run build</code></p>
<p>then reload this page. The API itself is at <a href="/api/meta">/api/meta</a>.</p>
</body></html>"""


# --------------------------------------------------------------------------- JSON safety
def jsonable(x):
    """Plain JSON types only: NaN/inf -> None, numpy/pandas scalars -> Python, datetimes -> ISO."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        f = float(x)
        return f if math.isfinite(f) else None
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return x.isoformat()
    if x is pd.NaT:
        return None
    if isinstance(x, Path):
        return str(x)
    return x


def ok(x) -> JSONResponse:
    return JSONResponse(jsonable(x))


# --------------------------------------------------------------------------- request bodies
class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InitBody(_Body):
    overwrite: bool = False
    use_market: bool = True


class UpdateBody(_Body):
    force_alert: bool = False


class TeamsBody(_Body):
    teams: list[str]


class PicksBody(_Body):
    teams: list[str]
    lock: Optional[list[str]] = None     # of those teams, the ones to lock (picked by hand)


class LineBody(_Body):
    team: str
    spread: Union[float, str]


class ScoreBody(_Body):
    team: str
    team_points: float = Field(ge=0)
    opponent_points: float = Field(ge=0)
    live: bool = False
    force: bool = False          # allow a game that hasn't kicked off


class PointsBody(_Body):
    team: str
    points: float
    live: bool = False
    force: bool = False          # allow a game that hasn't kicked off


class RefreshBody(_Body):
    season: Optional[int] = None


class ManualBody(_Body):
    week_rank: Optional[int] = Field(default=None, ge=1)
    entrants: Optional[int] = Field(default=None, ge=1)
    overall_points: Optional[float] = None
    overall_rank: Optional[int] = Field(default=None, ge=1)
    app_points: Optional[float] = None
    notes: Optional[str] = None


class SchedulerSettingsBody(_Body):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=settings_mod.INTERVAL_MIN, le=settings_mod.INTERVAL_MAX)
    sunday_interval_minutes: Optional[int] = Field(default=None, ge=settings_mod.INTERVAL_MIN,
                                                   le=settings_mod.INTERVAL_MAX)
    auto_init_wednesday: Optional[bool] = None


class SettingsBody(_Body):
    provider: Optional[Literal["oddsapi", "espn", "file"]] = None
    odds_api_key: Optional[str] = None
    ntfy_topic: Optional[str] = None
    ntfy_server: Optional[str] = None
    webhook_url: Optional[str] = None
    swap_margin: Optional[float] = Field(default=None, ge=0, le=settings_mod.MARGIN_MAX)
    flip_margin: Optional[float] = Field(default=None, ge=0, le=settings_mod.MARGIN_MAX)
    scheduler: Optional[SchedulerSettingsBody] = None
    # read-only fields of GET /api/settings, accepted so a client can PUT back what it got
    odds_api_key_set: Optional[bool] = None
    odds_api_key_hint: Optional[str] = None


# --------------------------------------------------------------------------- helpers
def _validation_detail(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        where = ".".join(loc)
        parts.append(f"{where}: {err.get('msg')}" if where else str(err.get("msg")))
    return "; ".join(parts) or "invalid request"


def _week_league(season: int, week: int) -> pd.DataFrame | None:
    """League lines after overrides, or None when the week is not initialised."""
    if not league_path(season, week).exists():
        return None
    return ovr.apply_line_overrides(load_week_file(season, week), ovr.load_overrides(season, week))


def _history_totals(weeks: list[dict]) -> dict:
    complete = [w for w in weeks if w["complete"]]
    final = sum(float(w["final_points"]) for w in weeks)
    return {
        "weeks": len(weeks),
        "complete_weeks": len(complete),
        "final_points": round(final, 2),
        "movement_edge": round(sum(float(w["movement_edge"]) for w in weeks), 2),
        # average final score over weeks whose five picks are all final
        "avg_week": (round(sum(float(w["final_points"]) for w in complete) / len(complete), 2)
                     if complete else None),
    }


def _local_host(host_header: str) -> bool:
    """localhost (or a *.localhost name), an IP address, or Starlette's TestClient host."""
    h = host_header.strip().lower()
    if h.startswith("["):                                   # [::1]:8765
        h = h[1:h.find("]")] if "]" in h else h[1:]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    if h in ("localhost", "testserver") or h.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def _cross_site_write(request: Request) -> bool:
    if request.method in SAFE_METHODS:
        return False
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if origin is not None:
        return origin not in {f"http://{host}", f"https://{host}", *DEV_ORIGINS}
    return request.headers.get("sec-fetch-site") == "cross-site"


class BacktestJob:
    """Runs the movement backtest (cover5.backtest_movement) in a thread; one at a time."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state, self.started_at, self.finished_at, self.error = "idle", None, None, None

    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "started_at": self.started_at, "finished_at": self.finished_at,
                    "error": self.error}

    def start(self, out_path: Path) -> bool:
        with self._lock:
            if self.state == "running":
                return False
            self.state, self.error = "running", None
            self.started_at, self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds"), None
        threading.Thread(target=self._run, args=(out_path,), name="cover5-backtest", daemon=True).start()
        return True

    def _run(self, out_path: Path) -> None:
        try:
            from cover5 import backtest_movement
            out_path.parent.mkdir(parents=True, exist_ok=True)
            backtest_movement.run(out_path=out_path)
            state, err = "done", None
        except Exception as e:  # noqa: BLE001
            state, err = "error", f"{type(e).__name__}: {e}"
        with self._lock:
            self.state, self.error = state, err
            self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _backtest_path() -> Path:
    return config.DATA / "history" / "backtest_results.json"


def _meta_seasons(current_season: int) -> list[int]:
    seasons = set(history.seasons()) | {current_season}
    for p in config.LEAGUE_DIR.glob("*_wk*.csv"):
        try:
            seasons.add(int(p.stem.split("_wk")[0]))
        except ValueError:
            pass
    return sorted(seasons)


# --------------------------------------------------------------------------- app
def create_app(frontend_dist: Path | None = None, scheduler: Scheduler | None = None,
               scheduler_allowed: bool = False) -> FastAPI:
    """Build the API. Applies saved settings to cover5.config.

    ``scheduler_allowed`` lets PUT /api/settings start the background thread when
    the scheduler is switched on (``serve`` sets it; tests leave it off).
    """
    settings_mod.apply()
    dist = Path(frontend_dist) if frontend_dist is not None else FRONTEND_DIST
    sched = scheduler or Scheduler()

    app = FastAPI(title="Cover 5", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)
    app.state.scheduler = sched
    app.state.scheduler_allowed = scheduler_allowed
    app.state.backtest = BacktestJob()
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Disposition"])

    @app.middleware("http")
    async def _local_only(request: Request, call_next):
        # Added after CORS, so it runs first: refused requests never reach a handler.
        if not _local_host(request.headers.get("host", "")):
            return JSONResponse({"detail": "This server only answers requests for localhost."}, status_code=403)
        if _cross_site_write(request):
            return JSONResponse({"detail": "Cross-site requests can't change Cover 5 data."}, status_code=403)
        return await call_next(request)

    # ---- errors
    @app.exception_handler(service.ProviderError)
    async def _provider_error(request: Request, exc: service.ProviderError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(service.UserError)
    async def _user_error(request: Request, exc: service.UserError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(requests.RequestException)
    async def _network_error(request: Request, exc: requests.RequestException):
        return JSONResponse({"detail": redact(f"Network request failed: {exc}")}, status_code=502)

    @app.exception_handler(settings_mod.SettingsError)
    async def _settings_error(request: Request, exc: settings_mod.SettingsError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": _validation_detail(exc)}, status_code=400)

    # ---- meta / schedule
    @app.get("/api/meta")
    def meta():
        now = datetime.now(timezone.utc)
        season, week = current_week(service.games(), now)
        return ok({
            "current": {"season": season, "week": week},
            "now": now.isoformat(timespec="seconds"),
            "seasons": _meta_seasons(season),
            "config": {
                "n_picks": config.N_PICKS,
                "swap_margin": config.SWAP_MARGIN,
                "flip_margin": config.FLIP_MARGIN,
                "provider": config.PROVIDER,
                "odds_api_key_set": bool(config.ODDS_API_KEY),
                "ntfy_topic_set": bool(config.NTFY_TOPIC),
                "webhook_set": bool(config.ALERT_WEBHOOK_URL),
            },
            "scheduler": sched.status(now),
            "results": service.results_info(),
        })

    @app.get("/api/schedule/{season}")
    def schedule(season: int):
        g = service.games()
        reg = g[(g.season == season) & (g.game_type == "REG")]
        if reg.empty:
            return JSONResponse({"detail": f"No regular season schedule for {season}"}, status_code=404)
        recorded = {w["week"] for w in history.list_weeks(season)}
        out = []
        for wk, grp in reg.groupby("week"):
            wk = int(wk)
            kicks = list(grp.kickoff_utc)
            out.append({"week": wk, "n_games": len(grp),
                        "first_kickoff": min(kicks).isoformat(), "last_kickoff": max(kicks).isoformat(),
                        "has_league_file": league_path(season, wk).exists(),
                        "has_record": wk in recorded})
        return ok(sorted(out, key=lambda r: r["week"]))

    # ---- week reads
    @app.get("/api/week/{season}/{week}")
    def get_week(season: int, week: int):
        with service.LOCK:
            return ok(service.week_view(season, week))

    @app.get("/api/week/{season}/{week}/snapshots")
    def snapshots(season: int, week: int):
        with service.LOCK:
            league = _week_league(season, week)
            snaps = st.load_snapshots(season, week)
            if league is not None:
                base = [{"game_id": r.game_id, "away": r.away, "home": r.home, "kickoff_utc": r.kickoff_utc,
                         "league_home_spread": r.home_spread} for r in league.itertuples()]
            else:
                slate = week_slate(service.games(), season, week)
                base = [{"game_id": r.game_id, "away": r.away_team, "home": r.home_team,
                         "kickoff_utc": r.kickoff_utc, "league_home_spread": None} for r in slate.itertuples()]
        series: dict[tuple, list] = {}
        if snaps is not None and not snaps.empty:
            snaps = snaps.assign(t_parsed=pd.to_datetime(snaps["fetched_at"], utc=True, format="ISO8601"))
            for r in snaps.sort_values("t_parsed", kind="stable").itertuples():
                series.setdefault((r.away, r.home), []).append({
                    "t": r.t_parsed.isoformat(), "home_spread": r.home_spread,
                    "books": 0 if pd.isna(r.books) else int(r.books),
                    "source": None if pd.isna(r.source) else str(r.source)})
        games_out = sorted(({**b, "kickoff_utc": pd.Timestamp(b["kickoff_utc"]).isoformat(),
                             "series": series.get((b["away"], b["home"]), [])} for b in base),
                           key=lambda b: (b["kickoff_utc"], b["game_id"]))
        return ok({"games": games_out})

    @app.get("/api/week/{season}/{week}/runs")
    def runs(season: int, week: int, limit: int = Query(100, ge=1, le=1000)):
        return ok(history.list_runs(season, week, limit=limit))

    @app.get("/api/week/{season}/{week}/score")
    def score(season: int, week: int):
        with service.LOCK:
            return ok(service.score_week(season, week, refresh=False))

    # ---- week writes (each returns an Outcome)
    @app.post("/api/week/{season}/{week}/init")
    def init(season: int, week: int, body: Optional[InitBody] = Body(None)):
        b = body or InitBody()
        return ok(service.init_week(season, week, overwrite=b.overwrite, use_market=b.use_market).to_dict())

    @app.post("/api/week/{season}/{week}/update")
    def update(season: int, week: int, body: Optional[UpdateBody] = Body(None)):
        b = body or UpdateBody()
        return ok(service.update(season, week, force_alert=b.force_alert).to_dict())

    @app.put("/api/week/{season}/{week}/picks")
    def put_picks(season: int, week: int, body: PicksBody):
        return ok(service.set_picks(season, week, body.teams, lock=body.lock).to_dict())

    @app.post("/api/week/{season}/{week}/confirm")
    def confirm(season: int, week: int):
        return ok(service.confirm_picks(season, week).to_dict())

    @app.post("/api/week/{season}/{week}/results")
    def week_results(season: int, week: int):
        return ok(service.week_results(season, week).to_dict())

    @app.post("/api/week/{season}/{week}/locks")
    def post_locks(season: int, week: int, body: TeamsBody):
        if not body.teams:
            raise service.UserError("name at least one team to lock")
        return ok(service.lock(season, week, body.teams).to_dict())

    @app.delete("/api/week/{season}/{week}/locks/{team}")
    def delete_lock(season: int, week: int, team: str):
        return ok(service.unlock(season, week, [team]).to_dict())

    @app.put("/api/week/{season}/{week}/lines")
    def put_line(season: int, week: int, body: LineBody):
        return ok(service.set_line(season, week, body.team, body.spread).to_dict())

    @app.put("/api/week/{season}/{week}/scores")
    def put_score(season: int, week: int, body: ScoreBody):
        return ok(service.set_score(season, week, body.team, body.team_points, body.opponent_points,
                                    live=body.live, force=body.force).to_dict())

    @app.put("/api/week/{season}/{week}/points")
    def put_points(season: int, week: int, body: PointsBody):
        return ok(service.set_points(season, week, body.team, body.points, live=body.live,
                                     force=body.force).to_dict())

    @app.delete("/api/week/{season}/{week}/overrides/{team}")
    def delete_overrides(season: int, week: int, team: str, kind: Optional[list[str]] = Query(None)):
        return ok(service.clear(season, week, team, kind or None).to_dict())

    # ---- history
    @app.get("/api/history")
    def get_history(season: Optional[int] = None):
        service.sync_records([season] if season else None)
        weeks = history.list_weeks(season)
        return ok({"seasons": history.seasons(), "weeks": weeks, "totals": _history_totals(weeks)})

    @app.post("/api/history/refresh")
    def refresh_history(body: Optional[RefreshBody] = Body(None)):
        season = body.season if body else None
        n = service.refresh_history([season] if season else None, download=True)
        return ok({"updated": n, "weeks": history.list_weeks(season), "results": service.results_info()})

    @app.get("/api/history/export")
    def export_history():
        service.sync_records()
        now = datetime.now(timezone.utc)
        payload = jsonable({"exported_at": now.isoformat(timespec="seconds"),
                            "weeks": history.list_weeks(), "runs": history.all_runs()})
        return Response(json.dumps(payload, indent=2), media_type="application/json",
                        headers={"Content-Disposition":
                                 f'attachment; filename="cover5-history-{now:%Y%m%d}.json"'})

    @app.put("/api/history/{season}/{week}")
    def put_history(season: int, week: int, body: ManualBody):
        try:
            return ok(history.set_manual(season, week, **body.model_dump(exclude_unset=True)))
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    # ---- backtest
    @app.get("/api/backtest")
    def backtest():
        p = _backtest_path()
        if not p.exists():
            return JSONResponse({"detail": f"No backtest results at {p}. Run the movement backtest "
                                           "(python -m cover5.backtest_movement) to create them."},
                                status_code=404)
        return ok(json.loads(p.read_text()))

    @app.get("/api/backtest/status")
    def backtest_status():
        return ok(app.state.backtest.status())

    @app.post("/api/backtest/run")
    def backtest_run():
        started = app.state.backtest.start(_backtest_path())
        return JSONResponse(jsonable({**app.state.backtest.status(), "started": started}), status_code=202)

    # ---- settings
    @app.get("/api/settings")
    def get_settings():
        return ok(settings_mod.public())

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        patch = body.model_dump(exclude_unset=True)
        if "scheduler" in patch and patch["scheduler"] is not None:
            patch["scheduler"] = {k: v for k, v in patch["scheduler"].items() if v is not None}
        s = settings_mod.update(patch)
        if app.state.scheduler_allowed and s["scheduler"]["enabled"] and not sched.running:
            sched.start()
        return ok(settings_mod.public(s))

    # ---- scheduler
    @app.get("/api/scheduler")
    def scheduler_status():
        return ok(sched.status())

    @app.post("/api/scheduler/run")
    def scheduler_run():
        return ok(sched.tick(force=True))

    # ---- frontend
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                   include_in_schema=False)
    def api_not_found(rest: str):
        return JSONResponse({"detail": f"Not found: /api/{rest}"}, status_code=404)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        index = dist / "index.html"
        if not index.exists():
            return HTMLResponse(BUILD_HINT)
        if path:
            root = dist.resolve()
            f = (dist / path).resolve()
            if f.is_file() and f.is_relative_to(root):
                return FileResponse(f)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
