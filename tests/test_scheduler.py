"""Scheduler: the pure decision table and one real tick against a synthetic week."""
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from cover5 import service
from cover5.server import scheduler as sch
from cover5.server.scheduler import WeekInfo, due_action, next_due, wednesday_noon_et

from tests.test_api import cfg_guard  # noqa: F401  (fixture)
from tests.test_overrides import week  # noqa: F401  (fixture)

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
SET = {"scheduler": {"enabled": True, "interval_minutes": 120, "sunday_interval_minutes": 15,
                     "auto_init_wednesday": True}}


def et(*a):
    return datetime(*a, tzinfo=ET)


def settings(**kw):
    return {"scheduler": {**SET["scheduler"], **kw}}


# Week of Thu Oct 29 2026 (EDT) .. Mon Nov 2 (EST: DST ends Sun Nov 1 at 2am).
OCT = WeekInfo(2026, 8, True, et(2026, 10, 29, 20, 15), et(2026, 11, 2, 20, 15))
# Week of Thu Nov 5 2026 (EST) .. Mon Nov 9.
NOV = WeekInfo(2026, 9, False, et(2026, 11, 5, 20, 15), et(2026, 11, 9, 20, 15))


def uninit(info):
    return WeekInfo(info.season, info.week, False, info.first_kickoff, info.last_kickoff)


# --------------------------------------------------------------------------- Wednesday init
def test_wednesday_noon_is_the_wednesday_before_the_first_kickoff():
    assert wednesday_noon_et(OCT.first_kickoff) == et(2026, 10, 28, 12)
    assert wednesday_noon_et(NOV.first_kickoff) == et(2026, 11, 4, 12)
    # Sunday-first week (no Thursday game): still the Wednesday before
    assert wednesday_noon_et(et(2026, 11, 8, 13)) == et(2026, 11, 4, 12)
    # a Wednesday evening kickoff (Christmas week): noon that same day is still before it
    assert wednesday_noon_et(et(2026, 12, 23, 20)) == et(2026, 12, 23, 12)
    # ...but a Wednesday kickoff at or before noon goes back a week
    assert wednesday_noon_et(et(2026, 12, 23, 12)) == et(2026, 12, 16, 12)


@pytest.mark.parametrize("now, expected", [
    (et(2026, 10, 27, 23, 0), None),          # Tuesday night
    (et(2026, 10, 28, 11, 59), None),         # Wednesday just before noon
    (et(2026, 10, 28, 12, 0), "init"),        # noon ET
    (et(2026, 10, 30, 9, 0), "init"),         # late start: still initialise
    (et(2026, 11, 3, 0, 0), None),            # every game has kicked off
])
def test_init_window(now, expected):
    assert due_action(now.astimezone(UTC), uninit(OCT), SET, None) == expected


def test_wednesday_noon_is_dst_safe():
    # EDT (UTC-4): noon is 16:00 UTC
    assert due_action(datetime(2026, 10, 28, 15, 59, tzinfo=UTC), uninit(OCT), SET, None) is None
    assert due_action(datetime(2026, 10, 28, 16, 0, tzinfo=UTC), uninit(OCT), SET, None) == "init"
    # EST (UTC-5) after DST ends: noon is 17:00 UTC, so 16:30 UTC is still 11:30 am
    assert due_action(datetime(2026, 11, 4, 16, 30, tzinfo=UTC), NOV, SET, None) is None
    assert due_action(datetime(2026, 11, 4, 17, 0, tzinfo=UTC), NOV, SET, None) == "init"


def test_auto_init_off_and_disabled():
    noon = et(2026, 10, 28, 13)
    assert due_action(noon, uninit(OCT), settings(auto_init_wednesday=False), None) is None
    assert due_action(noon, uninit(OCT), settings(enabled=False), None) is None
    # force (Run now) ignores 'enabled' but not 'nothing to do'
    assert due_action(noon, uninit(OCT), settings(enabled=False), None, force=True) == "init"
    assert due_action(et(2026, 10, 28, 11), uninit(OCT), SET, None, force=True) is None


# --------------------------------------------------------------------------- updates
@pytest.mark.parametrize("now, last_ago_min, expected", [
    (et(2026, 10, 30, 10, 0), None, "update"),        # never updated
    (et(2026, 10, 30, 10, 0), 119, None),             # Friday: 2 hour interval
    (et(2026, 10, 30, 10, 0), 120, "update"),
    (et(2026, 11, 1, 8, 59), 30, None),               # Sunday before the window
    (et(2026, 11, 1, 9, 0), 15, "update"),            # Sunday 9:00-13:00: 15 minutes
    (et(2026, 11, 1, 9, 0), 14, None),
    (et(2026, 11, 1, 12, 59), 15, "update"),
    (et(2026, 11, 1, 13, 0), 15, None),               # window closed at 13:00
    (et(2026, 11, 1, 13, 0), 120, "update"),
    (et(2026, 11, 2, 20, 15), None, None),            # last game kicked off
    (et(2026, 11, 3, 9, 0), 600, None),
])
def test_update_intervals(now, last_ago_min, expected):
    last = None if last_ago_min is None else now - timedelta(minutes=last_ago_min)
    assert due_action(now.astimezone(UTC), OCT, SET, last) == expected


