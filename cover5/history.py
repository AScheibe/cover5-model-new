"""Historical records in SQLite (data/cover5.db).

Two tables:

* ``runs``  one row per saved recompute: when, why, what changed, the alert
            text, and whether a notification went out. This is the alert log.
* ``weeks`` one row per week: the picks with their graded points, final/live/
            expected totals, the expected points from line movement at lock,
            plus fields only you can fill in from the league app (week rank,
            entrants, overall points and rank, notes). Computed fields are
            refreshed on every save; your fields are never overwritten by it.

The JSON files under data/ stay the source of truth for the live week; this
database is the durable record you browse and chart.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from cover5 import config

MANUAL_FIELDS = ("week_rank", "entrants", "overall_points", "overall_rank", "app_points", "notes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    season    INTEGER NOT NULL,
    week      INTEGER NOT NULL,
    at        TEXT NOT NULL,
    reason    TEXT NOT NULL,
    changed   INTEGER NOT NULL,
    sent      INTEGER NOT NULL DEFAULT 0,
    diff      TEXT NOT NULL,
    picks     TEXT NOT NULL,
    summary   TEXT NOT NULL,
    warnings  TEXT NOT NULL,
    title     TEXT NOT NULL,
    body      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_week ON runs(season, week, at);

CREATE TABLE IF NOT EXISTS weeks (
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    updated_at      TEXT NOT NULL,
    picks           TEXT NOT NULL,
    n_picks         INTEGER NOT NULL,
    final_points    REAL NOT NULL,
    live_points     REAL NOT NULL,
    expected_open   REAL NOT NULL,
    projected       REAL NOT NULL,
    movement_edge   REAL NOT NULL,
    n_final         INTEGER NOT NULL,
    n_live          INTEGER NOT NULL,
    n_open          INTEGER NOT NULL,
    complete        INTEGER NOT NULL,
    week_rank       INTEGER,
    entrants        INTEGER,
    overall_points  REAL,
    overall_rank    INTEGER,
    app_points      REAL,
    notes           TEXT,
    PRIMARY KEY (season, week)
);
"""


def db_path():
    return config.DB_PATH


@contextmanager
def connect():
    config.ensure_dirs()
    con = sqlite3.connect(db_path(), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- runs
def record_run(season: int, week: int, *, at: str, reason: str, changed: bool, diff: dict,
               picks: list, summary: dict, warnings: list, title: str, body: str, sent: bool) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO runs (season, week, at, reason, changed, sent, diff, picks, summary, warnings, title, body)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (season, week, at, reason, int(changed), int(sent), json.dumps(diff), json.dumps(picks),
             json.dumps(summary), json.dumps(warnings), title, body))


def _run_row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "season": r["season"], "week": r["week"], "at": r["at"], "reason": r["reason"],
            "changed": bool(r["changed"]), "sent": bool(r["sent"]), "diff": json.loads(r["diff"]),
            "picks": json.loads(r["picks"]), "summary": json.loads(r["summary"]),
            "warnings": json.loads(r["warnings"]), "title": r["title"], "body": r["body"]}


def list_runs(season: int, week: int, limit: int = 200) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM runs WHERE season=? AND week=? ORDER BY at DESC, id DESC LIMIT ?",
                           (season, week, limit)).fetchall()
    return [_run_row(r) for r in rows]


def last_change(season: int, week: int) -> dict | None:
    """The most recent run that changed the picks: what the user was told to do."""
    with connect() as con:
        r = con.execute("SELECT * FROM runs WHERE season=? AND week=? AND changed=1 ORDER BY at DESC, id DESC LIMIT 1",
                        (season, week)).fetchone()
    if r is None:
        return None
    d = _run_row(r)
    return {"at": d["at"], "reason": d["reason"], "sent": d["sent"], **d["diff"], "body": d["body"]}


# --------------------------------------------------------------------------- weeks
def upsert_week(view: dict) -> None:
    """Store the computed part of a week's record from a service week view."""
    if not view or not view.get("has_league_file"):
        return
    picks = [{k: p.get(k) for k in ("game_id", "team", "opponent", "is_home", "label", "league_spread",
                                     "market_spread", "edge", "status", "points", "source", "locked", "manual")}
             for p in view.get("picks", [])]
    s = view["summary"]
    n = view.get("n_picks", config.N_PICKS)
    complete = int(len(picks) >= n and all(p["status"] == "final" for p in picks))
    row = (view["season"], view["week"], _now(), json.dumps(picks), len(picks),
           float(s["final"]), float(s["live"]), float(s["expected"]), float(s["projected"]),
           float(sum(p["edge"] or 0.0 for p in picks)), int(s["n_final"]), int(s["n_live"]),
           int(s["n_open"]), complete)
    with connect() as con:
        con.execute(
            """INSERT INTO weeks (season, week, updated_at, picks, n_picks, final_points, live_points,
                   expected_open, projected, movement_edge, n_final, n_live, n_open, complete)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(season, week) DO UPDATE SET
                   updated_at=excluded.updated_at, picks=excluded.picks, n_picks=excluded.n_picks,
                   final_points=excluded.final_points, live_points=excluded.live_points,
                   expected_open=excluded.expected_open, projected=excluded.projected,
                   movement_edge=excluded.movement_edge, n_final=excluded.n_final,
                   n_live=excluded.n_live, n_open=excluded.n_open, complete=excluded.complete""",
            row)


def set_manual(season: int, week: int, **fields) -> dict:
    """Set fields only the user knows (rank, overall standing, notes). None clears a field."""
    bad = set(fields) - set(MANUAL_FIELDS)
    if bad:
        raise ValueError(f"unknown record fields: {sorted(bad)}")
    with connect() as con:
        exists = con.execute("SELECT 1 FROM weeks WHERE season=? AND week=?", (season, week)).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO weeks (season, week, updated_at, picks, n_picks, final_points, live_points,"
                " expected_open, projected, movement_edge, n_final, n_live, n_open, complete)"
                " VALUES (?,?,?,'[]',0,0,0,0,0,0,0,0,0,0)", (season, week, _now()))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE weeks SET {sets} WHERE season=? AND week=?", (*fields.values(), season, week))
    return get_week(season, week)


def _week_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["picks"] = json.loads(d["picks"])
    d["complete"] = bool(d["complete"])
    return d


def get_week(season: int, week: int) -> dict | None:
    with connect() as con:
        r = con.execute("SELECT * FROM weeks WHERE season=? AND week=?", (season, week)).fetchone()
    return _week_row(r) if r else None


def list_weeks(season: int | None = None) -> list[dict]:
    with connect() as con:
        if season is None:
            rows = con.execute("SELECT * FROM weeks ORDER BY season, week").fetchall()
        else:
            rows = con.execute("SELECT * FROM weeks WHERE season=? ORDER BY week", (season,)).fetchall()
    return [_week_row(r) for r in rows]


def seasons() -> list[int]:
    with connect() as con:
        return [r[0] for r in con.execute("SELECT DISTINCT season FROM weeks ORDER BY season").fetchall()]
