"""Backtest: how much is "wait for the closing line" worth against a frozen opening line?

League rule (see cover5.scoring): the league spread is frozen at the OPENING line,
picks lock at each game's kickoff, so a picker can see the CLOSING line before
committing. A pick scores (picked team's margin) + (league spread on the picked team).

Dataset: data/history/nfl_open_close.csv (built by scripts/build_history.py),
sportsbook sign (negative = home favored). Every strategy below uses
open_home_spread as the league line and scores with the actual result.

Strategies (per (season, week) with >= MIN_GAMES games having both lines):
  RANDOM               5 random games, random side (Monte Carlo).
  MOVEMENT-TOP5        side = sign(open - close), the 5 largest |open - close|.
                       Uses the whole week's closes at once (look-ahead for early games).
  MOVEMENT-TOP5-MIN1   as TOP5, but only games that moved >= 1 point; leftover
                       slots are filled at random (Monte Carlo).
  MOVEMENT-SEQ-T1      look-ahead-free version: walk kickoff slots in order, take a
                       game when |move| >= 1 or when the remaining slots must be filled.
  FAVORITES-TOP5 /     controls: the 5 biggest opening favorites, pick the
  UNDERDOGS-TOP5       favorite / the dog.

Run:  python -m cover5.backtest_movement
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from cover5.scoring import AWAY, HOME, home_edge, pick_score

ROOT = Path(__file__).resolve().parents[1]
HISTORY_CSV = ROOT / "data" / "history" / "nfl_open_close.csv"
GAMES_CSV = ROOT / "data" / "cache" / "games.csv"
RESULTS_JSON = ROOT / "data" / "history" / "backtest_results.json"

N_PICKS = 5
MIN_GAMES = 8
SEASON_WEEKS = 18
ERAS = [("2007-2012", 2007, 2012), ("2013-2018", 2013, 2018), ("2019+", 2019, 9999)]
BUCKETS = ["0", "0.5", "1", "1.5", "2", "2.5+"]


# --------------------------------------------------------------------------- data
def load_history(path: Path | str = HISTORY_CSV, games_path: Path | str | None = GAMES_CSV) -> pd.DataFrame:
    """Read the open/close history and attach a kickoff timestamp (gametime from nflverse)."""
    df = pd.read_csv(path)
    if games_path is not None and Path(games_path).exists() and "gametime" not in df.columns:
        g = pd.read_csv(games_path, usecols=["game_id", "season", "week", "game_type", "gameday",
                                             "gametime", "home_score"])
        df = df.merge(g[["game_id", "gametime"]], on="game_id", how="left")
        # Drop in-progress weeks (REG games still unplayed on/after the latest played date):
        # their pick pool is truncated to the early slate, which no real picker faces.
        reg = g[g.game_type == "REG"]
        last = reg.loc[reg.home_score.notna(), "gameday"].max()
        live = reg[reg.home_score.isna() & (reg.gameday >= last)][["season", "week"]].drop_duplicates()
        if len(live):
            key = pd.MultiIndex.from_frame(df[["season", "week"]])
            df = df[~key.isin(pd.MultiIndex.from_frame(live))].reset_index(drop=True)
    return df


def prepare(df: pd.DataFrame, min_games: int = MIN_GAMES) -> pd.DataFrame:
    """Keep played games with both lines, add per-game quantities, drop thin weeks."""
    d = df.dropna(subset=["open_home_spread", "close_home_spread", "home_score", "away_score"]).copy()
    if "gametime" not in d.columns:
        d["gametime"] = np.nan
    d["gametime"] = d["gametime"].fillna("00:00")
    d["kick"] = pd.to_datetime(d["gameday"].astype(str) + " " + d["gametime"].astype(str)).astype("int64")
    d["home_margin"] = d["home_score"] - d["away_score"]
    # league points from picking HOME at the opening line (AWAY gets the negative)
    d["home_pts"] = [pick_score(m, o, HOME) for m, o in zip(d.home_margin, d.open_home_spread)]
    d["edge"] = [home_edge(o, c) for o, c in zip(d.open_home_spread, d.close_home_spread)]
    d["abs_edge"] = d["edge"].abs()
    d["move_sign"] = np.where(d["edge"] >= 0, 1.0, -1.0)  # zero move -> HOME (EV 0 either way)
    d["move_pts"] = d["move_sign"] * d["home_pts"]
    n = d.groupby(["season", "week"])["game_id"].transform("size")
    d = d[n >= min_games]
    # canonical in-week ranking order: |edge| desc, later kickoff first, home team A-Z
    return d.sort_values(["season", "week"]).reset_index(drop=True)


def _ranked(w: pd.DataFrame, col: str) -> pd.DataFrame:
    return w.sort_values([col, "kick", "home"], ascending=[False, False, True], kind="mergesort")


# --------------------------------------------------------------------- strategies
# Each strategy returns an (n_weeks x k) matrix of weekly scores; k = 1 when the
# strategy is deterministic, k = reps when it involves Monte Carlo.

def _random_fill(pts: np.ndarray, n_pick: int, reps: int, rng: np.random.Generator) -> np.ndarray:
    """Scores of n_pick random games from `pts` (home-side points) with random sides."""
    if n_pick <= 0:
        return np.zeros(reps)
    n = len(pts)
    idx = np.argsort(rng.random((reps, n)), axis=1)[:, :n_pick]
    sides = rng.choice(np.array([-1.0, 1.0]), size=(reps, n_pick))
    return (sides * pts[idx]).sum(axis=1)


def strat_random(weeks, reps, rng):
    return np.vstack([_random_fill(w.home_pts.to_numpy(), N_PICKS, reps, rng) for w in weeks])


def strat_movement_top5(weeks):
    return np.array([[_ranked(w, "abs_edge").head(N_PICKS).move_pts.sum()] for w in weeks])


def strat_movement_min1(weeks, reps, rng, threshold=1.0):
    rows = []
    for w in weeks:
        r = _ranked(w, "abs_edge")
        moved = r[r.abs_edge >= threshold].head(N_PICKS)
        base = moved.move_pts.sum()
        rest = r[r.abs_edge < threshold].home_pts.to_numpy()
        k = N_PICKS - len(moved)
        rows.append(np.full(reps, base) if k == 0 else base + _random_fill(rest, k, reps, rng))
    return np.vstack(rows)


def sequential_picks(w: pd.DataFrame, threshold: float = 1.0) -> pd.DataFrame:
    """Look-ahead-free picks: at each kickoff slot only that slot's closes are known.

    Take every game in the slot with |move| >= threshold (best first) while slots
    remain; if the later slots have fewer games than open slots, force the best
    games of this slot in.
    """
    picks, left = [], N_PICKS
    slots = sorted(w.kick.unique())
    for i, k in enumerate(slots):
        if left == 0:
            break
        s = _ranked(w[w.kick == k], "abs_edge")
        later = int((w.kick > k).sum())
        forced = max(0, left - later)
        n_take = min(left, max(forced, int((s.abs_edge >= threshold).sum())))
        picks.append(s.head(n_take))
        left -= n_take
    return pd.concat(picks) if picks else w.head(0)


def strat_movement_seq(weeks, threshold=1.0):
    return np.array([[sequential_picks(w, threshold).move_pts.sum()] for w in weeks])


def strat_favorites(weeks, pick_dog=False):
    out = []
    for w in weeks:
        w = w.assign(fav_size=w.open_home_spread.abs())
        top = _ranked(w, "fav_size").head(N_PICKS)
        fav_sign = np.where(top.open_home_spread <= 0, 1.0, -1.0)  # home favored -> HOME
        sign = -fav_sign if pick_dog else fav_sign
        out.append([(sign * top.home_pts).sum()])
    return np.array(out)


# ----------------------------------------------------------------------- summary
def _cluster_se(x: np.ndarray, groups: np.ndarray) -> float | None:
    """Cluster-robust (by season) standard error of the mean of x."""
    n = len(x)
    if n < 2 or len(set(groups.tolist())) < 10:  # too few clusters -> cluster se is unreliable
        return None
    u = pd.Series(x - x.mean()).groupby(groups).sum().to_numpy()
    g = len(u)
    return float(math.sqrt((u ** 2).sum() * g / (g - 1)) / n)


def summarize(mat: np.ndarray, rand: np.ndarray, rng: np.random.Generator, trials: int,
              seasons: np.ndarray | None = None) -> dict:
    exp_week = mat.mean(axis=1)
    n = len(exp_week)
    sd_single = float(mat.std(ddof=1)) if mat.size > 1 else 0.0
    sd_exp = float(exp_week.std(ddof=1)) if n > 1 else 0.0
    # season sim: 18 weeks with replacement for the strategy and an independent random picker
    wi = rng.integers(n, size=(trials, SEASON_WEEKS))
    ci = rng.integers(mat.shape[1], size=(trials, SEASON_WEEKS))
    s = mat[wi, ci].sum(axis=1)
    wr = rng.integers(rand.shape[0], size=(trials, SEASON_WEEKS))
    cr = rng.integers(rand.shape[1], size=(trials, SEASON_WEEKS))
    r = rand[wr, cr].sum(axis=1)
    # single-week head to head (strategy week vs independent random week)
    wk = mat[wi[:, 0], ci[:, 0]] - rand[wr[:, 0], cr[:, 0]]
    return {
        "weeks": int(n),
        "mean_week": float(exp_week.mean()),
        "sd_week": sd_single,                      # sd of one realized week
        "sd_week_expected": sd_exp,                # sd across weeks of E[week score]
        "se_mean_week": sd_exp / math.sqrt(n) if n > 1 else 0.0,
        "mean_season": float(exp_week.mean() * SEASON_WEEKS),
        "sd_season_sim": float(s.std(ddof=1)),
        "p_beat_random_season": float((s > r).mean() + 0.5 * (s == r).mean()),
        "p_beat_random_week": float((wk > 0).mean() + 0.5 * (wk == 0).mean()),
        "p_week_positive": float((mat > 0).mean()),
        **_paired_stats(exp_week, rand, seasons),
    }


def _paired_stats(exp_week: np.ndarray, rand: np.ndarray, seasons: np.ndarray | None) -> dict:
    """Week-paired difference vs the random picker's expected week, with z-scores.

    se_diff is the iid-weeks standard error; se_diff_cluster_season clusters weeks by season.
    The random picker's exact expectation is 0, so diff differs from mean_week only by MC noise.
    """
    if rand.shape[0] != len(exp_week):
        return {}
    diff = exp_week - rand.mean(axis=1)
    n = len(diff)
    se = float(diff.std(ddof=1) / math.sqrt(n)) if n > 1 else None
    se_c = _cluster_se(diff, seasons) if seasons is not None and len(seasons) == n else None
    return {
        "mean_diff_vs_random": float(diff.mean()),
        "se_diff": se,
        "z_vs_random": float(diff.mean() / se) if se else None,
        "se_diff_cluster_season": se_c,
        "z_vs_random_cluster_season": float(diff.mean() / se_c) if se_c else None,
    }


def oracle_buckets(d: pd.DataFrame) -> dict:
    """Mean league points of the movement side by |open - close| bucket."""
    b = np.minimum(np.round(d.abs_edge * 2) / 2, 2.5)
    lab = np.where(b >= 2.5, "2.5+", b.map(lambda x: f"{x:g}"))
    rows = []
    for name in BUCKETS:
        g = d[lab == name]
        pts = g.move_pts.to_numpy()
        n = len(pts)
        rows.append({
            "bucket": name,
            "n": int(n),
            "mean_abs_edge": float(g.abs_edge.mean()) if n else None,
            "mean_move_side_pts": float(pts.mean()) if n else None,
            "se": float(pts.std(ddof=1) / math.sqrt(n)) if n > 1 else None,
            "cover_rate": float(((pts > 0).sum() + 0.5 * (pts == 0).sum()) / n) if n else None,
            # realized points per point of movement (1.0 = market move is worth face value)
            "pts_per_edge_point": float(pts.mean() / g.abs_edge.mean()) if n and g.abs_edge.mean() > 0 else None,
            "pts_per_edge_point_se": (float(pts.std(ddof=1) / math.sqrt(n) / g.abs_edge.mean())
                                      if n > 1 and g.abs_edge.mean() > 0 else None),
        })
    e, h = d.edge.to_numpy(), d.home_pts.to_numpy()
    slope0 = float((e * h).sum() / (e * e).sum())
    resid = h - slope0 * e
    se0 = float(math.sqrt((resid ** 2).sum() / (len(e) - 1) / (e * e).sum()))
    X = np.column_stack([np.ones_like(e), e])
    coef, *_ = np.linalg.lstsq(X, h, rcond=None)
    # heteroskedasticity-robust (HC1) standard errors for the OLS fit and the no-intercept slope
    r1 = h - X @ coef
    xtxi = np.linalg.inv(X.T @ X)
    meat = (X * r1[:, None]).T @ (X * r1[:, None])
    se_ols = np.sqrt(np.diag(xtxi @ meat @ xtxi) * len(h) / (len(h) - 2))
    se0_hc = float(math.sqrt(((e * resid) ** 2).sum()) / (e * e).sum())

    def _moved(sub):
        p = sub.move_pts.to_numpy()
        if len(p) < 2:
            return None
        se = float(p.std(ddof=1) / math.sqrt(len(p)))
        return {"n": int(len(p)), "mean_move_side_pts": float(p.mean()), "se": se,
                "z": float(p.mean() / se) if se else None, "mean_abs_edge": float(sub.abs_edge.mean())}

    return {
        "slope_se_hc1": se0_hc,
        "z_slope_eq_1": float((slope0 - 1) / se0_hc) if se0_hc else None,
        "ols_se_hc1": [float(se_ols[0]), float(se_ols[1])],
        "moved_ge1_games": _moved(d[d.abs_edge >= 1]),
        "moved_ge1_games_2013+": _moved(d[(d.abs_edge >= 1) & (d.season >= 2013)]),
        "note": "bucket 0 = no move; its side is HOME, so its mean is the home-side points at the open",
        "buckets": rows,
        "slope_home_pts_on_edge_through_origin": slope0,
        "slope_se": se0,
        "ols_intercept_slope": [float(coef[0]), float(coef[1])],
    }


def run(df: pd.DataFrame | None = None, reps: int = 4000, trials: int = 20000, seed: int = 0,
        out_path: Path | str | None = None, seq_threshold: float = 1.0) -> dict:
    rng = np.random.default_rng(seed)
    raw = load_history() if df is None else df
    d = prepare(raw)
    groups = list(d.groupby(["season", "week"], sort=True))
    keys = [k for k, _ in groups]
    weeks = [w for _, w in groups]
    seasons = np.array([k[0] for k in keys])

    rand = strat_random(weeks, reps, rng)
    mats = {
        "RANDOM": rand,
        "MOVEMENT-TOP5": strat_movement_top5(weeks),
        "MOVEMENT-TOP5-MIN1": strat_movement_min1(weeks, reps, rng),
        f"MOVEMENT-SEQ-T{seq_threshold:g}": strat_movement_seq(weeks, seq_threshold),
        "FAVORITES-TOP5": strat_favorites(weeks),
        "UNDERDOGS-TOP5": strat_favorites(weeks, pick_dog=True),
    }
    strategies = {name: summarize(m, rand, rng, trials, seasons) for name, m in mats.items()}
    strategies["RANDOM"]["sd_single_random_week"] = strategies["RANDOM"]["sd_week"]

    top5 = mats["MOVEMENT-TOP5"][:, 0]
    by_era = {}
    for label, lo, hi in ERAS:
        m = (seasons >= lo) & (seasons <= hi)
        if m.any():
            by_era[label] = summarize(mats["MOVEMENT-TOP5"][m], rand[m], rng, trials, seasons[m])
    per_season = {}
    for s in sorted(set(seasons)):
        m = seasons == s
        x = top5[m]
        per_season[str(int(s))] = {
            "weeks": int(m.sum()),
            "mean_week": float(x.mean()),
            "se": float(x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 1 else None,
            "total": float(x.sum()),
            "n_moved_ge1_per_week": float(np.mean([(w.abs_edge >= 1).sum() for w, ok in zip(weeks, m) if ok])),
        }

    # sensitivity: data-quality exclusions and the sequential threshold
    sens = {}
    for label, mask in [("excl_2022", seasons != 2022), ("excl_2022_2026", ~np.isin(seasons, [2022, 2026])),
                        ("2007-2021_only", seasons <= 2021)]:
        sens[f"MOVEMENT-TOP5[{label}]"] = summarize(mats["MOVEMENT-TOP5"][mask], rand[mask], rng, trials)
    try:
        val = json.loads((HISTORY_CSV.parent / "nfl_open_close.validation.json").read_text())
        suspects = set(val.get("open_sign_flip_suspects_kept", []))
    except (OSError, ValueError):
        suspects = set()
    if suspects:
        d2 = d[~d.game_id.isin(suspects)]
        w2 = [w for _, w in d2.groupby(["season", "week"], sort=True)]
        sens["MOVEMENT-TOP5[excl_sign_flip_suspects]"] = summarize(strat_movement_top5(w2), rand, rng, trials)
        sens["MOVEMENT-TOP5[excl_sign_flip_suspects]"]["n_games_excluded"] = int(d.game_id.isin(suspects).sum())
    for t in (0.5, 1.5, 2.0):
        sens[f"MOVEMENT-SEQ-T{t:g}"] = summarize(strat_movement_seq(weeks, t), rand, rng, trials)

    # 2013+ restriction (separate rng stream: earlier P values are unchanged by this block)
    rng2 = np.random.default_rng(seed + 1)
    m13 = seasons >= 2013
    if m13.any():
        for name in ("MOVEMENT-TOP5", "MOVEMENT-TOP5-MIN1", f"MOVEMENT-SEQ-T{seq_threshold:g}"):
            sens[f"{name}[2013+]"] = summarize(mats[name][m13], rand[m13], rng2, trials, seasons[m13])

    # how often the look-ahead TOP5 and the sequential rule agree on the 5 games
    overlap = float(np.mean([
        len(set(_ranked(w, "abs_edge").head(N_PICKS).game_id) & set(sequential_picks(w, seq_threshold).game_id))
        for w in weeks]))

    out = {
        "dataset": str(HISTORY_CSV) if df is None else "<dataframe>",
        "games_used": int(len(d)),
        "weeks_used": len(weeks),
        "seasons": [int(seasons.min()), int(seasons.max())] if len(seasons) else None,
        "min_games_per_week": MIN_GAMES,
        "mc_reps_per_week": reps,
        "season_trials": trials,
        "seed": seed,
        "random_expected_week_analytic": 0.0,
        "strategies": strategies,
        "movement_top5_by_era": by_era,
        "movement_top5_per_season": per_season,
        "sensitivity": sens,
        "top5_vs_sequential_mean_overlap_games": overlap,
        "oracle_check": oracle_buckets(d),
    }
    if out_path is not None:
        Path(out_path).write_text(json.dumps(out, indent=2))
    return out


def format_table(res: dict) -> str:
    lines = []
    h = f"{'strategy':<40}{'weeks':>6}{'mean/wk':>9}{'sd/wk':>8}{'se':>7}{'season':>8}{'P>rand':>8}{'z':>7}{'z_cl':>7}"
    lines += [f"games={res['games_used']} weeks={res['weeks_used']} seasons={res['seasons']}", h, "-" * len(h)]

    def row(name, s):
        return (f"{name:<40}{s['weeks']:>6}{s['mean_week']:>9.2f}{s['sd_week']:>8.2f}"
                f"{s['se_mean_week']:>7.2f}{s['mean_season']:>8.1f}{s['p_beat_random_season']:>8.3f}"
                f"{_f(s.get('z_vs_random'))}{_f(s.get('z_vs_random_cluster_season'))}")

    def _f(v):
        return f"{v:>7.2f}" if v is not None else f"{'-':>7}"

    for name, s in res["strategies"].items():
        lines.append(row(name, s))
    lines += ["", "MOVEMENT-TOP5 by era"] + [row(k, s) for k, s in res["movement_top5_by_era"].items()]
    lines += ["", "Sensitivity"] + [row(k, s) for k, s in res["sensitivity"].items()]
    lines += ["", f"{'season':<8}{'weeks':>6}{'mean/wk':>9}{'se':>7}{'total':>8}{'moved>=1/wk':>13}"]
    for k, s in res["movement_top5_per_season"].items():
        se = s["se"] if s["se"] is not None else float("nan")
        lines.append(f"{k:<8}{s['weeks']:>6}{s['mean_week']:>9.2f}{se:>7.2f}{s['total']:>8.1f}"
                     f"{s['n_moved_ge1_per_week']:>13.1f}")
    oc = res["oracle_check"]
    lines += ["", "Oracle check: movement-side league points per game by |open-close|",
              f"{'|edge|':<8}{'n':>6}{'mean|e|':>9}{'mean pts':>10}{'se':>7}{'cover%':>8}{'pts/edge':>10}{'se':>7}"]
    for b in oc["buckets"]:
        if b["n"]:
            lines.append(f"{b['bucket']:<8}{b['n']:>6}{b['mean_abs_edge']:>9.2f}{b['mean_move_side_pts']:>10.2f}"
                         f"{(b['se'] or 0):>7.2f}{100 * b['cover_rate']:>8.1f}"
                         + (f"{b['pts_per_edge_point']:>10.2f}{b['pts_per_edge_point_se']:>7.2f}"
                            if b["pts_per_edge_point"] is not None else ""))
    lines.append(f"slope(home pts ~ edge, no intercept) = {oc['slope_home_pts_on_edge_through_origin']:.3f}"
                 f" +- {oc['slope_se']:.3f} (HC1 {oc['slope_se_hc1']:.3f}; z for slope=1: {oc['z_slope_eq_1']:.2f})")
    a, b1 = oc["ols_intercept_slope"]
    sa, sb = oc["ols_se_hc1"]
    lines.append(f"OLS home pts = {a:.3f} (+- {sa:.3f}) + {b1:.3f} (+- {sb:.3f}) * edge")
    for k in ("moved_ge1_games", "moved_ge1_games_2013+"):
        m = oc.get(k)
        if m:
            lines.append(f"{k}: n={m['n']} movement-side pts/game {m['mean_move_side_pts']:.2f} +- {m['se']:.2f}"
                         f" (z {m['z']:.2f}), mean|edge| {m['mean_abs_edge']:.2f}")
    lines.append(f"TOP5 vs SEQ-T1 mean overlap: {res['top5_vs_sequential_mean_overlap_games']:.2f} of 5 games")
    return "\n".join(lines)


if __name__ == "__main__":
    res = run(out_path=RESULTS_JSON)
    print(format_table(res))
    print(f"\nwrote {RESULTS_JSON}")
