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
                                               (add --week N to fix a past week)
  python -m cover5 overrides                   list overrides
  python -m cover5 clear IND [--kind line]     remove overrides for a game

Each override immediately recomputes the picks from the last logged market
lines and alerts if they change. Add --no-recompute to only record it.

Records and the web app
  python -m cover5 history [--refresh]         saved weekly records
  python -m cover5 serve [--open]              local web app at http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import sys

from cover5 import config, service
from cover5 import overrides as ovr
from cover5 import state as st
from cover5.league import load_week_file
from cover5.service import KIND_ALIASES, UserError


def _week(args) -> tuple[int, int]:
    return service.resolve_week(args.season, args.week)


def _run(fn, *a, **kw):
    """Call a service operation and print what it says, CLI style."""
    try:
        out = fn(*a, **kw)
    except UserError as e:
        raise SystemExit(str(e)) from e
    for m in out.messages:
        print(m)
    return out


def _print_board(view: dict) -> None:
    import pandas as pd
    from cover5.scoring import fmt_spread
    rows = []
    for g in sorted(view["games"], key=lambda g: (g["edge"] is None, -(g["edge"] or 0))):
        league = fmt_spread(g["away"], g["home"], g["league_home_spread"])
        rows.append({"away": g["away"], "home": g["home"],
                     "league": league + ("*" if g["line_overridden"] else ""),
                     "market": fmt_spread(g["away"], g["home"], g["market_home_spread"]),
                     "best": g["best_team"] or "", "edge": None if g["edge"] is None else round(g["edge"], 1),
                     "books": g["books"], "locked": g["locked"]})
    print("\nBOARD (sorted by edge):")
    print(pd.DataFrame(rows).to_string(index=False))


# --------------------------------------------------------------------------- weekly flow
def cmd_init_week(args) -> int:
    season, week = _week(args)
    out = _run(service.init_week, season, week, overwrite=args.overwrite,
               use_market=not args.no_market, provider=args.provider)
    for g in out.view.get("games", []):
        print(f"  {g['away']:>3} @ {g['home']:<3}  home {g['league_home_spread']}")
    return 0


def cmd_update(args) -> int:
    season, week = _week(args)
    out = _run(service.update, season, week, provider=args.provider,
               force_alert=args.force_alert, echo=True)
    _print_board(out.view)
    return 0


def cmd_status(args) -> int:
    season, week = _week(args)
    s = st.load_state(season, week)
    print(f"Week {config.week_key(season, week)}  last run: {s.get('last_run')}")
    if st.load_snapshots(season, week) is not None:
        try:
            service.compute(season, week, None, reason="status, last logged lines", echo=True)
        except UserError as e:
            raise SystemExit(str(e)) from e
    else:
        print("No market lines logged yet. Run: python -m cover5 update")
    _print_overrides(season, week)
    return 0


def cmd_score_week(args) -> int:
    season, week = _week(args)
    try:
        res = service.score_week(season, week)
    except UserError as e:
        raise SystemExit(str(e)) from e
    for p in res["picks"]:
        if p["status"] == "open":
            print(f"  {p['label']:22s}  pending (expected {p['points']:+.1f})")
        else:
            tag = "" if p["status"] == "final" else " live"
            src = f"  [{p['source']}]" if "override" in p["source"] else ""
            print(f"  {p['label']:22s}  {p['points']:+g}{tag}{src}")
    for w in res.get("warnings", []):
        print(f"WARNING: {w}")
    print(res["summary"]["line"])
    return 0


# --------------------------------------------------------------------------- overrides
def _recompute_flag(args) -> dict:
    return {"recompute": not args.no_recompute, "echo": True}


def cmd_picks(args) -> int:
    season, week = _week(args)
    _run(service.set_picks, season, week, args.teams, **_recompute_flag(args))
    return 0


def cmd_lock(args) -> int:
    season, week = _week(args)
    _run(service.lock, season, week, args.teams, **_recompute_flag(args))
    return 0


def cmd_unlock(args) -> int:
    season, week = _week(args)
    _run(service.unlock, season, week, args.teams, **_recompute_flag(args))
    return 0


