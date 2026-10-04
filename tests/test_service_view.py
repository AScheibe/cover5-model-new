"""The service layer's JSON week view and the SQLite history it writes."""
import json
from datetime import datetime, timezone

from cover5 import history, service

from tests.test_overrides import week  # noqa: F401  (fixture)


def test_view_is_json_and_history_is_recorded(week):
    week("update")
    week("picks", "CLE", "WAS", "BAL", "DAL", "JAX")
    week("set-points", "CLE", "8", "--live")
    v = service.week_view(2026, 4)
    json.dumps(v)                                     # must be serialisable, no NaN/numpy
    assert v["has_league_file"] and len(v["games"]) == 8 and len(v["picks"]) == 5
    cle = next(p for p in v["picks"] if p["team"] == "CLE")
    assert cle["status"] == "live" and cle["points"] == 8.0 and cle["locked"] and cle["manual"]
    assert cle["label"] == "CLE -3 vs PIT"
    assert v["summary"]["live"] == 8.0
    g = next(g for g in v["games"] if g["home"] == "CLE")
    assert g["picked_team"] == "CLE" and g["lock"] == {"team": "CLE", "side": "HOME"}
    assert v["overrides"]["points"][0]["value"]["value"] == 8.0
    # the last change is what the user was told to do after setting picks
    assert v["last_change"] and set(v["last_change"]["dropped"]) == {"DAL", "JAX"}

    runs = history.list_runs(2026, 4)
    assert [r["reason"] for r in runs][:3] == ["after your points override", "after you set your picks",
                                               "market update"]
    rec = history.get_week(2026, 4)
    assert rec["n_picks"] == 5 and rec["live_points"] == 8.0 and not rec["complete"]

    # manual fields survive later recomputes
    history.set_manual(2026, 4, week_rank=1, entrants=10, overall_points=42.0, overall_rank=2, notes="hi")
    week("set-points", "CLE", "13.5")
    rec = history.get_week(2026, 4)
    assert rec["final_points"] == 13.5 and rec["week_rank"] == 1 and rec["notes"] == "hi"




def test_refresh_history_and_missing_week(tmp_root):
    v = service.week_view(2026, 9)
    assert v["has_league_file"] is False
    assert service.refresh_history() == 0
