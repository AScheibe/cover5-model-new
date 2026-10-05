"""Every tracker operation in one place, shared by the CLI, the local API and the scheduler.

Operations return an ``Outcome``: the week as structured data (``view``), the
alert that was produced (if picks were recomputed), and human readable
messages. User mistakes raise ``UserError`` so each front end can report them
its own way (exit code, HTTP 400).

All mutating operations take ``LOCK`` so the API and the background scheduler
never interleave writes to the same week's files.
"""
from __future__ import annotations

import math
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from cover5 import alerts, config
from cover5 import history
from cover5 import overrides as ovr
from cover5 import state as st
from cover5.league import build_week_file, league_path, load_week_file
from cover5.picks import (Pick, apply_locks, build_board, diff_picks, latest_snapshot_market,
                          lock_time_market, recommend)
from cover5.providers import MarketLine, fetch_market, redact
from cover5.schedule import current_week, load_games, week_slate
from cover5.scoring import AWAY, HOME, fmt_pick
from cover5.tally import summarize, summary_line, tally_picks
from cover5.teams import normalize

LOCK = threading.RLock()

KIND_ALIASES = {"line": "lines", "lines": "lines", "lock": "locks", "locks": "locks",
                "score": "results", "result": "results", "results": "results",
                "points": "points", "point": "points"}


class UserError(Exception):
    """A request the user can fix: unknown team, two picks in one game, no league file..."""


class ProviderError(UserError):
    """The odds provider failed (network, bad key, unreadable market file). HTTP 502 in the API."""


def _fetch(provider: str | None):
    try:
        return fetch_market(provider)
    except Exception as e:  # noqa: BLE001
        # ``from None``: the original error can carry the odds API key in its URL.
        raise ProviderError(f"Market fetch failed: {redact(e)}") from None


@dataclass
class Outcome:
    view: dict | None = None
    alert: dict | None = None          # {"title", "body", "changed", "sent"}
    messages: list[str] = field(default_factory=list)
    recomputed: bool = False           # picks were recomputed from logged lines (not part of the JSON)

    def to_dict(self) -> dict:
        return {"week": self.view, "alert": self.alert, "messages": self.messages}


@dataclass
class Computed:
    season: int
    week: int
    now: datetime
    league: pd.DataFrame
    ov: dict
    board: pd.DataFrame
    current: list[Pick]
    picks: list[Pick]
    tallies: list
    diff: dict
    warnings: list[str]
    title: str
    body: str
    market_source: dict
    saved: bool = False                # written as the presumed picks: nothing is pending any more


# --------------------------------------------------------------------------- lookups
_games_cache: dict = {"mtime": None, "df": None}


def games(force: bool = False) -> pd.DataFrame:
    """nflverse schedule, re-parsed only when the cached CSV changes."""
    df = load_games(force=force)
    path = config.CACHE_DIR / "games.csv"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if _games_cache["df"] is None or _games_cache["mtime"] != mtime or force:
        _games_cache.update(mtime=mtime, df=df)
    return _games_cache["df"]


def resolve_week(season: int | None, week: int | None, g: pd.DataFrame | None = None) -> tuple[int, int]:
    if season and week:
        return season, week
    s, w = current_week(g if g is not None else games())
    return (season or s), (week or w)


def parse_spread(s) -> float:
    if isinstance(s, (int, float)):
        return finite(s, "spread", limit=60)
    s = str(s).strip()
    if s.upper() in ("PK", "EVEN", "PICK"):
        return 0.0
    try:
        return finite(s, "spread", limit=60)
    except UserError as e:
        raise UserError(f"not a spread: {s!r} (use a number like -3.5 or PK)") from e


def finite(x, what: str, limit: float = 200) -> float:
    """A real, finite number within +-limit, or UserError."""
    try:
        v = float(x)
    except (TypeError, ValueError) as e:
        raise UserError(f"{what} must be a number") from e
    if not math.isfinite(v) or abs(v) > limit:
        raise UserError(f"{what} must be a finite number between -{limit:g} and {limit:g}")
    return v


def _load_league(season: int, week: int) -> pd.DataFrame:
    try:
        return load_week_file(season, week)
    except FileNotFoundError as e:
        raise UserError(f"No league lines for {season} week {week} yet. Initialise the week first.") from e


def team_game(league: pd.DataFrame, team: str):
    """(row, canonical team, side) for the game a team plays in this week."""
    try:
        t = normalize(team)
    except ValueError as e:
        raise UserError(f"unknown team {team!r}") from e
    row = league[(league.home == t) | (league.away == t)]
    if row.empty:
        raise UserError(f"{t} is not on this week's slate")
    r = row.iloc[0]
    return r, t, (HOME if r.home == t else AWAY)


