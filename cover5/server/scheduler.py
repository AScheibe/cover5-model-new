"""Background scheduler: initialise the week on Wednesday and poll the market.

The decision is a pure function, ``due_action(now, week_info, settings,
last_update_at)``, so it can be tested with fixed datetimes. The thread wakes
every ``TICK_SECONDS`` and calls ``Scheduler.tick``, which works out the
current week, asks ``due_action`` what to do and runs it through the service
layer (whose operations take ``service.LOCK``). Errors are recorded in the
status and never stop the thread.

Rules (docs/api.md, "Scheduler"):

1. ``"init"`` when the week has no league file, ``auto_init_wednesday`` is on,
   it is at or after noon ET on the Wednesday before the week's first kickoff,
   and not every game has kicked off yet.
2. ``"update"`` when the week has a league file, some game has not kicked off,
   and at least the interval has passed since the last update:
   ``sunday_interval_minutes`` on Sundays from 9:00 to 13:00 ET, otherwise
   ``interval_minutes``.
3. The update records the run and refreshes the week's history record
   (``service.update`` does both).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from cover5 import service
from cover5 import state as st
from cover5.league import league_path
from cover5.schedule import current_week, week_slate
from cover5.server import settings as settings_mod

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
TICK_SECONDS = 60
SUNDAY_WINDOW = (time(9, 0), time(13, 0))     # ET, [start, end)


@dataclass(frozen=True)
class WeekInfo:
    season: int
    week: int
    has_league_file: bool
    first_kickoff: datetime | None          # aware datetimes
    last_kickoff: datetime | None


# --------------------------------------------------------------------------- pure decision logic
def _sched(settings: dict) -> dict:
    """Accept full Settings or just its ``scheduler`` part."""
    return settings.get("scheduler", settings)


def wednesday_noon_et(first_kickoff: datetime) -> datetime:
    """The last Wednesday noon ET strictly before the first kickoff (an aware ET datetime)."""
    k = first_kickoff.astimezone(ET)
    d = k.date() - timedelta(days=(k.weekday() - 2) % 7)
    noon = datetime.combine(d, time(12, 0), tzinfo=ET)
    if noon >= k:
        noon = datetime.combine(d - timedelta(days=7), time(12, 0), tzinfo=ET)
    return noon


def in_sunday_window(now: datetime) -> bool:
    et = now.astimezone(ET)
    return et.weekday() == 6 and SUNDAY_WINDOW[0] <= et.time() < SUNDAY_WINDOW[1]


def interval_at(now: datetime, settings: dict) -> timedelta:
    sch = _sched(settings)
    minutes = sch["sunday_interval_minutes"] if in_sunday_window(now) else sch["interval_minutes"]
    return timedelta(minutes=minutes)


def due_action(now: datetime, week_info: WeekInfo, settings: dict,
               last_update_at: datetime | None, force: bool = False) -> str | None:
    """``"init"``, ``"update"`` or ``None`` for this instant.

    ``force`` (Run now) ignores ``enabled`` and the interval but still respects
    "nothing to do": no init before Wednesday noon or with auto init off, no
    update once every game has kicked off.
    """
    sch = _sched(settings)
    if not force and not sch.get("enabled", True):
        return None
    if week_info.first_kickoff is None or week_info.last_kickoff is None:
        return None
    if now >= week_info.last_kickoff:
        return None                                   # every game has kicked off
    if not week_info.has_league_file:
        if sch.get("auto_init_wednesday", True) and now >= wednesday_noon_et(week_info.first_kickoff):
            return "init"
        return None
    if force or last_update_at is None or now - last_update_at >= interval_at(now, settings):
        return "update"
    return None


def _sunday_window_starts(after: datetime, n: int = 2) -> list[datetime]:
    et = after.astimezone(ET)
    d = et.date() + timedelta(days=(6 - et.weekday()) % 7)
    out = []
    while len(out) < n:
        start = datetime.combine(d, SUNDAY_WINDOW[0], tzinfo=ET)
        if start + timedelta(hours=4) > after:
            out.append(start)
        d += timedelta(days=7)
    return out


def next_due(now: datetime, week_info: WeekInfo, settings: dict,
             last_update_at: datetime | None) -> datetime | None:
    """When ``due_action`` next returns something (``now`` if it already does), else None."""
    sch = _sched(settings)
    if not sch.get("enabled", True):
        return None
    if due_action(now, week_info, settings, last_update_at):
        return now
    if week_info.first_kickoff is None or week_info.last_kickoff is None:
        return None
    if not week_info.has_league_file:
        t = wednesday_noon_et(week_info.first_kickoff).astimezone(UTC)
        ok = sch.get("auto_init_wednesday", True) and t < week_info.last_kickoff
        return t if ok else None
    if last_update_at is None:
        return None
    cands = [last_update_at + timedelta(minutes=sch["interval_minutes"]),
             last_update_at + timedelta(minutes=sch["sunday_interval_minutes"])]
    cands += _sunday_window_starts(now)
    for t in sorted(c.astimezone(UTC) for c in cands):
        if t > now and due_action(t, week_info, settings, last_update_at):
            return t
    return None


# --------------------------------------------------------------------------- the thread
def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat(timespec="seconds") if dt else None


def week_info(now: datetime | None = None) -> WeekInfo:
    """The current week from the nflverse schedule and whether it has a league file."""
    now = now or datetime.now(UTC)
    g = service.games()
    season, week = current_week(g, now)
    slate = week_slate(g, season, week)
    first = last = None
    if not slate.empty:
        first = pd.Timestamp(min(slate.kickoff_utc)).to_pydatetime()
        last = pd.Timestamp(max(slate.kickoff_utc)).to_pydatetime()
    return WeekInfo(season, week, league_path(season, week).exists(), first, last)


def last_snapshot_at(season: int, week: int) -> datetime | None:
    """When the market was last logged for this week (by the scheduler, the app or the CLI)."""
    snaps = st.load_snapshots(season, week)
    if snaps is None or snaps.empty:
        return None
    return pd.to_datetime(snaps["fetched_at"], utc=True, format="ISO8601").max().to_pydatetime()


class Scheduler:
    def __init__(self, tick_seconds: float = TICK_SECONDS):
        self.tick_seconds = tick_seconds
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._last_tick: datetime | None = None
        self._last_update: datetime | None = None
        self._last_attempt: datetime | None = None
        self._last_result: str | None = None
        self._last_error: str | None = None

    # ---- thread
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cover5-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001  (tick already records errors; belt and braces)
                self._record(error=f"{type(e).__name__}: {e}")
            self._stop.wait(self.tick_seconds)

    # ---- work
    def _last_update_for(self, info: WeekInfo) -> datetime | None:
        """Latest of the last logged snapshot and the last attempt, so failures back off by the interval."""
        times = [t for t in (last_snapshot_at(info.season, info.week), self._last_attempt) if t is not None]
        return max(times) if times else None

    def tick(self, force: bool = False, now: datetime | None = None) -> dict:
        """One scheduler pass. Returns ``{ran, message, outcome?}``."""
        with self._tick_lock:
            now = now or datetime.now(UTC)
            try:
                return self._tick(force, now)
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}" if not isinstance(e, service.UserError) else str(e)
                self._record(now=now, result=f"Failed: {msg}", error=msg)
                return {"ran": False, "message": f"Failed: {msg}"}

    def _tick(self, force: bool, now: datetime) -> dict:
        s = settings_mod.load()
        if not force and not s["scheduler"]["enabled"]:
            self._record(now=now, result="Scheduler is off")
            return {"ran": False, "message": "Scheduler is off"}
        info = week_info(now)
        last = self._last_update_for(info)
        action = due_action(now, info, s, last, force=force)
        label = f"{info.season} week {info.week}"
        if action is None:
            msg = self._idle_message(now, info, s, label)
            self._record(now=now, result=msg)
            return {"ran": False, "message": msg}

        msgs = []
        self._last_attempt = now
        if action == "init":
            # Seed the frozen lines from the market, then recompute picks straight away.
            service.init_week(info.season, info.week, use_market=True)
            msgs.append(f"Initialised {label} from the market. Check the lines against the league app.")
        out = service.update(info.season, info.week)
        self._last_update = now
        changed = bool(out.alert and out.alert.get("changed"))
        sent = bool(out.alert and out.alert.get("sent"))
        msgs.append(f"Updated {label}: " + ("picks changed" if changed else "no change")
                    + (" (alert sent)" if sent else ""))
        msg = " ".join(msgs)
        self._record(now=now, result=msg, error=None)
        return {"ran": True, "message": msg, "outcome": out.to_dict()}

    def _idle_message(self, now: datetime, info: WeekInfo, s: dict, label: str) -> str:
        if info.last_kickoff is None:
            return f"Nothing to do: no games found for {label}"
        if now >= info.last_kickoff:
            return f"Nothing to do: every {label} game has kicked off"
        if not info.has_league_file:
            if not s["scheduler"]["auto_init_wednesday"]:
                return f"Nothing to do: {label} is not initialised and auto init is off"
            when = wednesday_noon_et(info.first_kickoff)
            return f"Waiting to initialise {label} at {when:%a %b %d %I:%M %p} ET"
        return f"Nothing to do: {label} was updated recently"

    def _record(self, now: datetime | None = None, result: str | None = None, error: str | None | bool = False):
        with self._status_lock:
            if now is not None:
                self._last_tick = now
            if result is not None:
                self._last_result = result
            if error is not False:
                self._last_error = error

    # ---- status
    def status(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        s = settings_mod.load()
        nd, last_update = None, self._last_update
        try:
            info = week_info(now)
            nd = next_due(now, info, s, self._last_update_for(info))
            logged = last_snapshot_at(info.season, info.week)    # updates from the app or CLI count too
            if logged is not None and (last_update is None or logged > last_update):
                last_update = logged
        except Exception:  # noqa: BLE001  (status must never fail, e.g. schedule unavailable)
            pass
        with self._status_lock:
            return {
                "enabled": bool(s["scheduler"]["enabled"]),
                "running": self.running,
                "last_tick": _iso(self._last_tick),
                "last_update_at": _iso(last_update),
                "last_result": self._last_result,
                "last_error": self._last_error,
                "next_due": _iso(nd),
            }
