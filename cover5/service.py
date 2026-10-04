"""Every tracker operation in one place, shared by the CLI, the local API and the scheduler.

Operations return an ``Outcome``: the week as structured data (``view``), the
alert that was produced (if picks were recomputed), and human readable
messages. User mistakes raise ``UserError`` so each front end can report them
its own way (exit code, HTTP 400).

All mutating operations take ``LOCK`` so the API and the background scheduler
never interleave writes to the same week's files.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from cover5 import alerts, config
from cover5 import history
from cover5 import overrides as ovr
from cover5 import state as st
from cover5.league import build_week_file, league_path, load_week_file
from cover5.picks import (Pick, apply_locks, build_board, diff_picks, latest_snapshot_market,
                          lock_time_market, recommend)
from cover5.providers import fetch_market
from cover5.schedule import current_week, load_games
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
        raise ProviderError(f"Market fetch failed: {e}") from e


@dataclass
class Outcome:
    view: dict | None = None
    alert: dict | None = None          # {"title", "body", "changed", "sent"}
    messages: list[str] = field(default_factory=list)

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
        return float(s)
    s = str(s).strip()
    if s.upper() in ("PK", "EVEN", "PICK"):
        return 0.0
    try:
        return float(s)
    except ValueError as e:
        raise UserError(f"not a spread: {s!r} (use a number like -3.5 or PK)") from e


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
            now: datetime | None = None, echo: bool = False) -> tuple[Computed, dict | None]:
    """Recompute picks from overrides + market. ``market=None`` uses the last logged lines.

    Returns the computation and, when ``alert`` is set, the alert dict.
    ``save`` writes the recommendation as the presumed current picks and records
    the run in history. ``echo`` prints the alert (CLI).
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
    warnings = _warnings(picks, ov, league)
    title, body = alerts.format_alert(season, week, picks, diff, board, tallies=tallies,
                                      warnings=warnings, reason=reason)
    c = Computed(season, week, now, league, ov, board, current, picks, tallies, diff, warnings,
                 title, body, market_source)
    alert_info = None
    if alert:
        sent = alerts.send(title, body, force=force_alert, changed=diff["changed"], echo=echo)
        alert_info = {"title": title, "body": body, "changed": diff["changed"], "sent": sent}
    elif echo:
        print(title)
        print(body)
    if save:
        s["recommended"] = st.picks_to_state(picks)
        s["last_run"] = st.now_iso()
        s.pop("confirmed", None)
        summ = summarize(tallies)
        s.setdefault("history", []).append({
            "at": s["last_run"], "reason": reason, "changed": diff["changed"],
            "picks": [f"{p.team}:{p.edge:+.1f}{':L' if p.locked else ''}" for p in picks],
            "projected": round(summ["projected"], 2)})
        st.save_state(season, week, s)
        view = build_view(c)
        history.record_run(season, week, at=s["last_run"], reason=reason, changed=diff["changed"],
                           diff=view["diff"], picks=view["picks"], summary=view["summary"],
                           warnings=warnings, title=title, body=body,
                           sent=bool(alert_info and alert_info["sent"]))
        history.upsert_week(view)
    return c, alert_info


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
        "diff": {"changed": c.diff["changed"],
                 "added": [p.team for p in c.diff["added"]],
                 "dropped": [p.team for p in c.diff["dropped"]],
                 "flipped": [p.team for p in c.diff["flipped"]]},
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


def _after_change(season: int, week: int, reason: str, recompute: bool, echo: bool,
                  messages: list[str]) -> Outcome:
    """Recompute from the last logged lines after an override, if there are any."""
    out = Outcome(messages=messages)
    if recompute and st.load_snapshots(season, week) is not None:
        c, out.alert = compute(season, week, None, reason=reason, alert=True, save=True, echo=echo)
        out.view = build_view(c)
    else:
        if recompute:
            out.messages.append("No market lines logged yet this week, so picks were not "
                                "recomputed. Fetch lines to update.")
        out.view = week_view(season, week)
    return out


# --------------------------------------------------------------------------- operations
def init_week(season: int, week: int, *, overwrite: bool = False, use_market: bool = True,
              provider: str | None = None) -> Outcome:
    if not overwrite and league_path(season, week).exists():
        raise UserError(f"{season} week {week} already has league lines; overwrite to replace them")
    # Fetch outside LOCK so a slow provider never blocks reads of the week.
    market, msgs = None, []
    if use_market:
        try:
            market = fetch_market(provider)
        except Exception as e:  # noqa: BLE001
            msgs.append(f"Market fetch failed ({e}); seeded from nflverse spreads only.")
    with LOCK:
        g = games()
        try:
            df = build_week_file(g, season, week, market, overwrite=overwrite)
        except FileExistsError as e:
            raise UserError(f"{season} week {week} already has league lines; overwrite to replace them") from e
        if df.empty:
            league_path(season, week).unlink(missing_ok=True)
            raise UserError(f"No regular season games found for {season} week {week}")
        msgs.append(f"Seeded {len(df)} games for {season} week {week}. "
                    "Correct any line that differs from the league app.")
        if market is not None:
            st.append_snapshot(season, week, market)
        if ovr.load_overrides(season, week)["lines"]:
            msgs.append("Existing line overrides still apply on top of the new sheet.")
        out = Outcome(messages=msgs, view=week_view(season, week))
        return out


