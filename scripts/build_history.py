#!/usr/bin/env python3
"""Build data/history/nfl_open_close.csv: historical NFL opening + closing spreads.

Primary source
    greerreNFL/nfelomarket_data  Data/lines.csv  (the market feed behind the nfelo
    model). One row per game, keyed by an nflverse-style game_id, with
    home_spread_open / home_spread_last in SPORTSBOOK sign (negative = home favoured).
    Seasons 1999-2006 carry open == close (the open is just a copy), so they are
    excluded; real opens start in 2007.

Cross-check source (optional, validation only, not written to the output)
    mrcaseb/nfl-data  data/nfl_lines_odds.csv.gz : per-book opening/closing spreads
    2006-2021 incl. PINNACLE. Used to check what the nfelomarket "open" actually is.

Output columns (exactly):
    season, week, gameday, away, home, open_home_spread, close_home_spread,
    home_score, away_score, game_id
*_home_spread is sportsbook style (-3.0 = home favoured by 3, pk = 0.0). Team codes
are current nflverse codes (LA for STL/LAR, LAC for SD, LV for OAK). game_id is the
nflverse game_id from data/cache/games.csv (it keeps historical codes such as SD/STL).

Regular season (game_type == 'REG') and completed games only.

Usage:  python scripts/build_history.py [--refresh] [--min-season 2007] [--no-crosscheck]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cover5.teams import normalize  # noqa: E402

GAMES_CSV = ROOT / "data" / "cache" / "games.csv"
HIST_DIR = ROOT / "data" / "history"
RAW_DIR = HIST_DIR / "raw"
OUT_CSV = HIST_DIR / "nfl_open_close.csv"
VALIDATION_JSON = HIST_DIR / "nfl_open_close.validation.json"

SOURCES = {
    "nfelomarket_lines.csv": "https://raw.githubusercontent.com/greerreNFL/nfelomarket_data/main/Data/lines.csv",
    "nfl_lines_odds.csv.gz": "https://raw.githubusercontent.com/mrcaseb/nfl-data/master/data/nfl_lines_odds.csv.gz",
}
OUT_COLS = ["season", "week", "gameday", "away", "home", "open_home_spread",
            "close_home_spread", "home_score", "away_score", "game_id"]
FIRST_REAL_OPEN_SEASON = 2007


# --------------------------------------------------------------------------- io
def fetch(name: str, refresh: bool = False) -> Path:
    """Return the local raw file, downloading it if missing (or if refresh)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / name
    if path.exists() and path.stat().st_size > 0 and not refresh:
        return path
    url = SOURCES[name]
    print(f"downloading {url}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    path.write_bytes(r.content)
    meta = {"url": url, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sha256": hashlib.sha256(r.content).hexdigest(), "bytes": len(r.content)}
    (RAW_DIR / f"{name}.source.json").write_text(json.dumps(meta, indent=2))
    return path


def canon(code: str) -> str:
    return normalize(str(code))


# ------------------------------------------------------------------- loading
def load_games() -> pd.DataFrame:
    """Full nflverse schedule (all game types) with canonical team codes."""
    g = pd.read_csv(GAMES_CSV)
    g["away"] = g["away_team"].map(canon)
    g["home"] = g["home_team"].map(canon)
    return g


def load_nfelomarket(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    out = pd.DataFrame({
        "src_game_id": df["game_id"],
        "season": df["season"].astype(int),
        "week": df["week"].astype(int),
        "away": df["away_team"].map(canon),
        "home": df["home_team"].map(canon),
        "open_home_spread": pd.to_numeric(df["home_spread_open"], errors="coerce"),
        "close_home_spread": pd.to_numeric(df["home_spread_last"], errors="coerce"),
        "open_source": df["home_spread_open_source"],
    })
    return out


# ---------------------------------------------------------------------- join
def join_to_games(src: pd.DataFrame, games_all: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Join source rows to the FULL nflverse schedule (all game types).

    The source has no gameday, but its game_id is nflverse-style, so
    (season, week, away, home) on canonical team codes is an exact key (this is
    stricter than a (gameday, away, home) +-1 day match). Joining against every
    game type lets playoff rows be identified and dropped instead of being
    mistaken for join failures. Fallback for unmatched rows: swapped home/away
    (spreads sign-flipped). Rows that still fail are reported.
    """
    key = ["season", "week", "away", "home"]
    g = games_all[key + ["game_type", "gameday", "home_score", "away_score",
                         "game_id", "spread_line"]]
    m = src.merge(g, on=key, how="left", indicator=True)
    hit = m[m["_merge"] == "both"].drop(columns="_merge")
    miss = m[m["_merge"] != "both"][src.columns]

    sw = miss.rename(columns={"away": "home", "home": "away"})
    sw["open_home_spread"] = -sw["open_home_spread"]
    sw["close_home_spread"] = -sw["close_home_spread"]
    m2 = sw.merge(g, on=key, how="left", indicator=True)
    hit2 = m2[m2["_merge"] == "both"].drop(columns="_merge")
    failed = m2[m2["_merge"] != "both"]

    joined = pd.concat([hit, hit2], ignore_index=True)
    dup = joined["game_id"].duplicated(keep=False)
    stats = {
        "source_rows_considered": int(len(src)),
        "matched_direct": int(len(hit)),
        "matched_swapped_home_away": int(len(hit2)),
        "failed": int(len(failed)),
        "failed_ids": failed["src_game_id"].tolist()[:25],
        "duplicate_game_ids": int(dup.sum()),
        "matched_non_reg_dropped": int((joined["game_type"] != "REG").sum()),
    }
    joined = joined[(joined["game_type"] == "REG") & ~dup]
    stats["matched_reg"] = int(len(joined))
    return joined, stats


# ---------------------------------------------------------------- validation
def summarize(df: pd.DataFrame) -> dict:
    d_close = (df["close_home_spread"] - (-df["spread_line"])).abs()
    move = (df["close_home_spread"] - df["open_home_spread"]).abs()
    return {
        "rows": int(len(df)),
        "close_agree_exact": round(float((d_close == 0).mean()), 4),
        "close_agree_within_0.5": round(float((d_close <= 0.5).mean()), 4),
        "close_agree_within_1": round(float((d_close <= 1).mean()), 4),
        "close_mae_vs_nflverse": round(float(d_close.mean()), 4),
        "close_sign_disagrees_with_nflverse": int(((df["close_home_spread"] * df["spread_line"]) > 0).sum()),
        "mean_abs_move": round(float(move.mean()), 4),
        "pct_moved_any": round(float((move > 0).mean()), 4),
        "pct_moved_1plus": round(float((move >= 1).mean()), 4),
    }


def crosscheck_pinnacle(df: pd.DataFrame, refresh: bool) -> dict:
    """Compare our open/close with mrcaseb per-book data (2006-2021)."""
    path = fetch("nfl_lines_odds.csv.gz", refresh)
    x = pd.read_csv(path, na_values=["NA"])
    x = x[x["market_type"] == "spread"].copy()
    x["home_code"] = x["game_id"].str.split("_").str[-1]
    x = x[x["abbr"] == x["home_code"]]
    x["lines"] = pd.to_numeric(x["lines"], errors="coerce")
    x["opening_lines"] = pd.to_numeric(x["opening_lines"], errors="coerce")
    med = x.groupby("game_id").agg(med_open=("opening_lines", "median"),
                                   med_close=("lines", "median"))
    pin = x[x["book"].str.upper() == "PINNACLE"].groupby("game_id").agg(
        pin_open=("opening_lines", "first"), pin_close=("lines", "first"))
    c = df.merge(med, left_on="game_id", right_index=True).merge(
        pin, left_on="game_id", right_index=True, how="left")
    res = {"games_compared": int(len(c))}
    for col in ["pin_open", "med_open"]:
        d = (c["open_home_spread"] - c[col]).abs().dropna()
        res[f"open_vs_{col}"] = {"n": int(len(d)), "exact": round(float((d == 0).mean()), 4),
                                 "within_0.5": round(float((d <= 0.5).mean()), 4),
                                 "mae": round(float(d.mean()), 4)}
    for o, cl in [("pin_open", "pin_close"), ("med_open", "med_close")]:
        mv = (c[cl] - c[o]).abs().dropna()
        res[f"{o.split('_')[0]}_mean_abs_move"] = round(float(mv.mean()), 4)
        res[f"{o.split('_')[0]}_pct_moved_1plus"] = round(float((mv >= 1).mean()), 4)
    return res


# ---------------------------------------------------------------------- main
def build(refresh: bool = False, min_season: int = FIRST_REAL_OPEN_SEASON,
          crosscheck: bool = True, max_close_dev: float = 3.0) -> dict:
    games = load_games()
    src_all = load_nfelomarket(fetch("nfelomarket_lines.csv", refresh))

    # seasons whose "open" is just a copy of the close are useless for open->close work
    by_season = src_all.assign(mv=(src_all.open_home_spread != src_all.close_home_spread)) \
        .groupby("season")["mv"].mean()
    copy_seasons = sorted(int(s) for s, v in by_season.items() if v == 0)
    src = src_all[src_all["season"] >= min_season].copy()

    joined, jstats = join_to_games(src, games)

    no_open = joined["open_home_spread"].isna()
    no_close = joined["close_home_spread"].isna()
    unplayed = joined["home_score"].isna() | joined["away_score"].isna()
    cand = joined[~no_open & ~no_close & ~unplayed].copy()
    pre_filter = summarize(cand)

    # A close that is >= max_close_dev away from nflverse's close is a stale/copied
    # or sign-broken line in the source (e.g. 2020_01_ARI_SF close +7.5 vs SF -7).
    # Drop those rather than patching them with another source's number.
    dev = (cand["close_home_spread"] + cand["spread_line"]).abs()
    bad_close = dev >= max_close_dev
    dropped_bad_close = cand.loc[bad_close, ["game_id", "open_home_spread",
                                             "close_home_spread", "spread_line"]]
    keep = cand[~bad_close].copy()

    keep["season"] = keep["season"].astype(int)
    keep["week"] = keep["week"].astype(int)
    keep["home_score"] = keep["home_score"].astype(int)
    keep["away_score"] = keep["away_score"].astype(int)
    keep["open_home_spread"] = keep["open_home_spread"].astype(float) + 0.0  # -0.0 -> 0.0
    keep["close_home_spread"] = keep["close_home_spread"].astype(float) + 0.0
    keep = keep.sort_values(["season", "week", "gameday", "home"]).reset_index(drop=True)

    # opens that look sign-flipped relative to the close (kept, but listed)
    o, c = keep["open_home_spread"], keep["close_home_spread"]
    flip = keep[(o * c < 0) & ((o + c).abs() <= 1) & (c.abs() >= 3)]["game_id"].tolist()

    reg = games[games["game_type"] == "REG"]
    played = reg[reg["home_score"].notna()]
    per_season = {int(s): summarize(grp) for s, grp in keep.groupby("season")}
    coverage = {int(s): f"{per_season[s]['rows']}/{int((played.season == s).sum())}"
                for s in per_season}
    missing_reg = played[(played.season >= min_season) & ~played.game_id.isin(keep.game_id)]
    report = {
        "source": SOURCES["nfelomarket_lines.csv"],
        "source_rows": int(len(src_all)),
        "copy_open_seasons_excluded": [s for s in copy_seasons if s < min_season],
        "min_season": min_season,
        "join": {**jstats, "join_key": "(season, week, away, home) on canonical codes "
                 "against all nflverse game types; fallback swapped home/away"},
        "dropped_missing_open": int(no_open.sum()),
        "dropped_missing_open_ids": joined.loc[no_open, "game_id"].tolist(),
        "dropped_missing_close": int(no_close.sum()),
        "dropped_unplayed": int((unplayed & ~no_open & ~no_close).sum()),
        "max_close_dev": max_close_dev,
        "dropped_bad_close": int(bad_close.sum()),
        "dropped_bad_close_rows": dropped_bad_close.to_dict("records"),
        "played_reg_games_missing_from_output": int(len(missing_reg)),
        "open_sign_flip_suspects_kept": flip,
        "overall_before_close_filter": pre_filter,
        "overall": summarize(keep),
        "coverage_vs_played_nflverse_reg": coverage,
        "per_season": per_season,
    }
    if crosscheck:
        try:
            report["crosscheck_mrcaseb_2006_2021"] = crosscheck_pinnacle(keep, refresh)
        except Exception as e:  # cross-check is optional
            report["crosscheck_mrcaseb_2006_2021"] = f"skipped: {e!r}"

    keep[OUT_COLS].to_csv(OUT_CSV, index=False, float_format="%.1f")
    VALIDATION_JSON.write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true", help="re-download raw files")
    ap.add_argument("--min-season", type=int, default=FIRST_REAL_OPEN_SEASON)
    ap.add_argument("--no-crosscheck", action="store_true")
    ap.add_argument("--max-close-dev", type=float, default=3.0,
                    help="drop games whose close differs from nflverse close by >= this")
    a = ap.parse_args()
    rep = build(a.refresh, a.min_season, not a.no_crosscheck, a.max_close_dev)
    o = rep["overall"]
    print(f"wrote {OUT_CSV} ({o['rows']} rows, seasons {min(rep['per_season'])}-{max(rep['per_season'])})")
    print("join:", {k: v for k, v in rep["join"].items() if k != "join_key"})
    print(f"dropped: missing open {rep['dropped_missing_open']}, missing close "
          f"{rep['dropped_missing_close']}, unplayed {rep['dropped_unplayed']}, "
          f"close off nflverse by >= {rep['max_close_dev']}: {rep['dropped_bad_close']}")
    print("played REG games (>= min season) missing from output:",
          rep["played_reg_games_missing_from_output"])
    print("open sign-flip suspects kept:", len(rep["open_sign_flip_suspects_kept"]))
    print("overall before close filter:", rep["overall_before_close_filter"])
    print("overall:", o)
    print(f"{'season':>6} {'rows':>9} {'|c+sl|<=1':>9} {'exact':>6} {'mean|mv|':>8} {'moved':>6} {'mv>=1':>6}")
    for s, v in rep["per_season"].items():
        print(f"{s:>6} {rep['coverage_vs_played_nflverse_reg'][s]:>9} {v['close_agree_within_1']:>9.3f} "
              f"{v['close_agree_exact']:>6.3f} {v['mean_abs_move']:>8.3f} {v['pct_moved_any']:>6.3f} "
              f"{v['pct_moved_1plus']:>6.3f}")
    print("crosscheck:", json.dumps(rep.get("crosscheck_mrcaseb_2006_2021"), indent=1))
    print(f"validation report: {VALIDATION_JSON}")


if __name__ == "__main__":
    main()
