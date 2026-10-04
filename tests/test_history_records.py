"""History records: what refresh stores for weeks without picks, and the slot count."""
from cover5 import history, service

from tests.test_overrides import week  # noqa: F401  (fixture)


def test_week_set_up_but_not_picked_is_not_a_record(week):
    # init-week ran with no market: league lines exist, but no picks yet
    assert service.week_view(2026, 4)["picks"] == []
    assert service.refresh_history() == 0
    assert history.get_week(2026, 4) is None and history.list_weeks(2026) == []

    week("update")                                   # now the model has picks
    assert service.refresh_history() == 1
    rec = history.get_week(2026, 4)
    assert rec["n_picks"] == 5 and len(rec["picks"]) == 5


def test_record_keeps_the_league_slot_count_with_fewer_picks(week):
    week("update")
    v = service.week_view(2026, 4)
    v["picks"] = v["picks"][:3]                      # e.g. the other games kicked off unpicked
    assert history.upsert_week(v) is True
    rec = history.get_week(2026, 4)
    assert rec["n_picks"] == 5 and len(rec["picks"]) == 3 and rec["complete"] is False

    # a record that exists is still updated when its picks are cleared
    v["picks"] = []
    assert history.upsert_week(v) is True
    assert history.get_week(2026, 4)["picks"] == []
