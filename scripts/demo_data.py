"""Build a self-contained Cover 5 data root for demos and end-to-end tests.

    python scripts/demo_data.py /tmp/cover5-demo [--force]

The root gets:

* ``data/cache/games.csv`` copied from this repo's nflverse cache, so the app
  runs fully offline.
* The REAL current week (``service.resolve_week``) initialised from nflverse
  spreads (``use_market=False``), plus two market snapshots: one from the
  Wednesday the league lines came out and one from now, with a few games moved
  0.5 to 3 points. Both are saved as model runs, so the week has picks, an
  alert log and a "Do this" change.
* ``market.json``: the market the "file" provider serves when you press
  Fetch lines (the same lines as the second snapshot).
* The two previous weeks of the same season, initialised and picked the same
  way on their own Wednesdays, graded with real nflverse results, with
  standings filled in for the older one so History has records.

It never touches this repo's own data/ folder: cover5 is imported only after
COVER5_ROOT points at the new root. At the end it prints the environment
variables to run the app against it.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
SOURCE_GAMES = REPO / "data" / "cache" / "games.csv"
ET = ZoneInfo("America/New_York")


def env_for(root: Path) -> dict[str, str]:
    return {
        "COVER5_ROOT": str(root),
        "COVER5_OFFLINE": "1",
        "COVER5_PROVIDER": "file",
        "COVER5_MARKET_FILE": str(root / "market.json"),
        # never push demo alerts anywhere
        "NTFY_TOPIC": "",
        "ALERT_WEBHOOK_URL": "",
    }


def wednesday_noon_before(kickoff: datetime) -> datetime:
    """Noon ET on the last Wednesday strictly before ``kickoff`` (when league lines come out)."""
    k = kickoff.astimezone(ET)
    d = k.date()
    while True:
        if d.weekday() == 2:
            noon = datetime(d.year, d.month, d.day, 12, tzinfo=ET)
            if noon < k:
                return noon.astimezone(timezone.utc)
        d -= timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="folder to create (holds data/ and market.json)")
    ap.add_argument("--force", action="store_true", help="delete the folder first if it exists")
    ap.add_argument("--quiet", action="store_true", help="print only the environment variables")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    if root == REPO or REPO / "data" in (root, *root.parents) or root == REPO / "data":
        print("refusing to build demo data inside the repo's real data folder", file=sys.stderr)
        return 2
    if root.exists() and any(root.iterdir()):
        if not args.force:
            print(f"{root} exists and is not empty (use --force to replace it)", file=sys.stderr)
            return 2
        shutil.rmtree(root)
    if not SOURCE_GAMES.exists():
        print(f"no schedule cache at {SOURCE_GAMES}; run the app once online first", file=sys.stderr)
        return 2
    (root / "data" / "cache").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_GAMES, root / "data" / "cache" / "games.csv")

    env = env_for(root)
    os.environ.update(env)
    sys.path.insert(0, str(REPO))
    # Only now import cover5: config reads COVER5_ROOT at import time.
    from cover5 import config, history, service
    from cover5 import state as st
    from cover5.providers import MarketLine, parse_file
    from cover5.schedule import week_slate

    assert config.ROOT == root, (config.ROOT, root)
    log = (lambda *a: None) if args.quiet else print

    g = service.games()
    season, week = service.resolve_week(None, None, g)
    now = datetime.now(timezone.utc)
    rng = random.Random(season * 100 + week)

    def market_rows(league, moves: dict[str, float], fetched: datetime, source: str) -> list[MarketLine]:
        out = []
        for r in league.itertuples():
            if r.home_spread != r.home_spread:      # NaN: no line to move
                continue
            hs = float(r.home_spread) + moves.get(r.game_id, 0.0)
            out.append(MarketLine(r.away, r.home, r.kickoff_utc, hs, rng.randint(4, 9), source, fetched))
        return out

    def random_moves(league, n: int, lo: float, hi: float, games: list[str] | None = None) -> dict[str, float]:
        ids = list(games if games is not None else league.game_id)
        rng.shuffle(ids)
        steps = [x / 2 for x in range(int(lo * 2), int(hi * 2) + 1)]
        return {gid: rng.choice(steps) * rng.choice((-1, 1)) for gid in ids[:n]}

    def run_at(s: int, wk: int, market: list[MarketLine], at: datetime, reason: str = "market update"):
        """Log a snapshot and save a model run as if it happened at ``at``."""
        real_now_iso = st.now_iso
        st.now_iso = lambda: at.isoformat(timespec="seconds")
        try:
            with service.LOCK:
                st.append_snapshot(s, wk, market)
                service.compute(s, wk, market, reason=reason, alert=True, save=True, now=at)
        finally:
            st.now_iso = real_now_iso

    # ---- two earlier, completed weeks: picked on their Wednesday, graded by nflverse
    past = [w for w in (week - 2, week - 1) if w >= 1]
    for wk in past:
        service.init_week(season, wk, use_market=False)
        league = service._load_league(season, wk)
        wed = wednesday_noon_before(min(league.kickoff_utc))
        moves = random_moves(league, 7, 0.5, 2.5)
        run_at(season, wk, market_rows(league, moves, wed + timedelta(hours=1), "demo"), wed + timedelta(hours=1))
        later = {**moves, **random_moves(league, 3, 0.5, 1.5)}
        run_at(season, wk, market_rows(league, later, wed + timedelta(days=2, hours=6), "demo"),
               wed + timedelta(days=2, hours=6))
        log(f"  {season} week {wk}: picks {[p['team'] for p in service.week_view(season, wk)['picks']]}")

    # ---- the real current week
    service.init_week(season, week, use_market=False)
    league = service._load_league(season, week)
    first_kick = min(league.kickoff_utc)
    t_b = now - timedelta(minutes=10)
    t_a = min(wednesday_noon_before(first_kick) + timedelta(hours=1), t_b - timedelta(hours=2))
    # Games still open now: run A picks one of them (the biggest Wednesday move),
    # run B takes that move back and moves another open game 3 points, so the
    # model switches the pick (or flips its side when only one game is open) and
    # the week has a real "Do this" change.
    open_ids = [r.game_id for r in league.sort_values("kickoff_utc").itertuples()
                if r.kickoff_utc > now + timedelta(minutes=30)]
    moves_a = random_moves(league, 6, 0.5, 2.0, [r for r in league.game_id if r not in open_ids[:2]])
    if open_ids:
        moves_a[open_ids[0]] = 2.5 * rng.choice((-1, 1))
    run_at(season, week, market_rows(league, moves_a, t_a, "demo"), t_a)

    moves_b = dict(moves_a)
    if len(open_ids) >= 2:
        moves_b[open_ids[0]] = 0.0
        moves_b[open_ids[1]] = 3.0 * rng.choice((-1, 1))
    elif open_ids:
        moves_b[open_ids[0]] = -moves_a[open_ids[0]]
    lines_b = market_rows(league, moves_b, now, "demo")
    market_file = root / "market.json"
    market_file.write_text(json.dumps([
        {"away": m.away, "home": m.home, "kickoff_utc": m.kickoff_utc.isoformat(),
         "home_spread": m.home_spread, "books": m.books, "source": "demo"} for m in lines_b], indent=2))
    assert len(parse_file(json.loads(market_file.read_text()))) == len(lines_b)
    # A real fetch through the file provider, like pressing "Fetch lines" now.
    out = service.update(season, week)
    log(f"  {season} week {week}: picks {[p['team'] for p in out.view['picks']]}"
        f" (changed: {out.alert['changed'] if out.alert else False})")

    # ---- history records, with standings for the older week only (the newer one is for you to fill in)
    service.refresh_history([season])
    if past:
        n = len(week_slate(g, season, past[0]))
        history.set_manual(season, past[0], week_rank=3, entrants=10, overall_points=float(
            round(history.get_week(season, past[0])["final_points"] + 18.5, 1)), overall_rank=2,
            notes=f"demo record ({n} games)")

    log(f"Demo data for {season} week {week} built in {root}")
    log("Run the app against it with:")
    for k, v in env.items():
        print(f"export {k}={v}" if v else f"export {k}=")
    log("python3 -m cover5 serve --no-scheduler")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