def update(season: int, week: int, *, provider: str | None = None, force_alert: bool = False,
           echo: bool = False) -> Outcome:
    with LOCK:
        _load_league(season, week)               # fail early with a clear message
    market = _fetch(provider)                    # outside LOCK: a slow provider never blocks reads
    with LOCK:
        _load_league(season, week)
        st.append_snapshot(season, week, market)
        c, alert = compute(season, week, market, reason="market update", alert=True, save=True,
                           force_alert=force_alert, echo=echo)
        return Outcome(view=build_view(c), alert=alert,
                       messages=[f"Fetched {len(market)} market lines."])


def set_picks(season: int, week: int, teams: list[str], *, recompute: bool = True,
              echo: bool = False) -> Outcome:
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
        s = st.load_state(season, week)
        s["recommended"] = st.picks_to_state(chosen)
        s.pop("confirmed", None)
        st.save_state(season, week, s)
        msgs = ["Your picks are now: " + (", ".join(p.team for p in chosen) or "none")]
        if len(chosen) < config.N_PICKS:
            msgs.append(f"{config.N_PICKS - len(chosen)} open slot(s); the model will suggest teams for them.")
        return _after_change(season, week, "after you set your picks", recompute, echo, msgs)


def lock(season: int, week: int, teams: list[str], *, recompute: bool = True, echo: bool = False) -> Outcome:
    with LOCK:
        league = _load_league(season, week)
        ov = ovr.load_overrides(season, week)
        presumed = {p.game_id: p for p in st.picks_from_state(st.load_state(season, week))}
        msgs = []
        for team in teams:
            r, t, side = team_game(league, team)
            ov["locks"][r.game_id] = {"team": t, "side": side}
            note = ""
            if r.game_id not in presumed:
                note = (" It wasn't one of your picks, so the weakest open pick gets dropped to make room."
                        " If you replaced a different team, set your picks to your actual five.")
            elif presumed[r.game_id].team != t:
                note = f" (replaces {presumed[r.game_id].team} in that game)"
            msgs.append(f"Locked {t}.{note}")
        ovr.save_overrides(season, week, ov)
        return _after_change(season, week, "after you locked a pick", recompute, echo, msgs)


def unlock(season: int, week: int, teams: list[str], *, recompute: bool = True, echo: bool = False) -> Outcome:
    with LOCK:
        league = _load_league(season, week)
        ov = ovr.load_overrides(season, week)
        msgs = []
        for team in teams:
            r, t, _ = team_game(league, team)
            if ovr.clear(ov, r.game_id, ("locks",)):
                started = datetime.now(timezone.utc) >= r.kickoff_utc
                msgs.append(f"Unlocked {r.away}@{r.home}. It stays your pick until the model suggests otherwise"
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
              recompute: bool = True, echo: bool = False) -> Outcome:
    with LOCK:
        league = _load_league(season, week)
        r, t, side = team_game(league, team)
        try:
            tp, op = float(team_points), float(opponent_points)
        except (TypeError, ValueError) as e:
            raise UserError("scores must be numbers") from e
        if tp < 0 or op < 0:
            raise UserError("scores can't be negative")
        home_score, away_score = (tp, op) if side == HOME else (op, tp)
        ov = ovr.load_overrides(season, week)
        ov["results"][r.game_id] = {"home_score": home_score, "away_score": away_score, "final": not live}
        ovr.save_overrides(season, week, ov)
        msgs = [f"Score recorded: {r.away} {away_score:g}, {r.home} {home_score:g} ({'live' if live else 'final'})"]
        return _after_change(season, week, "after your score override", recompute, echo, msgs)


def set_points(season: int, week: int, team: str, points, *, live: bool = False,
               recompute: bool = True, echo: bool = False) -> Outcome:
    with LOCK:
        league = _load_league(season, week)
        r, t, side = team_game(league, team)
        try:
            val = float(points)
        except (TypeError, ValueError) as e:
            raise UserError("points must be a number") from e
        ov = ovr.load_overrides(season, week)
        ov["points"][r.game_id] = {"team": t, "value": val, "final": not live}
        # Points only exist for a pick you hold, so the team is your pick in that game.
        ov["locks"][r.game_id] = {"team": t, "side": side}
        ovr.save_overrides(season, week, ov)
        msgs = [f"{t} scored {val:+g} ({'live' if live else 'final'}); locked {t} as your pick in that game."]
        return _after_change(season, week, "after your points override", recompute, echo, msgs)


def clear(season: int, week: int, team: str, kinds: list[str] | None = None, *,
          recompute: bool = True, echo: bool = False) -> Outcome:
    with LOCK:
        league = _load_league(season, week)
        r, t, _ = team_game(league, team)
        try:
            ks = tuple(KIND_ALIASES[k] for k in kinds) if kinds else ovr.KINDS
        except KeyError as e:
            raise UserError(f"unknown override kind {e.args[0]!r}") from e
        ov = ovr.load_overrides(season, week)
        removed = ovr.clear(ov, r.game_id, ks)
        ovr.save_overrides(season, week, ov)
        if not removed:
            return Outcome(view=week_view(season, week), messages=[f"Nothing to clear for {r.away}@{r.home}"])
        msgs = [f"Cleared {', '.join(removed)} for {r.away}@{r.home}"]
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
    return {"picks": lines, "summary": {**summ, "line": summary_line(summ)}}


def refresh_history(seasons: list[int] | None = None) -> int:
    """Recompute and store the record for every week that has league lines. Returns weeks updated."""
    n = 0
    with LOCK:
        for path in sorted(config.LEAGUE_DIR.glob("*_wk*.csv")):
            try:
                season = int(path.stem.split("_wk")[0])
                week = int(path.stem.split("_wk")[1])
            except ValueError:
                continue
            if seasons and season not in seasons:
                continue
            history.upsert_week(week_view(season, week))
            n += 1
    return n
