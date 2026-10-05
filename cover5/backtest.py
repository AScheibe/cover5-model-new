"""Historical sanity checks for the league scoring rule, using nflverse closing lines.

nflverse only carries the closing spread, so this cannot replay Wednesday-vs-close
movement directly. It answers the questions that decide the design instead:

  1. Is the closing line an unbiased predictor of margin (so that movement toward
     it is worth face value in league points)?
  2. How noisy is a 5-pick week, so you know what a given edge is worth?
  3. Do simple selection rules (favorites, dogs, home, a margin-rating model)
     beat picking at random? If not, the market is the model.

The live tracker logs every market read to data/snapshots/, so after a season
you can backtest actual Wednesday-vs-kickoff movement with `replay_snapshots`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _reg(games: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    g = games[(games.game_type == "REG") & games.result.notna() & games.spread_line.notna()]
    g = g[(g.season >= start) & (g.season <= end)].copy()
    # nflverse spread_line is expected home margin; convert to book convention
    g["home_spread"] = -g.spread_line
    g["home_resid"] = g.result + g.home_spread      # = margin - expected margin
    return g


def _weekly_scores(g: pd.DataFrame, score_col: str, rank_col: str, n: int = 5, ascending=False) -> np.ndarray:
    out = []
    for _, d in g.groupby(["season", "week"]):
        d = d.sort_values(rank_col, ascending=ascending).head(n)
        out.append(d[score_col].sum())
    return np.asarray(out)


def margin_ratings(g: pd.DataFrame, k: float = 0.08, home_adv: float = 2.0, regress: float = 0.5):
    """A deliberately simple rolling margin rating (Elo-flavoured, in points).

    Predicted home margin = rating_home - rating_away + home_adv. Ratings update
    toward the observed margin after each game and regress halfway to zero each
    offseason. Returns a Series of pre-game predictions aligned to g.
    """
    g = g.sort_values(["season", "week", "kickoff_utc"])
    ratings: dict[str, float] = {}
    preds = pd.Series(index=g.index, dtype=float)
    last_season = None
    for idx, r in g.iterrows():
        if r.season != last_season:
            ratings = {t: v * regress for t, v in ratings.items()}
            last_season = r.season
        rh, ra = ratings.get(r.home_team, 0.0), ratings.get(r.away_team, 0.0)
        pred = rh - ra + home_adv
        preds[idx] = pred
        err = r.result - pred
        ratings[r.home_team] = rh + k * err
        ratings[r.away_team] = ra - k * err
    return preds


def run(games: pd.DataFrame, start: int = 2010, end: int = 2025, seed: int = 0) -> None:
    g = _reg(games, start, end)
    rng = np.random.default_rng(seed)
    print(f"Regular season games {start}-{end}: {len(g)}  weeks: {g.groupby(['season','week']).ngroups}")

    # 1. unbiasedness
    print("\n1. Closing line vs actual home margin")
    print(f"   mean residual {g.home_resid.mean():+.2f} pts   sd {g.home_resid.std():.2f} pts")
    fav_resid = np.where(g.home_spread < 0, g.home_resid, -g.home_resid)
    g["fav_resid"] = fav_resid
    g["abs_spread"] = g.home_spread.abs()
    buckets = pd.cut(g.abs_spread, [-0.1, 3, 6.5, 9.5, 30], labels=["0-3", "3.5-6.5", "7-9.5", "10+"])
    tab = g.groupby(buckets, observed=True).fav_resid.agg(["count", "mean", "sem"]).round(2)
    tab.columns = ["games", "favorite cover margin", "std err"]
    print("   by spread size (positive = favorites beat the number on average):")
    print(tab.to_string())

    # 2. noise
    print("\n2. Weekly 5-pick score under simple rules (mean / sd across weeks)")
    rules = {}
    g["s_fav"] = g.fav_resid
    g["s_dog"] = -g.fav_resid
    g["s_home"] = g.home_resid
    g["s_away"] = -g.home_resid
    g["rand_side"] = rng.choice([-1, 1], len(g))
    g["s_rand"] = g.home_resid * g.rand_side
    g["rand_rank"] = rng.random(len(g))
    rules["random 5"] = _weekly_scores(g, "s_rand", "rand_rank")
    rules["5 biggest favorites"] = _weekly_scores(g, "s_fav", "abs_spread")
    rules["5 biggest underdogs"] = _weekly_scores(g, "s_dog", "abs_spread")
    rules["5 home teams, highest total"] = _weekly_scores(g, "s_home", "total_line")
    rules["5 away teams, highest total"] = _weekly_scores(g, "s_away", "total_line")

    # 3. a ratings model against the market
    preds = margin_ratings(g)
    g["model_pred"] = preds
    g["model_edge_home"] = g.model_pred - (-g.home_spread)     # model margin minus market margin
    g["s_model"] = np.where(g.model_edge_home > 0, g.home_resid, -g.home_resid)
    g["abs_model_edge"] = g.model_edge_home.abs()
    warm = g[g.season > start]                                 # skip first season warm-up
    rules["5 biggest model-vs-market gaps"] = _weekly_scores(warm, "s_model", "abs_model_edge")
    for name, v in rules.items():
        se = v.std() / np.sqrt(len(v))
        print(f"   {name:34s} mean {v.mean():+6.2f}  sd {v.std():5.2f}  (std err {se:.2f}, {len(v)} weeks)")
    mae_market = (warm.result + warm.home_spread).abs().mean()
    mae_model = (warm.result - warm.model_pred).abs().mean()
    best_w, best_mae = 1.0, mae_market
    for w in np.arange(0.5, 1.001, 0.05):
        blend = w * (-warm.home_spread) + (1 - w) * warm.model_pred
        mae = (warm.result - blend).abs().mean()
        if mae < best_mae - 1e-9:
            best_w, best_mae = w, mae
    print(f"\n3. Margin MAE: market {mae_market:.2f}  rating model {mae_model:.2f}  "
          f"best blend weight on market {best_w:.2f} (MAE {best_mae:.2f})")

    # 4. what an edge is worth
    sd_week = rules["random 5"].std()
    print("\n4. What a per-pick edge is worth over a season (18 weeks), given weekly sd "
          f"of ~{sd_week:.0f} pts")
    for e in (0.5, 1.0, 1.5, 2.0, 3.0):
        season_mean = 5 * e * 18
        season_sd = sd_week * np.sqrt(18)
        print(f"   {e:.1f} pt/pick -> +{season_mean:.0f} pts/season, season sd {season_sd:.0f}, "
              f"P(beat a no-edge picker) ~ {_p_beat(season_mean, season_sd):.0%}")


def _p_beat(mean: float, sd: float) -> float:
    from math import erf, sqrt
    # difference of two season totals has sd * sqrt(2)
    z = mean / (sd * sqrt(2))
    return 0.5 * (1 + erf(z / sqrt(2)))


def replay_snapshots(snapshots: pd.DataFrame, league: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    """Given logged market snapshots for a week, the league lines, and final
    results, score the 'pick at last snapshot before kickoff' policy. Useful once
    a few weeks of data/snapshots exist."""
    from cover5.scoring import best_side, pick_score
    snaps = snapshots.copy()
    snaps["fetched_at"] = pd.to_datetime(snaps.fetched_at, utc=True)
    snaps["kickoff_utc"] = pd.to_datetime(snaps.kickoff_utc, utc=True)
    rows = []
    for lg in league.itertuples():
        s = snaps[(snaps.away == lg.away) & (snaps.home == lg.home) & (snaps.fetched_at < snaps.kickoff_utc)]
        if s.empty:
            continue
        close = s.sort_values("fetched_at").iloc[-1].home_spread
        side, edge = best_side(lg.home_spread, close)
        res = results[(results.away_team == lg.away) & (results.home_team == lg.home)]
        if res.empty or pd.isna(res.iloc[0].result):
            continue
        rows.append({"game": f"{lg.away}@{lg.home}", "league": lg.home_spread, "close": close,
                     "side": side, "edge": edge, "score": pick_score(res.iloc[0].result, lg.home_spread, side)})
    return pd.DataFrame(rows).sort_values("edge", ascending=False)