def cmd_set_line(args) -> int:
    season, week = _week(args)
    _run(service.set_line, season, week, args.team, args.spread, **_recompute_flag(args))
    return 0


def cmd_set_score(args) -> int:
    season, week = _week(args)
    _run(service.set_score, season, week, args.team, args.team_points, args.opponent_points,
         live=args.live, force=args.force, **_recompute_flag(args))
    return 0


def cmd_set_points(args) -> int:
    season, week = _week(args)
    _run(service.set_points, season, week, args.team, args.points, live=args.live, force=args.force,
         **_recompute_flag(args))
    return 0


def cmd_overrides(args) -> int:
    season, week = _week(args)
    _print_overrides(season, week)
    return 0


def _print_overrides(season: int, week: int) -> None:
    import pandas as pd
    ov = ovr.load_overrides(season, week)
    try:
        league = load_week_file(season, week)
    except FileNotFoundError:
        league = pd.DataFrame(columns=["game_id", "away", "home"])
    lines = ovr.describe(ov, league)
    print(f"\nOVERRIDES ({ovr.overrides_path(season, week).name}):")
    print("  none" if not lines else "\n".join(f"  {l}" for l in lines))


def cmd_clear(args) -> int:
    season, week = _week(args)
    _run(service.clear, season, week, args.team, args.kind, **_recompute_flag(args))
    return 0


def cmd_history(args) -> int:
    from cover5 import history
    if args.refresh:
        n = service.refresh_history()
        print(f"Refreshed {n} week record(s).")
    rows = history.list_weeks(args.season)
    if not rows:
        print("No weekly records yet.")
        return 0
    for r in rows:
        picks = ", ".join(f"{p['team']} {p['points']:+g}" if p["status"] != "open" else f"{p['team']} (open)"
                          for p in r["picks"])
        rank = f"  rank {r['week_rank']}/{r['entrants'] or '?'}" if r["week_rank"] else ""
        print(f"{r['season']} wk{r['week']:>2}  {r['final_points'] + r['live_points']:+7.1f}"
              f"  (movement edge {r['movement_edge']:+.1f}){rank}  {picks}")
    return 0


def cmd_serve(args) -> int:
    from cover5.server import serve
    serve(host=args.host, port=args.port, scheduler=not args.no_scheduler, open_browser=args.open)
    return 0


def cmd_backtest(args) -> int:
    from cover5.backtest import run
    run(service.games(), start=args.start, end=args.end)
    return 0


# --------------------------------------------------------------------------- parser
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cover5", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--provider", choices=list(config.PROVIDERS), default=None)
    # --season/--week also work after the subcommand ("set-points NO 3.5 --week 4"),
    # which is how the phone workflow targets a past week. SUPPRESS keeps a
    # subcommand from overwriting values given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--season", type=int, default=argparse.SUPPRESS)
    common.add_argument("--week", type=int, default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add_parser = sub.add_parser
    sub.add_parser = lambda *a, **kw: _add_parser(*a, parents=[common], **kw)

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
    q.add_argument("--force", action="store_true", help="allow a game that hasn't kicked off")
    q = override("set-points", cmd_set_points, "a pick's score as the league app shows it")
    q.add_argument("team")
    q.add_argument("points", help="e.g. 13.5 or -4")
    q.add_argument("--live", action="store_true", help="game still in progress")
    q.add_argument("--force", action="store_true", help="allow a game that hasn't kicked off")
    q = override("clear", cmd_clear, "remove overrides for the game a team plays in")
    q.add_argument("team")
    q.add_argument("--kind", action="append", choices=sorted(KIND_ALIASES),
                   help="only this kind (repeatable); default clears all")

    p = sub.add_parser("history", help="list saved weekly records")
    p.add_argument("--refresh", action="store_true", help="recompute every week's record first")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("serve", help="run the local web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-scheduler", action="store_true", help="don't poll the market in the background")
    p.add_argument("--open", action="store_true", help="open the app in a browser")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("backtest", help="historical checks using nflverse closing lines")
    p.add_argument("--start", type=int, default=2010)
    p.add_argument("--end", type=int, default=2025)
    p.set_defaults(fn=cmd_backtest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
