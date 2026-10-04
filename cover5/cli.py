"""Command line entry points.

  python -m cover5 init-week            seed this week's league lines from the market
  python -m cover5 update               fetch market, recompute picks, alert on changes
  python -m cover5 status               show the board without fetching
  python -m cover5 set-line IND -3.5    enter a league line the way the app shows it
  python -m cover5 confirm KC           record that you actually have KC picked
  python -m cover5 score-week           score the week's picks from final results
  python -m cover5 backtest             historical sanity checks on the scoring rule
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from cover5 import config, alerts
from cover5.league import build_week_file, load_week_file, league_path
from cover5.picks import build_board, recommend, diff_picks
from cover5.providers import fetch_market
from cover5.schedule import load_games, current_week
from cover5 import state as st
from cover5.scoring import HOME, AWAY, fmt_pick
from cover5.teams import normalize


def _resolve_week(args, games) -> tuple[int, int]:
    if args.season and args.week:
        return args.season, args.week
    s, w = current_week(games)
    return (args.season or s), (args.week or w)


def cmd_init_week(args) -> int:
    games = load_games()
    season, week = _resolve_week(args, games)
    market = None
    if not args.no_market:
        try:
            market = fetch_market(args.provider)
        except Exception as e:  # noqa: BLE001
            print(f"[init-week] market fetch failed ({e}); seeding from nflverse spreads only")
    df = build_week_file(games, season, week, market, overwrite=args.overwrite)
    print(f"Wrote {league_path(season, week)} with {len(df)} games. "
          f"Edit home_spread to match the league sheet if it differs.")
    print(df.to_string(index=False))
    if market is not None:
        st.append_snapshot(season, week, market)
    return 0


def cmd_update(args) -> int:
    games = load_games()
    season, week = _resolve_week(args, games)
    league = load_week_file(season, week)
    market = fetch_market(args.provider)
    st.append_snapshot(season, week, market)
    s = st.load_state(season, week)
    current = st.picks_from_state(s)
    board = build_board(league, market)
    picks = recommend(board, current)
    diff = diff_picks(current, picks)
    title, body = alerts.format_alert(season, week, picks, diff, board)
    alerts.send(title, body, force=args.force_alert, changed=diff["changed"])
    s["recommended"] = st.picks_to_state(picks)
    s["last_run"] = st.now_iso()
    s["history"].append({"at": s["last_run"], "changed": diff["changed"],
                         "picks": [f"{p.team}:{p.edge:+.1f}" for p in picks]})
    # a confirmation is consumed once the recommendation agrees with it
    for gid in list(s.get("confirmed", {})):
        rec = next((p for p in picks if p.game_id == gid), None)
        if rec and rec.side == s["confirmed"][gid]["side"]:
            del s["confirmed"][gid]
    st.save_state(season, week, s)
    _print_board(board)
    return 0


def cmd_status(args) -> int:
    games = load_games()
    season, week = _resolve_week(args, games)
    league = load_week_file(season, week)
    s = st.load_state(season, week)
    print(f"Week {config.week_key(season, week)}  last run: {s.get('last_run')}")
    for i, p in enumerate(st.picks_from_state(s), 1):
        print(f"  {i}. {p.team} ({p.side}) edge {p.edge:+.1f}{' LOCKED' if p.locked else ''}")
    print(league.to_string(index=False))
    return 0


def cmd_confirm(args) -> int:
    games = load_games()
    season, week = _resolve_week(args, games)
    league = load_week_file(season, week)
    r = _find_game(league, args.team)
    if r is None:
        print(f"{args.team.upper()} is not on this week's slate")
        return 1
    team = normalize(args.team)
    side = HOME if r.home == team else AWAY
    s = st.load_state(season, week)
    if args.remove:
        s.setdefault("confirmed", {}).pop(r.game_id, None)
        s["recommended"] = [p for p in s.get("recommended", []) if p["game_id"] != r.game_id]
        print(f"Removed {team} from your picks")
    else:
        s.setdefault("confirmed", {})[r.game_id] = {"side": side, "team": team}
        print(f"Recorded that you have {team} picked ({side})")
    st.save_state(season, week, s)
    return 0


def _find_game(league: pd.DataFrame, team: str):
    try:
        team = normalize(team)
    except ValueError:
        return None
    row = league[(league.home == team) | (league.away == team)]
    return None if row.empty else row.iloc[0]


def cmd_set_line(args) -> int:
    """Enter a line from the named team's perspective, e.g. `set-line IND -3.5`
    means IND is favored by 3.5 whether IND is home or away."""
    games = load_games()
    season, week = _resolve_week(args, games)
    league = load_week_file(season, week)
    r = _find_game(league, args.team)
    if r is None:
        print(f"{args.team.upper()} is not on this week's slate")
        return 1
    team = normalize(args.team)
    spread = 0.0 if args.spread.upper() in ("PK", "EVEN") else float(args.spread)
    home_spread = spread if r.home == team else -spread
    league.loc[league.game_id == r.game_id, "home_spread"] = home_spread
    out = league.copy()
    out["kickoff_utc"] = out["kickoff_utc"].map(lambda d: d.isoformat())
    out.to_csv(league_path(season, week), index=False)
    opp = r.away if r.home == team else r.home
    print(f"Set {fmt_pick(team, opp, home_spread, r.home == team)}  (home_spread {home_spread:+g})")
    return 0


def cmd_score_week(args) -> int:
    """Score the recommended/confirmed picks with final results, app style."""
    from cover5.scoring import pick_score
    games = load_games(force=True)
    season, week = _resolve_week(args, games)
    league = load_week_file(season, week).set_index("game_id")
    results = games[(games.season == season) & (games.week == week)].set_index("game_id")
    picks = st.picks_from_state(st.load_state(season, week))
    total, pending = 0.0, 0
    for p in picks:
        lg = league.loc[p.game_id]
        is_home = p.side == HOME
        opp = lg.away if is_home else lg.home
        label = fmt_pick(p.team, opp, lg.home_spread, is_home)
        res = results.loc[p.game_id, "result"] if p.game_id in results.index else float("nan")
        if pd.isna(res):
            print(f"  {label:22s}  pending")
            pending += 1
            continue
        sc = pick_score(float(res), float(lg.home_spread), p.side)
        total += sc
        print(f"  {label:22s}  {sc:+g}")
    print(f"Week {week} total: {total:+g}" + (f"  ({pending} game(s) pending)" if pending else ""))
    return 0


def cmd_backtest(args) -> int:
    from cover5.backtest import run
    run(load_games(), start=args.start, end=args.end)
    return 0


def _print_board(board: pd.DataFrame) -> None:
    show = board[["away", "home", "league_str", "market_str", "best_team", "edge", "books", "locked"]].copy()
    show["edge"] = show["edge"].round(1)
    print("\nBOARD (sorted by edge):")
    print(show.to_string(index=False))


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

    p = sub.add_parser("status", help="show current picks and league lines")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("confirm", help="record the pick you actually have entered")
    p.add_argument("team")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(fn=cmd_confirm)

    p = sub.add_parser("set-line", help="enter a league line from a team's perspective, e.g. IND -3.5")
    p.add_argument("team")
    p.add_argument("spread", help="number from that team's view (-3.5 favored, 2.5 underdog, PK)")
    p.set_defaults(fn=cmd_set_line)

    p = sub.add_parser("score-week", help="score this week's picks from final results")
    p.set_defaults(fn=cmd_score_week)

    p = sub.add_parser("backtest", help="historical checks using nflverse closing lines")
    p.add_argument("--start", type=int, default=2010)
    p.add_argument("--end", type=int, default=2025)
    p.set_defaults(fn=cmd_backtest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
