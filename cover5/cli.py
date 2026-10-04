"""Command line entry points.

Weekly flow
  python -m cover5 init-week              seed this week's league lines from the market
  python -m cover5 update                 fetch market, recompute picks, alert on changes
  python -m cover5 status                 show picks, scores and overrides (no fetch)
  python -m cover5 score-week             grade the week's picks

Telling the tracker what is true (all of these are overrides the bot never erases)
  python -m cover5 picks IND CHI LAR TEN JAX   the five picks you actually have in the app
  python -m cover5 lock TEN                    pin a pick; the model plans around it
  python -m cover5 unlock TEN
  python -m cover5 set-line IND -3.5           league line, from that team's view
  python -m cover5 set-score IND 30 13         game score, that team's points first
  python -m cover5 set-points TEN 5.5          a pick's score as the app shows it
  python -m cover5 overrides                   list overrides
  python -m cover5 clear IND [--kind line]     remove overrides for a game

Each override immediately recomputes the picks from the last logged market
lines and alerts if they change. Add --no-recompute to only record it.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from cover5 import config, alerts
from cover5 import overrides as ovr
from cover5 import state as st
from cover5.league import build_week_file, load_week_file, league_path
from cover5.picks import (Pick, apply_locks, build_board, diff_picks, latest_snapshot_market,
                          lock_time_market, recommend)
from cover5.providers import fetch_market
from cover5.schedule import current_week, load_games
from cover5.scoring import AWAY, HOME, fmt_pick
from cover5.tally import summarize, summary_line, tally_picks
from cover5.teams import normalize

KIND_ALIASES = {"line": "lines", "lines": "lines", "lock": "locks", "locks": "locks",
                "score": "results", "result": "results", "results": "results",
                "points": "points", "point": "points"}


# --------------------------------------------------------------------------- helpers
def _resolve_week(args, games) -> tuple[int, int]:
    if args.season and args.week:
        return args.season, args.week
    s, w = current_week(games)
    return (args.season or s), (args.week or w)


def _find_game(league: pd.DataFrame, team: str):
    try:
        team = normalize(team)
    except ValueError:
        return None
    row = league[(league.home == team) | (league.away == team)]
    return None if row.empty else row.iloc[0]


def _team_game(league: pd.DataFrame, team: str):
    """(row, canonical team, side) or raise SystemExit with a clear message."""
    r = _find_game(league, team)
    if r is None:
        raise SystemExit(f"{team.upper()} is not on this week's slate")
    t = normalize(team)
    return r, t, (HOME if r.home == t else AWAY)


def _parse_spread(s: str) -> float:
    return 0.0 if s.upper() in ("PK", "EVEN", "PICK") else float(s)


def _week_results(games: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    return games[(games.season == season) & (games.week == week)].set_index("game_id")


def _warnings(picks: list[Pick], board: pd.DataFrame, ov: dict, league: pd.DataFrame) -> list[str]:
    w = []
    locked = [p for p in picks if p.locked]
    if len(locked) > config.N_PICKS:
        w.append(f"{len(locked)} picks are locked but the league allows {config.N_PICKS}. "
                 f"Run 'picks' with the {config.N_PICKS} teams you actually have.")
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


def _recompute(season: int, week: int, games: pd.DataFrame, market, *, reason: str,
               alert: bool = True, save: bool = True, force_alert: bool = False,
               now: datetime | None = None):
    """The one place picks are recomputed: overrides in, recommendation and alert out."""
    now = now or datetime.now(timezone.utc)
    ov = ovr.load_overrides(season, week)
    league = ovr.apply_line_overrides(load_week_file(season, week), ov)
    market = lock_time_market(market, st.load_snapshots(season, week), league, now)
    s = st.load_state(season, week)
    current = apply_locks(st.picks_from_state(s), ov["locks"])
    board = build_board(league, market, now)
    picks = recommend(board, current)
    # Diff against the presumed picks with your own locks already applied, so the
    # alert only lists changes the model wants, not the ones you just made.
    diff = diff_picks(current, picks)
    tallies = tally_picks(picks, league, _drop_mismatched_points(ov, picks), _week_results(games, season, week))
    warnings = _warnings(picks, board, ov, league)
    title, body = alerts.format_alert(season, week, picks, diff, board, tallies=tallies,
                                      warnings=warnings, reason=reason)
    if alert:
        alerts.send(title, body, force=force_alert, changed=diff["changed"])
    else:
        print(title)
        print(body)
    if save:
        s["recommended"] = st.picks_to_state(picks)
        s["last_run"] = st.now_iso()
        s.pop("confirmed", None)
        s.setdefault("history", []).append({
            "at": s["last_run"], "reason": reason, "changed": diff["changed"],
            "picks": [f"{p.team}:{p.edge:+.1f}{':L' if p.locked else ''}" for p in picks],
            "projected": round(summarize(tallies)["projected"], 2)})
        st.save_state(season, week, s)
    return board, picks, tallies, diff


def _drop_mismatched_points(ov: dict, picks: list[Pick]) -> dict:
    """A points override only grades the pick it was entered for."""
    teams = {p.game_id: p.team for p in picks}
    pts = {gid: v for gid, v in ov.get("points", {}).items()
           if not v.get("team") or teams.get(gid) == v["team"]}
    return {**ov, "points": pts}


def _offline_recompute(args, season: int, week: int, games: pd.DataFrame, reason: str) -> int:
    if getattr(args, "no_recompute", False):
        return 0
    market = latest_snapshot_market(st.load_snapshots(season, week))
    if not market:
        print("No market lines logged yet this week, so picks were not recomputed. Run: python -m cover5 update")
        return 0
    print()
    _recompute(season, week, games, market, reason=reason)
    return 0


def _print_board(board: pd.DataFrame) -> None:
    show = board[["away", "home", "league_str", "market_str", "best_team", "edge", "books", "locked"]].copy()
    show["edge"] = show["edge"].round(1)
    show.loc[board.line_overridden, "league_str"] = show.loc[board.line_overridden, "league_str"] + "*"
    print("\nBOARD (sorted by edge):")
    print(show.to_string(index=False))


def _context(args):
    games = load_games()
    season, week = _resolve_week(args, games)
    return games, season, week


# --------------------------------------------------------------------------- weekly flow
def cmd_init_week(args) -> int:
    games, season, week = _context(args)
    market = None
    if not args.no_market:
        try:
            market = fetch_market(args.provider)
        except Exception as e:  # noqa: BLE001
            print(f"[init-week] market fetch failed ({e}); seeding from nflverse spreads only")
    df = build_week_file(games, season, week, market, overwrite=args.overwrite)
    print(f"Wrote {league_path(season, week)} with {len(df)} games. "
          f"Fix any line that differs from the league app with: python -m cover5 set-line TEAM SPREAD")
    print(df.to_string(index=False))
    if market is not None:
        st.append_snapshot(season, week, market)
    if ovr.load_overrides(season, week)["lines"]:
        print("Existing line overrides still apply on top of this file (see: python -m cover5 overrides).")
    return 0


def cmd_update(args) -> int:
    games, season, week = _context(args)
    market = fetch_market(args.provider)
    load_week_file(season, week)          # fail early with a clear message
    st.append_snapshot(season, week, market)
    board, *_ = _recompute(season, week, games, market, reason="market update",
                           force_alert=args.force_alert)
    _print_board(board)
    return 0


def cmd_status(args) -> int:
    games, season, week = _context(args)
    s = st.load_state(season, week)
    print(f"Week {config.week_key(season, week)}  last run: {s.get('last_run')}")
    market = latest_snapshot_market(st.load_snapshots(season, week))
    if market:
        _recompute(season, week, games, market, reason="status, last logged lines", alert=False, save=False)
    else:
        print("No market lines logged yet. Run: python -m cover5 update")
    _print_overrides(season, week)
    return 0


def cmd_score_week(args) -> int:
    games = load_games(force=not config.OFFLINE)
    season, week = _resolve_week(args, games)
    ov = ovr.load_overrides(season, week)
    league = ovr.apply_line_overrides(load_week_file(season, week), ov)
    picks = apply_locks(st.picks_from_state(st.load_state(season, week)), ov["locks"])
    tallies = tally_picks(picks, league, _drop_mismatched_points(ov, picks), _week_results(games, season, week))
    rows = league.set_index("game_id")
    for t in tallies:
        p = t.pick
        r = rows.loc[p.game_id]
        is_home = p.side == HOME
        label = fmt_pick(p.team, r.away if is_home else r.home, r.home_spread, is_home)
        if t.status == "open":
            print(f"  {label:22s}  pending (expected {t.points:+.1f})")
        else:
            tag = "" if t.status == "final" else " live"
            src = f"  [{t.source}]" if "override" in t.source else ""
            print(f"  {label:22s}  {t.points:+g}{tag}{src}")
    print(summary_line(summarize(tallies)))
    return 0


# --------------------------------------------------------------------------- overrides
def cmd_picks(args) -> int:
    games, season, week = _context(args)
    league = ovr.apply_line_overrides(load_week_file(season, week), ovr.load_overrides(season, week))
    if len(args.teams) > config.N_PICKS:
        raise SystemExit(f"{len(args.teams)} teams given; the league allows {config.N_PICKS}")
    chosen, seen_games = [], set()
    for team in args.teams:
        r, t, side = _team_game(league, team)
        if r.game_id in seen_games:
            raise SystemExit(f"two picks in {r.away}@{r.home}; pick one side per game")
        seen_games.add(r.game_id)
        chosen.append(Pick(r.game_id, side, t, 0.0))
    s = st.load_state(season, week)
    s["recommended"] = st.picks_to_state(chosen)
    s.pop("confirmed", None)
    st.save_state(season, week, s)
    print("Your picks are now: " + ", ".join(p.team for p in chosen))
    if len(chosen) < config.N_PICKS:
        print(f"{config.N_PICKS - len(chosen)} open slot(s); the model will suggest teams for them.")
    return _offline_recompute(args, season, week, games, "after you set your picks")


def cmd_lock(args) -> int:
    games, season, week = _context(args)
    league = load_week_file(season, week)
    ov = ovr.load_overrides(season, week)
    presumed = {p.game_id: p for p in st.picks_from_state(st.load_state(season, week))}
    for team in args.teams:
        r, t, side = _team_game(league, team)
        ov["locks"][r.game_id] = {"team": t, "side": side}
        note = ""
        if r.game_id not in presumed:
            note = (" It wasn't one of your picks, so the weakest open pick gets dropped to make room."
                    " If you replaced a different team, run 'picks' with your actual five.")
        elif presumed[r.game_id].team != t:
            note = f" (replaces {presumed[r.game_id].team} in that game)"
        print(f"Locked {t}.{note}")
    ovr.save_overrides(season, week, ov)
    return _offline_recompute(args, season, week, games, "after you locked a pick")


def cmd_unlock(args) -> int:
    games, season, week = _context(args)
    league = load_week_file(season, week)
    ov = ovr.load_overrides(season, week)
    for team in args.teams:
        r, t, _ = _team_game(league, team)
        if ovr.clear(ov, r.game_id, ("locks",)):
            print(f"Unlocked {r.away}@{r.home}. It stays your pick until the model suggests otherwise"
                  + (" (it has kicked off, so it stays locked)." if datetime.now(timezone.utc) >= r.kickoff_utc else "."))
        else:
            print(f"No lock on {r.away}@{r.home}")
    ovr.save_overrides(season, week, ov)
    return _offline_recompute(args, season, week, games, "after you unlocked a pick")


def cmd_set_line(args) -> int:
    """`set-line IND -3.5` means IND is favored by 3.5 whether IND is home or away."""
    games, season, week = _context(args)
    league = load_week_file(season, week)
    r, t, side = _team_game(league, args.team)
    spread = _parse_spread(args.spread)
    home_spread = spread if side == HOME else -spread
    ov = ovr.load_overrides(season, week)
    ov["lines"][r.game_id] = home_spread
    ovr.save_overrides(season, week, ov)
    opp = r.away if side == HOME else r.home
    was = fmt_pick(t, opp, r.home_spread, side == HOME)
    print(f"League line set: {fmt_pick(t, opp, home_spread, side == HOME)} (sheet had {was})")
    return _offline_recompute(args, season, week, games, "after your line override")


def cmd_set_score(args) -> int:
    games, season, week = _context(args)
    league = load_week_file(season, week)
    r, t, side = _team_game(league, args.team)
    team_pts, opp_pts = float(args.team_points), float(args.opponent_points)
    home_score, away_score = (team_pts, opp_pts) if side == HOME else (opp_pts, team_pts)
    ov = ovr.load_overrides(season, week)
    ov["results"][r.game_id] = {"home_score": home_score, "away_score": away_score, "final": not args.live}
    ovr.save_overrides(season, week, ov)
    print(f"Score recorded: {r.away} {away_score:g}, {r.home} {home_score:g} ({'live' if args.live else 'final'})")
    return _offline_recompute(args, season, week, games, "after your score override")


def cmd_set_points(args) -> int:
    games, season, week = _context(args)
    league = load_week_file(season, week)
    r, t, side = _team_game(league, args.team)
    ov = ovr.load_overrides(season, week)
    ov["points"][r.game_id] = {"team": t, "value": float(args.points), "final": not args.live}
    # Points only exist for a pick you hold, so the team is your pick in that game.
    ov["locks"][r.game_id] = {"team": t, "side": side}
    ovr.save_overrides(season, week, ov)
    print(f"{t} scored {float(args.points):+g} ({'live' if args.live else 'final'}); locked {t} as your pick in that game.")
    return _offline_recompute(args, season, week, games, "after your points override")


def cmd_overrides(args) -> int:
    games, season, week = _context(args)
    _print_overrides(season, week)
    return 0


def _print_overrides(season: int, week: int) -> None:
    ov = ovr.load_overrides(season, week)
    try:
        league = load_week_file(season, week)
    except FileNotFoundError:
        league = pd.DataFrame(columns=["game_id", "away", "home"])
    lines = ovr.describe(ov, league)
    print(f"\nOVERRIDES ({ovr.overrides_path(season, week).name}):")
    print("  none" if not lines else "\n".join(f"  {l}" for l in lines))


def cmd_clear(args) -> int:
    games, season, week = _context(args)
    league = load_week_file(season, week)
    r, t, _ = _team_game(league, args.team)
    kinds = tuple(KIND_ALIASES[k] for k in args.kind) if args.kind else ovr.KINDS
    ov = ovr.load_overrides(season, week)
    removed = ovr.clear(ov, r.game_id, kinds)
    ovr.save_overrides(season, week, ov)
    print(f"Cleared {', '.join(removed)} for {r.away}@{r.home}" if removed else f"Nothing to clear for {r.away}@{r.home}")
    return _offline_recompute(args, season, week, games, "after clearing an override") if removed else 0


def cmd_backtest(args) -> int:
    from cover5.backtest import run
    run(load_games(), start=args.start, end=args.end)
    return 0


# --------------------------------------------------------------------------- parser
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cover5", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--provider", choices=["oddsapi", "espn"], default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-week", help="seed league lines for the week")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-market", action="store_true", help="seed from nflverse spreads only")
    p.set_defaults(fn=cmd_init_week)

    p = sub.add_parser("update", help="fetch market, recompute picks, alert on change")
    p.add_argument("--force-alert", action="store_true", help="push a notification even if nothing changed")
    p.set_defaults(fn=cmd_update)

    sub.add_parser("status", help="show picks, scores and overrides").set_defaults(fn=cmd_status)
    sub.add_parser("score-week", help="grade this week's picks").set_defaults(fn=cmd_score_week)
    sub.add_parser("overrides", help="list this week's overrides").set_defaults(fn=cmd_overrides)

    def override(name, fn, help_):
        q = sub.add_parser(name, help=help_)
        q.add_argument("--no-recompute", action="store_true", help="record only; don't recompute picks")
        q.set_defaults(fn=fn)
        return q

    q = override("picks", cmd_picks, "set the picks you actually have in the league app")
    q.add_argument("teams", nargs="+")
    q = override("lock", cmd_lock, "pin picks so the model plans around them")
    q.add_argument("teams", nargs="+")
    q = override("unlock", cmd_unlock, "remove a lock")
    q.add_argument("teams", nargs="+")
    q = override("set-line", cmd_set_line, "league line from a team's view, e.g. IND -3.5")
    q.add_argument("team")
    q.add_argument("spread", help="that team's spread: -3.5 favored, 2.5 underdog, PK")
    q = override("set-score", cmd_set_score, "game score, e.g. IND 30 13 (team's points first)")
    q.add_argument("team")
    q.add_argument("team_points")
    q.add_argument("opponent_points")
    q.add_argument("--live", action="store_true", help="game still in progress")
    q = override("set-points", cmd_set_points, "a pick's score as the league app shows it")
    q.add_argument("team")
    q.add_argument("points", help="e.g. 13.5 or -4")
    q.add_argument("--live", action="store_true", help="game still in progress")
    q = override("clear", cmd_clear, "remove overrides for the game a team plays in")
    q.add_argument("team")
    q.add_argument("--kind", action="append", choices=sorted(KIND_ALIASES),
                   help="only this kind (repeatable); default clears all")

    p = sub.add_parser("backtest", help="historical checks using nflverse closing lines")
    p.add_argument("--start", type=int, default=2010)
    p.add_argument("--end", type=int, default=2025)
    p.set_defaults(fn=cmd_backtest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
