"""The league's frozen Wednesday lines, one CSV per week.

CSV columns: game_id, away, home, kickoff_utc, home_spread
``home_spread`` is the number the league sheet shows next to the home team
(negative = home favored). Edit the file by hand if the league's sheet differs
from the market snapshot used to seed it.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from cover5 import config
from cover5.providers import MarketLine
from cover5.schedule import week_slate


def league_path(season: int, week: int):
    return config.LEAGUE_DIR / f"{config.week_key(season, week)}.csv"


def build_week_file(games: pd.DataFrame, season: int, week: int,
                    market: list[MarketLine] | None, overwrite: bool = False,
                    keep: dict[str, float] | None = None) -> pd.DataFrame:
    """Seed the week's league-line file from the schedule plus current market lines.

    ``keep`` maps game_id to a home_spread that wins over everything (a re-seed
    keeps the frozen line of every game that has kicked off). Otherwise it uses
    the market, falls back to the nflverse spread when the market has no number
    for a game, and leaves the cell blank if neither exists so you notice and
    fill it in.
    """
    config.ensure_dirs()
    path = league_path(season, week)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass --overwrite to replace it")
    slate = week_slate(games, season, week)
    by_pair = {(m.away, m.home): m.home_spread for m in (market or [])}
    rows = []
    for _, g in slate.iterrows():
        hs = (keep or {}).get(g.game_id)
        if hs is None:
            hs = by_pair.get((g.away_team, g.home_team))
        if hs is None and pd.notna(g.spread_line):
            hs = -float(g.spread_line)   # nflverse spread_line is expected home margin
        rows.append({
            "game_id": g.game_id, "away": g.away_team, "home": g.home_team,
            "kickoff_utc": g.kickoff_utc.isoformat(), "home_spread": hs,
        })
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def load_week_file(season: int, week: int) -> pd.DataFrame:
    path = league_path(season, week)
    if not path.exists():
        raise FileNotFoundError(
            f"No league lines for {config.week_key(season, week)}. Run: python -m cover5 init-week")
    df = pd.read_csv(path)
    df["kickoff_utc"] = df["kickoff_utc"].map(lambda s: datetime.fromisoformat(s))
    missing = df[df["home_spread"].isna()]
    if len(missing):
        print(f"[league] WARNING {len(missing)} game(s) have no league spread in {path.name}: "
              + ", ".join(f"{r.away}@{r.home}" for r in missing.itertuples()))
    return df