def test_sunday_window_is_dst_safe():
    last_ago = timedelta(minutes=20)
    # Sun Oct 25 (EDT): 13:30 UTC is 9:30 am ET, inside the window
    oct25 = WeekInfo(2026, 7, True, et(2026, 10, 22, 20, 15), et(2026, 10, 26, 20, 15))
    t = datetime(2026, 10, 25, 13, 30, tzinfo=UTC)
    assert due_action(t, oct25, SET, t - last_ago) == "update"
    # Sun Nov 1 (EST, DST just ended): 13:30 UTC is 8:30 am ET, outside it
    t = datetime(2026, 11, 1, 13, 30, tzinfo=UTC)
    assert due_action(t, OCT, SET, t - last_ago) is None
    t = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)          # 9:00 am EST
    assert due_action(t, OCT, SET, t - last_ago) == "update"


def test_disabled_and_force():
    now = et(2026, 10, 30, 10)
    recent = now - timedelta(minutes=5)
    assert due_action(now, OCT, settings(enabled=False), None) is None
    assert due_action(now, OCT, SET, recent) is None
    assert due_action(now, OCT, SET, recent, force=True) == "update"
    assert due_action(et(2026, 11, 3, 9), OCT, SET, recent, force=True) is None   # all kicked off


def test_accepts_scheduler_part_only_and_missing_slate():
    now = et(2026, 10, 30, 10)
    assert due_action(now, OCT, SET["scheduler"], None) == "update"
    empty = WeekInfo(2026, 30, False, None, None)
    assert due_action(now, empty, SET, None, force=True) is None


def test_next_due():
    fri = et(2026, 10, 30, 10)
    assert next_due(fri, OCT, SET, None) == fri
    assert next_due(fri, OCT, SET, fri - timedelta(minutes=30)) == fri + timedelta(minutes=90)
    # Sunday 8:00 last update 7:30: the window opens at 9:00 and the 15 minute interval applies
    sun = et(2026, 11, 1, 8, 0)
    assert next_due(sun, OCT, SET, sun - timedelta(minutes=30)) == et(2026, 11, 1, 9, 0)
    assert next_due(et(2026, 10, 27, 9), uninit(OCT), SET, None) == et(2026, 10, 28, 12)
    assert next_due(fri, OCT, settings(enabled=False), None) is None
    assert next_due(et(2026, 11, 3), OCT, SET, None) is None


# --------------------------------------------------------------------------- the real tick
def test_tick_updates_then_waits_then_force_runs(week, cfg_guard):
    s = sch.Scheduler()
    res = s.tick()
    assert res["ran"] and "Updated 2026 week 4" in res["message"]
    assert len(res["outcome"]["week"]["picks"]) == 5
    res = s.tick()                          # just updated: nothing to do
    assert not res["ran"] and "updated recently" in res["message"]
    res = s.tick(force=True)                # Run now ignores the interval
    assert res["ran"]
    st_ = s.status()
    assert st_["enabled"] and not st_["running"] and st_["last_result"].startswith("Updated")
    assert st_["last_update_at"] and st_["last_error"] is None
    assert st_["next_due"] is not None


def test_tick_initialises_when_due(week, cfg_guard, monkeypatch):
    (service.league_path(2026, 4)).unlink()
    # pretend it is the Wednesday noon window for this synthetic week
    monkeypatch.setattr(sch, "wednesday_noon_et", lambda first: datetime(2000, 1, 1, tzinfo=UTC))
    res = sch.Scheduler().tick()
    assert res["ran"] and res["message"].startswith("Initialised 2026 week 4")
    assert service.league_path(2026, 4).exists()


def test_tick_records_errors_and_backs_off(week, cfg_guard, monkeypatch):
    def boom(provider=None):
        raise RuntimeError("provider down")
    monkeypatch.setattr(service, "fetch_market", boom)
    s = sch.Scheduler()
    res = s.tick()
    assert not res["ran"] and "provider down" in res["message"]
    assert "provider down" in s.status()["last_error"]
    # a failed attempt counts toward the interval, so it does not retry every minute
    assert "updated recently" in s.tick()["message"]


def test_tick_when_disabled(week, cfg_guard):
    from cover5.server import settings as settings_mod
    settings_mod.update({"scheduler": {"enabled": False}})
    s = sch.Scheduler()
    assert s.tick() == {"ran": False, "message": "Scheduler is off"}
    assert s.tick(force=True)["ran"]


def test_thread_starts_and_stops(week, cfg_guard):
    s = sch.Scheduler(tick_seconds=0.05)
    s.start()
    assert s.running
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    while s.status()["last_tick"] is None and datetime.now(UTC) < deadline:
        time.sleep(0.02)
    s.stop()
    assert not s.running and s.status()["last_tick"] is not None
