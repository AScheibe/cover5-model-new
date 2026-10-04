"""Fetch current market spreads.

Both providers return a list of MarketLine with ``home_spread`` in sportsbook
convention (negative = home favored). Parsing is separated from fetching so it
can be unit tested on saved JSON.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from cover5 import config
from cover5.teams import normalize


@dataclass
class MarketLine:
    away: str
    home: str
    kickoff_utc: datetime
    home_spread: float
    books: int
    source: str
    fetched_at: datetime

    def to_row(self) -> dict:
        d = asdict(self)
        d["kickoff_utc"] = self.kickoff_utc.isoformat()
        d["fetched_at"] = self.fetched_at.isoformat()
        return d


# --------------------------------------------------------------------------- Odds API
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


def fetch_oddsapi(api_key: str | None = None) -> list[MarketLine]:
    key = api_key or config.ODDS_API_KEY
    if not key:
        raise RuntimeError("ODDS_API_KEY is not set")
    r = requests.get(
        ODDS_API_URL,
        params={"apiKey": key, "regions": "us", "markets": "spreads", "oddsFormat": "american"},
        timeout=30,
    )
    r.raise_for_status()
    remaining = r.headers.get("x-requests-remaining")
    if remaining is not None:
        print(f"[oddsapi] requests remaining this month: {remaining}")
    return parse_oddsapi(r.json())


def parse_oddsapi(payload: list[dict], now: datetime | None = None) -> list[MarketLine]:
    now = now or datetime.now(timezone.utc)
    out: list[MarketLine] = []
    for ev in payload:
        try:
            home = normalize(ev["home_team"])
            away = normalize(ev["away_team"])
        except ValueError:
            continue
        points: list[float] = []
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "spreads":
                    continue
                for oc in mk.get("outcomes", []):
                    if oc.get("point") is None:
                        continue
                    try:
                        if normalize(oc["name"]) == home:
                            points.append(float(oc["point"]))
                    except ValueError:
                        pass
        if not points:
            continue
        kick = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        out.append(MarketLine(away, home, kick, statistics.median(points), len(points), "oddsapi", now))
    return out


# --------------------------------------------------------------------------- ESPN
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def fetch_espn(week: int | None = None, season: int | None = None) -> list[MarketLine]:
    params = {}
    if week is not None:
        params["week"] = week
    if season is not None:
        params["dates"] = season
        params["seasontype"] = 2
    r = requests.get(ESPN_URL, params=params, timeout=30)
    r.raise_for_status()
    return parse_espn(r.json())


def parse_espn(payload: dict, now: datetime | None = None) -> list[MarketLine]:
    now = now or datetime.now(timezone.utc)
    out: list[MarketLine] = []
    for ev in payload.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c["homeAway"]: c for c in comp.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue
        try:
            home = normalize(teams["home"]["team"]["abbreviation"])
            away = normalize(teams["away"]["team"]["abbreviation"])
        except ValueError:
            continue
        odds = comp.get("odds") or []
        if not odds:
            continue
        o = odds[0]
        hs = _espn_home_spread(o, home, away)
        if hs is None:
            continue
        kick = datetime.fromisoformat(comp["date"].replace("Z", "+00:00"))
        out.append(MarketLine(away, home, kick, hs, 1, "espn", now))
    return out


def _espn_home_spread(o: dict, home: str, away: str) -> float | None:
    """ESPN gives 'details' like 'KC -3.5' or 'EVEN'. Prefer that; fall back to
    the per-team spread fields when present."""
    details = (o.get("details") or "").strip()
    if details.upper() in ("EVEN", "PK", "PICK"):
        return 0.0
    if details:
        parts = details.split()
        if len(parts) == 2:
            try:
                fav = normalize(parts[0])
                num = float(parts[1])
                if fav == home:
                    return num          # 'KC -3.5' with KC home -> home_spread -3.5
                if fav == away:
                    return -num
            except ValueError:
                pass
    hto = o.get("homeTeamOdds") or {}
    if hto.get("spread") is not None:
        return float(hto["spread"])
    if o.get("spread") is not None:
        return float(o["spread"])
    return None


# --------------------------------------------------------------------------- file
def market_file_path() -> Path:
    return Path(config.MARKET_FILE) if config.MARKET_FILE else config.DATA / "market.json"


def fetch_file(path: str | Path | None = None) -> list[MarketLine]:
    """Read market lines from a local JSON file (offline demos, end-to-end tests)."""
    p = Path(path) if path else market_file_path()
    if not p.exists():
        raise RuntimeError(f"market file {p} not found (set COVER5_MARKET_FILE)")
    try:
        payload = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"market file {p} is not valid JSON: {e}") from e
    return parse_file(payload)


def parse_file(payload, now: datetime | None = None) -> list[MarketLine]:
    """``[{away, home, kickoff_utc, home_spread, books?, source?}]``; rows without a spread are skipped."""
    now = now or datetime.now(timezone.utc)
    if isinstance(payload, dict):
        payload = payload.get("lines", [])
    out: list[MarketLine] = []
    for i, row in enumerate(payload):
        try:
            away, home = normalize(row["away"]), normalize(row["home"])
            if row.get("home_spread") is None:
                continue
            kick = datetime.fromisoformat(str(row["kickoff_utc"]).replace("Z", "+00:00"))
            if kick.tzinfo is None:
                kick = kick.replace(tzinfo=timezone.utc)
            out.append(MarketLine(away, home, kick, float(row["home_spread"]), int(row.get("books") or 1),
                                  str(row.get("source") or "file"), now))
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"market file row {i}: {e!r}") from e
    return out


# --------------------------------------------------------------------------- dispatch
def fetch_market(provider: str | None = None, **kw) -> list[MarketLine]:
    provider = provider or config.PROVIDER
    if provider == "oddsapi":
        return fetch_oddsapi()
    if provider == "espn":
        return fetch_espn(**kw)
    if provider == "file":
        return fetch_file()
    raise ValueError(f"unknown provider {provider!r}")