def _week_results(g: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    return g[(g.season == season) & (g.week == week)].set_index("game_id")


def drop_mismatched_points(ov: dict, picks: list[Pick]) -> dict:
    """A points override only grades the pick it was entered for."""
    teams = {p.game_id: p.team for p in picks}
    pts = {gid: v for gid, v in ov.get("points", {}).items()
           if not v.get("team") or teams.get(gid) == v["team"]}
    return {**ov, "points": pts}


def _warnings(picks: list[Pick], ov: dict, league: pd.DataFrame) -> list[str]:
    w = []
    locked = [p for p in picks if p.locked]
    if len(locked) > config.N_PICKS:
        w.append(f"{len(locked)} picks are locked but the league allows {config.N_PICKS}. "
                 f"Set your picks to the {config.N_PICKS} teams you actually have.")
    if len(picks) < config.N_PICKS:
        w.append(f"only {len(picks)} pick(s) available; the remaining games have kicked off or have no line.")
    gids = set(league.game_id)
    for kind in ovr.KINDS:
        for gid in ov.get(kind, {}):
            if gid not in gids:
                w.append(f"{kind} override for {gid} does not match a game this week")
    picked = {p.game_id: p for p in picks}
    lg = league.set_index("game_id")
    for gid, p in picked.items():
        if gid in lg.index and pd.isna(lg.loc[gid, "home_spread"]) and gid in ov.get("results", {}):
            r = lg.loc[gid]
            w.append(f"a score is recorded for {r.away}@{r.home} but there is no league line, so it "
                     f"can't be graded; set the line for {p.team}")
    for gid, pt in ov.get("points", {}).items():
        p = picked.get(gid)
        if p is None:
            w.append(f"points override for {pt.get('team', gid)} ignored: that game is not one of your picks")
        elif pt.get("team") and pt["team"] != p.team:
            w.append(f"points override was entered for {pt['team']} but your pick in that game is {p.team}")
    return w


# --------------------------------------------------------------------------- core recompute
def compute(season: int, week: int, market: list | None = None, *, reason: str,
            alert: bool = False, save: bool = False, force_alert: bool = False,
            now: datetime | None = None, echo: bool = False,
            notes: list[str] | None = None) -> tuple[Computed, dict | None]:
    """Recompute picks from overrides + market. ``market=None`` uses the last logged lines.

    Returns the computation and, when ``alert`` is set, the alert dict.
    ``save`` writes the recommendation as the presumed current picks and records
    the run in history. ``echo`` prints the alert (CLI). ``notes`` are extra
    warnings to carry in the alert (e.g. an assumption an override forced).

    A fetch that is missing a game (a book pulled the line) is topped up with
    that game's last logged line, so a market update and an offline recompute
    after an override always value every game the same way.
    """
    now = now or datetime.now(timezone.utc)
    g = games()
    ov = ovr.load_overrides(season, week)
    league = ovr.apply_line_overrides(_load_league(season, week), ov)
    snaps = st.load_snapshots(season, week)
    if market is None:
        market = latest_snapshot_market(snaps)
        market_source = {"kind": "snapshot", "fetched_at": _latest_fetch(snaps)}
    else:
        market_source = {"kind": "live", "fetched_at": now.isoformat(timespec="seconds")}
        merged = {(m.away, m.home): MarketLine(m.away, m.home, m.kickoff_utc, m.home_spread, m.books,
                                               f"{m.source}@last", m.fetched_at)
                  for m in latest_snapshot_market(snaps)}
        merged.update({(m.away, m.home): m for m in market})
        market = list(merged.values())
    market = lock_time_market(market, snaps, league, now)
    s = st.load_state(season, week)
    current = apply_locks(st.picks_from_state(s), ov["locks"])
    board = build_board(league, market, now)
    picks = recommend(board, current, n=config.N_PICKS,
                      swap_margin=config.SWAP_MARGIN, flip_margin=config.FLIP_MARGIN)
    # Diff against the presumed picks with your own locks already applied, so the
    # alert only lists changes the model wants, not the ones you just made.
    diff = diff_picks(current, picks)
    tallies = tally_picks(picks, league, drop_mismatched_points(ov, picks), _week_results(g, season, week))
    warnings = _warnings(picks, ov, league) + list(notes or [])
    title, body = alerts.format_alert(season, week, picks, diff, board, tallies=tallies,
                                      warnings=warnings, reason=reason)
    c = Computed(season, week, now, league, ov, board, current, picks, tallies, diff, warnings,
                 title, body, market_source, saved=save)
    alert_info = None
    if alert:
        sent, failures = alerts.deliver(title, body, force=force_alert, changed=diff["changed"], echo=echo)
        alert_info = {"title": title, "body": body, "changed": diff["changed"], "sent": sent,
                      "failures": failures}
        # The run (and the view) say when the push did not reach the phone.
        c.warnings = warnings = warnings + [f"Alert not delivered: {f}" for f in failures]
    elif echo:
        print(title)
        print(body)
    if save:
        s["recommended"] = st.picks_to_state(picks)
        s["last_run"] = st.now_iso()
        summ = summarize(tallies)
        s.setdefault("history", []).append({
            "at": s["last_run"], "reason": reason, "changed": diff["changed"],
            "picks": [f"{p.team}:{p.edge:+.1f}{':L' if p.locked else ''}" for p in picks],
            "projected": round(summ["projected"], 2)})
        st.save_state(season, week, s)
        view = build_view(c)
        history.record_run(season, week, at=s["last_run"], reason=reason, changed=diff["changed"],
                           diff=_diff_json(diff), picks=view["picks"], summary=view["summary"],
                           warnings=warnings, title=title, body=body,
                           sent=bool(alert_info and alert_info["sent"]))
        history.upsert_week(view)
    return c, alert_info


def _diff_json(d: dict) -> dict:
    return {"changed": d["changed"], "added": [p.team for p in d["added"]],
            "dropped": [p.team for p in d["dropped"]], "flipped": [p.team for p in d["flipped"]]}


def _todo(c: Computed, s: dict) -> dict:
    """What to change in the league app: the model's picks versus the ones you last confirmed.

    Every change since you last said what you hold, netted out (an add that a
    later run dropped again never shows), for games that haven't kicked off.
    Before you confirm anything this week, the baseline is an empty set: add
    all of the model's picks.
    """
    conf = st.confirmed_from_state(s)
    d = diff_picks(conf or [], c.picks)
    started = {r.game_id for r in c.board.itertuples() if r.locked}
    teams = lambda ps: [p.team for p in ps if p.game_id not in started]   # noqa: E731
    out = {"added": teams(d["added"]), "dropped": teams(d["dropped"]), "flipped": teams(d["flipped"])}
    return {"changed": any(out.values()), **out,
            "since": (s.get("confirmed") or {}).get("at") if conf is not None else None,
            "confirmed": [p.team for p in conf] if conf is not None else None}


def _latest_fetch(snaps: pd.DataFrame | None) -> str | None:
    if snaps is None or snaps.empty:
        return None
    return str(pd.to_datetime(snaps["fetched_at"], utc=True, format="ISO8601").max().isoformat())


def _f(x):
    """JSON-safe float: NaN -> None."""
    if x is None:
        return None
    try:
        return None if pd.isna(x) else float(x)
    except (TypeError, ValueError):
        return None


def _team_view(home_spread, is_home: bool):
    v = _f(home_spread)
    return None if v is None else (v if is_home else -v)


def build_view(c: Computed) -> dict:
    """The week as JSON-ready data. Shapes are documented in docs/api.md."""
    s = st.load_state(c.season, c.week)
    lg = c.league.set_index("game_id")
    g = games()
    res = _week_results(g, c.season, c.week)
    picked = {p.game_id: p for p in c.picks}
    tally_by = {t.pick.game_id: t for t in c.tallies}

    games_out = []
    for r in c.board.sort_values("kickoff_utc").itertuples():
        result = None
        o = c.ov["results"].get(r.game_id)
        if o is not None:
            result = {"home_score": _f(o["home_score"]), "away_score": _f(o["away_score"]),
                      "final": bool(o.get("final", True)), "source": "score override"}
        elif r.game_id in res.index and pd.notna(res.loc[r.game_id, "result"]):
            result = {"home_score": _f(res.loc[r.game_id, "home_score"]),
                      "away_score": _f(res.loc[r.game_id, "away_score"]),
                      "final": True, "source": "nflverse"}
        p = picked.get(r.game_id)
        games_out.append({
            "game_id": r.game_id, "away": r.away, "home": r.home,
            "kickoff_utc": pd.Timestamp(r.kickoff_utc).isoformat(),
            "locked": bool(r.locked),
            "league_home_spread": _f(r.league_home_spread),
            "sheet_home_spread": _f(lg.loc[r.game_id, "sheet_home_spread"]),
            "line_overridden": bool(r.line_overridden),
            "market_home_spread": _f(r.market_home_spread),
            "books": int(r.books or 0),
            "best_side": r.best_side if _f(r.edge) is not None else None,
            "best_team": r.best_team if _f(r.edge) is not None else None,
            "edge": _f(r.edge),
            "result": result,
            "picked_side": p.side if p else None,
            "picked_team": p.team if p else None,
            "lock": c.ov["locks"].get(r.game_id),
        })

    picks_out = []
    for p in c.picks:
        r = c.board[c.board.game_id == p.game_id].iloc[0]
        is_home = p.side == HOME
        opp = r.away if is_home else r.home
        t = tally_by.get(p.game_id)
        picks_out.append({
            "game_id": p.game_id, "team": p.team, "opponent": opp, "side": p.side, "is_home": is_home,
            "league_spread": _team_view(r.league_home_spread, is_home),
            "market_spread": _team_view(r.market_home_spread, is_home),
            "label": fmt_pick(p.team, opp, r.league_home_spread, is_home),
            "kickoff_utc": pd.Timestamp(r.kickoff_utc).isoformat(),
            "edge": _f(p.edge) or 0.0, "locked": p.locked, "manual": p.manual,
            "status": t.status if t else "open", "points": _f(t.points) if t else 0.0,
            "source": t.source if t else "edge",
            "line_overridden": bool(r.line_overridden),
        })

    summ = summarize(c.tallies)
    team_of = {r.game_id: (r.away, r.home) for r in c.board.itertuples()}

    def ov_list(kind):
        out = []
        for gid, v in sorted(c.ov.get(kind, {}).items()):
            away, home = team_of.get(gid, (None, None))
            out.append({"game_id": gid, "away": away, "home": home, "value": v})
        return out

    return {
        "season": c.season, "week": c.week,
        "now": c.now.isoformat(timespec="seconds"),
        "has_league_file": True,
        "market": c.market_source,
        "last_run": s.get("last_run"),
        "n_picks": config.N_PICKS,
        "games": games_out,
        "picks": picks_out,
        "summary": {**{k: (round(v, 2) if isinstance(v, float) else v) for k, v in summ.items()},
                    "line": summary_line(summ)},
        # Changes the model would make to the presumed picks. Empty right after a
        # saved run: those picks are now the presumed ones (``todo`` is what to do).
        "diff": _diff_json(c.diff if not c.saved else {"changed": False, "added": [], "dropped": [], "flipped": []}),
        "todo": _todo(c, s),
        "warnings": c.warnings,
        "overrides": {k: ov_list(k) for k in ovr.KINDS},
        "last_change": history.last_change(c.season, c.week),
    }


def week_view(season: int, week: int, now: datetime | None = None) -> dict:
    """Read-only view of a week from the last logged lines. Never writes."""
    if not league_path(season, week).exists():
        return {"season": season, "week": week, "has_league_file": False,
                "now": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    c, _ = compute(season, week, None, reason="view", now=now)
    return build_view(c)


def _all_kicked_off(league: pd.DataFrame, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return len(league) > 0 and all(now >= k for k in league.kickoff_utc)


def _after_change(season: int, week: int, reason: str, recompute: bool, echo: bool,
                  messages: list[str], notes: list[str] | None = None) -> Outcome:
    """Recompute from the last logged lines after an override, if there are any.

    Either way the week's history record is brought up to date.
    """
    out = Outcome(messages=messages)
    if recompute and st.load_snapshots(season, week) is not None:
        c, out.alert = compute(season, week, None, reason=reason, alert=True, save=True, echo=echo,
                               notes=notes)
        out.view = build_view(c)
        out.recomputed = True
    else:
        if recompute:
            if _all_kicked_off(_load_league(season, week)):
                out.messages.append("No market lines were logged for this week, so picks were not "
                                    "recomputed; every game has kicked off, so the picks stand as entered.")
            else:
                out.messages.append("No market lines logged yet this week, so picks were not "
                                    "recomputed. Fetch lines to update.")
        out.view = week_view(season, week)
        history.upsert_week(out.view)         # compute(save=True) does this on the other branch
    return out


def _week_market(market: list[MarketLine], games: list[tuple[str, str, datetime]]) -> list[MarketLine]:
    """Only the lines for this week's games: same teams, kickoff within a few days.

    A feed returns whatever is on the board right now, which is another week's
    slate when you fetch for a past or future week. Those lines must never be
    logged as this week's snapshots.
    """
    kicks = {(a, h): k for a, h, k in games}
    out = []
    for m in market:
        k = kicks.get((m.away, m.home))
        if k is None:
            continue
        if abs((pd.Timestamp(m.kickoff_utc) - pd.Timestamp(k)).total_seconds()) > 3.5 * 86400:
            continue
        out.append(m)
    return out


def _backup_sheet(season: int, week: int, now: datetime) -> str | None:
    path = league_path(season, week)
    if not path.exists():
        return None
    d = config.LEAGUE_DIR / "backup"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{config.week_key(season, week)}-{now:%Y%m%dT%H%M%SZ}.csv"
    shutil.copy2(path, dest)
    return str(dest.relative_to(config.DATA))


# --------------------------------------------------------------------------- operations
def init_week(season: int, week: int, *, overwrite: bool = False, use_market: bool = True,
              provider: str | None = None, now: datetime | None = None) -> Outcome:
    """Seed the week's league sheet. A re-seed (``overwrite``) never changes a frozen line
    of a game that has kicked off, keeps the sheet's line for games the market lacks,
    and saves the previous sheet under data/league_lines/backup/ first."""
    if not overwrite and league_path(season, week).exists():
        raise UserError(f"{season} week {week} already has league lines; overwrite to replace them")
    # Fetch outside LOCK so a slow provider never blocks reads of the week.
    market, msgs = None, []
    if use_market:
        try:
            market = fetch_market(provider)
        except Exception as e:  # noqa: BLE001
            msgs.append(f"Market fetch failed ({redact(e)}); seeded from nflverse spreads only.")
    with LOCK:
        now = now or datetime.now(timezone.utc)
        g = games()
        slate = week_slate(g, season, week)
        if market is not None:
            market = _week_market(market, list(zip(slate.away_team, slate.home_team, slate.kickoff_utc)))
        keep, backup, n_started, n_missing = {}, None, 0, 0
        if overwrite and league_path(season, week).exists():
            old = load_week_file(season, week)
            pairs = {(m.away, m.home) for m in (market or [])}
            for r in old.itertuples():
                if pd.isna(r.home_spread):
                    continue
                if now >= r.kickoff_utc:
                    keep[r.game_id] = float(r.home_spread)
                    n_started += 1
                elif market is not None and (r.away, r.home) not in pairs:
                    keep[r.game_id] = float(r.home_spread)
                    n_missing += 1
            backup = _backup_sheet(season, week, now)
        try:
            df = build_week_file(g, season, week, market, overwrite=overwrite, keep=keep)
        except FileExistsError as e:
            raise UserError(f"{season} week {week} already has league lines; overwrite to replace them") from e
        if df.empty:
            league_path(season, week).unlink(missing_ok=True)
            raise UserError(f"No regular season games found for {season} week {week}")
        msgs.append(f"Seeded {len(df)} games for {season} week {week}. "
                    "Correct any line that differs from the league app.")
        if n_started:
            msgs.append(f"Kept the frozen league line for {n_started} game(s) that have kicked off.")
        if n_missing:
            msgs.append(f"Kept the sheet's line for {n_missing} game(s) the market has no line for.")
        if backup:
            msgs.append(f"The previous sheet is saved as {backup}.")
        if market:
            st.append_snapshot(season, week, market)
        if ovr.load_overrides(season, week)["lines"]:
            msgs.append("Existing line overrides still apply on top of the new sheet.")
        out = Outcome(messages=msgs, view=week_view(season, week))
        history.upsert_week(out.view, only_if_changed=True)    # a re-seed can regrade the week
        return out


def update(season: int, week: int, *, provider: str | None = None, force_alert: bool = False,
           echo: bool = False) -> Outcome:
    with LOCK:
        league = _load_league(season, week)      # fail early with a clear message
        if _all_kicked_off(league):
            raise UserError(f"Every {season} week {week} game has kicked off, so there are no lines left to "
                            "fetch. Scores and the week's record still update from nflverse.")
    market = _fetch(provider)                    # outside LOCK: a slow provider never blocks reads
    with LOCK:
        league = _load_league(season, week)
        mine = _week_market(market, list(zip(league.away, league.home, league.kickoff_utc)))
        msgs = [f"Fetched {len(mine)} market lines."]
        if not mine:
            msgs.append(f"The market has no lines for {season} week {week}'s games right now "
                        f"({len(market)} lines for other games were ignored).")
        else:
            st.append_snapshot(season, week, mine)
        c, alert = compute(season, week, mine, reason="market update", alert=True, save=True,
                           force_alert=force_alert, echo=echo)
        return Outcome(view=build_view(c), alert=alert, messages=msgs, recomputed=True)


ET = ZoneInfo("America/New_York")


def _effective_picks(season: int, week: int, ov: dict) -> list[Pick]:
    """The picks the tracker believes you hold, with your locks applied."""
    return apply_locks(st.picks_from_state(st.load_state(season, week)), ov["locks"])


def _set_state_pick(season: int, week: int, game_id: str, pick: Pick | None,
                    confirmed: bool = False) -> None:
    """Rewrite the presumed pick for one game (None removes it).

    ``confirmed`` makes the same change to the picks you confirmed, if you have.
    """
    s = st.load_state(season, week)
    recs = [p for p in st.picks_from_state(s) if p.game_id != game_id]
    if pick is not None:
        recs.append(Pick(pick.game_id, pick.side, pick.team, pick.edge))
    s["recommended"] = st.picks_to_state(recs)
    if confirmed and st.confirmed_from_state(s) is not None:
        st.confirm_game(s, game_id, pick)
    st.save_state(season, week, s)


def _confirm_game(season: int, week: int, game_id: str, pick: Pick) -> None:
    """You told the tracker you hold ``pick`` (by locking it or entering its points)."""
    s = st.load_state(season, week)
    st.confirm_game(s, game_id, pick)
    st.save_state(season, week, s)


def _require_kicked_off(r, week: int, force: bool) -> None:
    """Scores only exist once a game starts; catches overrides typed into the wrong week."""
    if force or datetime.now(timezone.utc) >= r.kickoff_utc:
        return
    k = pd.Timestamp(r.kickoff_utc).tz_convert(ET)
    hint = f" If you meant last week's game, enter it in week {week - 1}." if week > 1 else ""
    raise UserError(f"{r.away}@{r.home} hasn't kicked off yet (kickoff {k:%a %b %d %I:%M %p} ET), "
                    f"so it has no score to enter.{hint}")


def _entered_vs_model(chosen: list[Pick], out: Outcome) -> list[str]:
    """Say what you entered and, if the model's picks differ, exactly how."""
    entered = ", ".join(p.team for p in chosen) or "none"
    view = out.view or {}
    got = {p["game_id"]: p for p in view.get("picks", [])}
    if not out.recomputed:
        msgs = [f"Your picks are now: {entered}"]
        empty = config.N_PICKS - len(chosen)
        if empty > 0:
            open_games = [g for g in view.get("games", []) if not g["locked"]
                          and g["game_id"] not in {p.game_id for p in chosen}]
            msgs.append(f"{empty} open slot(s); the model will suggest teams for them once lines are fetched."
                        if open_games else
                        f"{empty} slot(s) stay empty: every other game has kicked off (an empty slot scores 0).")
        return msgs
    chosen_ids = {p.game_id for p in chosen}
    switched = [(p.team, got[p.game_id]) for p in chosen if p.game_id in got and got[p.game_id]["team"] != p.team]
    dropped = [p.team for p in chosen if p.game_id not in got]
    added = [g for gid, g in got.items() if gid not in chosen_ids]
    if not (switched or dropped or added):
        msgs = [f"Your picks are now: {entered}"]
        if len(got) < config.N_PICKS:
            msgs.append(f"{config.N_PICKS - len(got)} slot(s) stay empty: no game that hasn't kicked off "
                        "is left to fill them (an empty slot scores 0).")
        return msgs
    msgs = [f"You entered: {entered}."]
    for team, g in switched:
        msgs.append(f"The model would switch {team} to {g['team']} (edge {g['edge']:+.1f}). "
                    f"Lock {team} to keep it.")
    adds = ", ".join(f"{g['team']} (edge {g['edge']:+.1f})" for g in added)
    if dropped and added:
        msgs.append(f"The model would replace {', '.join(dropped)} with {adds}. Lock a team to keep it.")
    elif dropped:
        msgs.append(f"The model would drop {', '.join(dropped)}. Lock a team to keep it.")
    elif added:
        msgs.append(f"The model suggests {adds} for the open slot(s).")
    msgs.append("The model's picks are now: " + ", ".join(g["team"] for g in got.values())
                + ". \"Do this\" lists the changes to make in the app.")
    return msgs


def set_picks(season: int, week: int, teams: list[str], *, lock: list[str] | None = None,
              recompute: bool = True, echo: bool = False) -> Outcome:
    """Tell the tracker the picks you actually entered in the league app.

    They become your confirmed picks and the presumed picks, so any lock on a
    game you didn't list (or on the other side of a game you did) is removed.
    The model then recomputes from them and may suggest changes, which the
    messages spell out and ``todo`` lists. Teams in ``lock`` are locked too, so
    the model keeps them (the web app locks a team you pick by hand).
    """
    with LOCK:
        league = _load_league(season, week)
        if len(teams) > config.N_PICKS:
            raise UserError(f"{len(teams)} teams given; the league allows {config.N_PICKS}")
        chosen, seen = [], set()
        for team in teams:
            r, t, side = team_game(league, team)
            if r.game_id in seen:
                raise UserError(f"two picks in {r.away}@{r.home}; pick one side per game")
            seen.add(r.game_id)
            chosen.append(Pick(r.game_id, side, t, 0.0))
        by_game = {p.game_id: p for p in chosen}
        to_lock = []
        for team in lock or []:
            r, t, side = team_game(league, team)
            p = by_game.get(r.game_id)
            if p is None or p.team != t:
                raise UserError(f"{t} isn't in the picks you entered, so it can't be locked")
            to_lock.append(p)
        ov = ovr.load_overrides(season, week)
        msgs = []
        for gid, lk in list(ov["locks"].items()):
            p = by_game.get(gid)
            if p is None or p.side != lk["side"]:
                del ov["locks"][gid]
                msgs.append(f"Removed your lock on {lk['team']} (it isn't in the picks you entered).")
        for p in to_lock:
            if p.game_id not in ov["locks"]:
                ov["locks"][p.game_id] = {"team": p.team, "side": p.side, "replaced": None}
                msgs.append(f"Locked {p.team}, so the model keeps it. Unlock it to let the model manage that pick.")
        ovr.save_overrides(season, week, ov)
        s = st.load_state(season, week)
        s["recommended"] = st.picks_to_state(chosen)
        st.set_confirmed(s, chosen, at=st.now_iso())
        st.save_state(season, week, s)
        out = _after_change(season, week, "after you set your picks", recompute, echo, msgs)
        out.messages[:0] = _entered_vs_model(chosen, out)
        return out


def confirm_picks(season: int, week: int) -> Outcome:
    """You made the suggested changes in the league app: the model's picks are now your confirmed picks."""
    with LOCK:
        _load_league(season, week)
        view = week_view(season, week)
        picks = [Pick(p["game_id"], p["side"], p["team"], 0.0) for p in view["picks"]]
        s = st.load_state(season, week)
        st.set_confirmed(s, picks, at=st.now_iso())
        st.save_state(season, week, s)
        teams = ", ".join(p.team for p in picks) or "none"
        return Outcome(view=week_view(season, week),
                       messages=[f"Noted: your picks in the app are {teams}."])


def lock(season: int, week: int, teams: list[str], *, recompute: bool = True, echo: bool = False) -> Outcome:
    """Pin picks. A locked pick fills a slot and is never swapped or flipped."""
    with LOCK:
        league = _load_league(season, week)
        resolved, seen = [], set()
        for team in teams:
            r, t, side = team_game(league, team)
            if r.game_id in seen:
                raise UserError(f"two picks in {r.away}@{r.home}; pick one side per game")
            seen.add(r.game_id)
            resolved.append((r, t, side))
        ov = ovr.load_overrides(season, week)
        current = {p.game_id: p for p in _effective_picks(season, week, ov)}
        now = datetime.now(timezone.utc)
        # Validate everything before writing anything.
        count = len(current)
        for r, t, side in resolved:
            if r.game_id not in current:
                if count >= config.N_PICKS and now >= r.kickoff_utc:
                    raise UserError(
                        f"{t}'s game has kicked off and {t} isn't one of your {config.N_PICKS} picks, so the "
                        f"tracker can't tell which pick it replaced. Set your picks to the {config.N_PICKS} "
                        f"you actually have instead.")
                count += 1
        msgs, notes = [], []
        count = len(current)
        for r, t, side in resolved:
            cur = current.get(r.game_id)
            ov["locks"][r.game_id] = {"team": t, "side": side,
                                      "replaced": ({"team": cur.team, "side": cur.side}
                                                   if cur is not None and cur.team != t else None)}
            if cur is None:
                if count >= config.N_PICKS:
                    note = (f"{t} wasn't one of your picks, so the weakest open pick is dropped to make room. "
                            f"If you already replaced a different team in the app, set your picks to the "
                            f"{config.N_PICKS} you actually have.")
                    msgs.append(f"Locked {t}. {note}")
                    notes.append(note)
                else:
                    msgs.append(f"Locked {t} (fills an open slot).")
                count += 1
            elif cur.team != t:
                msgs.append(f"Locked {t} (replaces {cur.team} in that game).")
            else:
                msgs.append(f"Locked {t}.")
        ovr.save_overrides(season, week, ov)
        for r, t, side in resolved:
            _confirm_game(season, week, r.game_id, Pick(r.game_id, side, t, 0.0))
        return _after_change(season, week, "after you locked a pick", recompute, echo, msgs, notes)


def unlock(season: int, week: int, teams: list[str], *, recompute: bool = True, echo: bool = False) -> Outcome:
    """Stop pinning picks. They stay your picks until the model suggests otherwise."""
    with LOCK:
        league = _load_league(season, week)
        ov = ovr.load_overrides(season, week)
        msgs = []
        for team in teams:
            r, t, _ = team_game(league, team)
            lk = ov["locks"].get(r.game_id)
            if lk and ovr.clear(ov, r.game_id, ("locks",)):
                _set_state_pick(season, week, r.game_id, Pick(r.game_id, lk["side"], lk["team"], 0.0))
                started = datetime.now(timezone.utc) >= r.kickoff_utc
                msgs.append(f"Unlocked {lk['team']}. It stays your pick until the model suggests otherwise"
                            + (" (it has kicked off, so it stays locked)." if started else "."))
            else:
                msgs.append(f"No lock on {r.away}@{r.home}")
        ovr.save_overrides(season, week, ov)
        return _after_change(season, week, "after you unlocked a pick", recompute, echo, msgs)


def set_line(season: int, week: int, team: str, spread, *, recompute: bool = True,
             echo: bool = False) -> Outcome:
    """`set_line(.., 'IND', -3.5)` means IND is favored by 3.5 whether home or away."""
    with LOCK:
        league = _load_league(season, week)
        r, t, side = team_game(league, team)
        sp = parse_spread(spread)
        home_spread = sp if side == HOME else -sp
        ov = ovr.load_overrides(season, week)
        ov["lines"][r.game_id] = home_spread
        ovr.save_overrides(season, week, ov)
        opp = r.away if side == HOME else r.home
        was = fmt_pick(t, opp, r.home_spread, side == HOME)
        msgs = [f"League line set: {fmt_pick(t, opp, home_spread, side == HOME)} (sheet had {was})"]
        return _after_change(season, week, "after your line override", recompute, echo, msgs)


def set_score(season: int, week: int, team: str, team_points, opponent_points, *, live: bool = False,
              force: bool = False, recompute: bool = True, echo: bool = False) -> Outcome:
    """Record a game's score (team's points first). Grades whichever side you picked."""
    with LOCK:
        league = _load_league(season, week)
        r, t, side = team_game(league, team)
        tp = finite(team_points, "scores", limit=200)
        op = finite(opponent_points, "scores", limit=200)
        if tp < 0 or op < 0:
            raise UserError("scores can't be negative")
        _require_kicked_off(r, week, force)
        home_score, away_score = (tp, op) if side == HOME else (op, tp)
        ov = ovr.load_overrides(season, week)
        ov["results"][r.game_id] = {"home_score": home_score, "away_score": away_score, "final": not live}
        ovr.save_overrides(season, week, ov)
        msgs = [f"Score recorded: {r.away} {away_score:g}, {r.home} {home_score:g} ({'live' if live else 'final'})"]
        if pd.isna(r.home_spread) and r.game_id not in ov["lines"]:
            msgs.append(f"There's no league line for {r.away}@{r.home}, so this can't be graded until you set one.")
        return _after_change(season, week, "after your score override", recompute, echo, msgs)


def set_points(season: int, week: int, team: str, points, *, live: bool = False, force: bool = False,
               recompute: bool = True, echo: bool = False) -> Outcome:
    """Record a pick's score exactly as the league app shows it.

    Points only exist for a pick you hold, so this also locks that team as your
    pick in its game. It refuses a team that isn't one of your picks when all
    slots are full, rather than guessing which pick it replaced.
    """
    with LOCK:
        league = _load_league(season, week)
        r, t, side = team_game(league, team)
        val = finite(points, "points", limit=200)
        _require_kicked_off(r, week, force)
        ov = ovr.load_overrides(season, week)
        current = {p.game_id: p for p in _effective_picks(season, week, ov)}
        cur = current.get(r.game_id)
        if cur is None and len(current) >= config.N_PICKS:
            raise UserError(f"{t} isn't one of your {config.N_PICKS} picks. Set your picks to the "
                            f"{config.N_PICKS} you actually have, then enter {t}'s points.")
        ov["points"][r.game_id] = {"team": t, "value": val, "final": not live}
        existing = ov["locks"].get(r.game_id)
        if not (existing and existing["team"] == t):
            ov["locks"][r.game_id] = {
                "team": t, "side": side, "auto": True, "added": cur is None,
                "replaced": {"team": cur.team, "side": cur.side} if cur is not None and cur.team != t else None}
        ovr.save_overrides(season, week, ov)
        _confirm_game(season, week, r.game_id, Pick(r.game_id, side, t, 0.0))   # the app shows points for it
        tag = "live" if live else "final"
        if cur is None:
            msgs = [f"{t} scored {val:+g} ({tag}); added {t} to your picks in the open slot."]
        elif cur.team != t:
            msgs = [f"{t} scored {val:+g} ({tag}); {t} replaces {cur.team} as your pick in that game."]
        else:
            msgs = [f"{t} scored {val:+g} ({tag}); locked {t} as your pick in that game."]
        return _after_change(season, week, "after your points override", recompute, echo, msgs)


def clear(season: int, week: int, team: str, kinds: list[str] | None = None, *,
          recompute: bool = True, echo: bool = False) -> Outcome:
    """Remove overrides for the game a team plays in.

    Clearing points also removes the lock that entering them added, and puts
    back the pick it replaced. Clearing a lock you set yourself keeps that team
    as an open pick, like unlock.
    """
    with LOCK:
        league = _load_league(season, week)
        r, t, _ = team_game(league, team)
        try:
            ks = tuple(KIND_ALIASES[k] for k in kinds) if kinds else ovr.KINDS
        except KeyError as e:
            raise UserError(f"unknown override kind {e.args[0]!r}") from e
        ov = ovr.load_overrides(season, week)
        lk = ov["locks"].get(r.game_id)
        if "points" in ks and lk and lk.get("auto") and "locks" not in ks:
            ks = ks + ("locks",)          # that lock was a side effect of entering points
        removed = ovr.clear(ov, r.game_id, ks)
        ovr.save_overrides(season, week, ov)
        if not removed:
            return Outcome(view=week_view(season, week), messages=[f"Nothing to clear for {r.away}@{r.home}"])
        msgs = [f"Cleared {', '.join(removed)} for {r.away}@{r.home}"]
        if "locks" in removed and lk:
            if lk.get("auto"):
                rep = lk.get("replaced")
                if rep:
                    _set_state_pick(season, week, r.game_id, Pick(r.game_id, rep["side"], rep["team"], 0.0),
                                    confirmed=True)
                    msgs.append(f"Put {rep['team']} back as your pick in that game.")
                elif lk.get("added"):
                    _set_state_pick(season, week, r.game_id, None, confirmed=True)
                    msgs.append(f"Removed {lk['team']} from your picks (it was added when you entered its points).")
            else:
                _set_state_pick(season, week, r.game_id, Pick(r.game_id, lk["side"], lk["team"], 0.0))
        return _after_change(season, week, "after clearing an override", recompute, echo, msgs)


def score_week(season: int, week: int, refresh: bool = True) -> dict:
    """Grade the presumed picks without recommending changes (used after games)."""
    if refresh and not config.OFFLINE:
        games(force=True)
    g = games()
    ov = ovr.load_overrides(season, week)
    league = ovr.apply_line_overrides(_load_league(season, week), ov)
    picks = apply_locks(st.picks_from_state(st.load_state(season, week)), ov["locks"])
    tallies = tally_picks(picks, league, drop_mismatched_points(ov, picks), _week_results(g, season, week))
    rows = league.set_index("game_id")
    lines = []
    for t in tallies:
        p = t.pick
        r = rows.loc[p.game_id]
        is_home = p.side == HOME
        lines.append({"label": fmt_pick(p.team, r.away if is_home else r.home, r.home_spread, is_home),
                      "status": t.status, "points": t.points, "source": t.source})
    summ = summarize(tallies)
    return {"picks": lines, "summary": {**summ, "line": summary_line(summ)},
            "warnings": _warnings(picks, ov, league)}


def _league_weeks(seasons: list[int] | None = None) -> list[tuple[int, int]]:
    out = []
    for path in sorted(config.LEAGUE_DIR.glob("*_wk*.csv")):
        try:
            season = int(path.stem.split("_wk")[0])
            week = int(path.stem.split("_wk")[1])
        except ValueError:
            continue
        if seasons and season not in seasons:
            continue
        out.append((season, week))
    return out


def results_info() -> dict:
    """When the nflverse schedule and results were last downloaded."""
    path = config.CACHE_DIR / "games.csv"
    try:
        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        at = None
    return {"downloaded_at": at, "offline": bool(config.OFFLINE)}


def refresh_results() -> tuple[bool, str]:
    """Download the nflverse schedule and results now instead of waiting out the 6 hour cache.

    Returns (downloaded, message). Offline, or when the download fails, the
    saved copy stays in use.
    """
    if config.OFFLINE:
        return False, "Offline: using the saved nflverse results."
    path = config.CACHE_DIR / "games.csv"
    before = path.stat().st_mtime if path.exists() else None
    try:
        games(force=True)
    except requests.RequestException as e:
        return False, f"Couldn't download nflverse results ({type(e).__name__}); using the saved copy."
    after = path.stat().st_mtime if path.exists() else None
    if after is None or after == before:
        return False, "Couldn't download nflverse results; using the saved copy."
    return True, "Downloaded the latest nflverse results."


def week_results(season: int, week: int) -> Outcome:
    """Fetch nflverse results now and regrade the week (and its record)."""
    _, msg = refresh_results()
    with LOCK:
        _load_league(season, week)
        view = week_view(season, week)
        history.upsert_week(view, only_if_changed=True)
        return Outcome(view=view, messages=[msg])


def sync_records(seasons: list[int] | None = None) -> int:
    """Bring every unfinished week record up to date with its computed week. Returns weeks written.

    Finals arrive from nflverse after the week's last update ran, so a record
    would otherwise keep its live and open picks. Only the database is
    written, and only when a value changed; complete records are left alone.
    """
    n = 0
    with LOCK:
        for season, week in _league_weeks(seasons):
            rec = history.get_week(season, week)
            if rec is not None and rec["complete"]:
                continue
            try:
                view = week_view(season, week)
            except (UserError, ValueError, KeyError):
                continue
            if history.upsert_week(view, only_if_changed=True):
                n += 1
    return n


def refresh_history(seasons: list[int] | None = None, download: bool = False) -> int:
    """Recompute and store the record for every week that has league lines and picks. Returns weeks updated.

    ``download`` fetches the latest nflverse results first (unless offline).
    """
    if download:
        refresh_results()
    n = 0
    with LOCK:
        for season, week in _league_weeks(seasons):
            if history.upsert_week(week_view(season, week)):
                n += 1
    return n
