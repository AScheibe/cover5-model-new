"""Settings: persistence in data/settings.json, key masking, validation, applying to config."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from cover5 import config
from cover5.server import settings as settings_mod
from cover5.server.app import create_app

from tests.test_api import cfg_guard  # noqa: F401  (fixture)


@pytest.fixture
def c(cfg_guard, tmp_path):
    return TestClient(create_app(frontend_dist=tmp_path / "no-dist"))


def test_defaults_come_from_config(c):
    s = c.get("/api/settings").json()
    assert s == {"provider": "espn", "odds_api_key_set": False, "odds_api_key_hint": None,
                 "ntfy_topic": "", "ntfy_server": "https://ntfy.sh", "webhook_url": "",
                 "swap_margin": 0.5, "flip_margin": 0.5,
                 "scheduler": {"enabled": True, "interval_minutes": 120, "sunday_interval_minutes": 15,
                               "auto_init_wednesday": True}}
    assert not config.SETTINGS_PATH.exists()            # GET never writes


def test_put_persists_masks_and_applies(c, cfg_guard):
    r = c.put("/api/settings", json={"odds_api_key": "abcdef123456wxyz", "provider": "oddsapi",
                                     "swap_margin": 1.5, "ntfy_topic": "cover5-me",
                                     "scheduler": {"interval_minutes": 60}})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["odds_api_key_set"] and s["odds_api_key_hint"] == "…wxyz"
    assert "abcdef123456wxyz" not in r.text and "odds_api_key" not in s
    assert s["scheduler"] == {"enabled": True, "interval_minutes": 60, "sunday_interval_minutes": 15,
                              "auto_init_wednesday": True}
    # applied to the config module the service reads
    assert (config.PROVIDER, config.ODDS_API_KEY, config.SWAP_MARGIN, config.NTFY_TOPIC) == \
        ("oddsapi", "abcdef123456wxyz", 1.5, "cover5-me")
    assert c.get("/api/meta").json()["config"]["swap_margin"] == 1.5
    # persisted, only what changed, owner-only permissions
    stored = json.loads(config.SETTINGS_PATH.read_text())
    assert stored == {"odds_api_key": "abcdef123456wxyz", "provider": "oddsapi", "swap_margin": 1.5,
                      "ntfy_topic": "cover5-me", "scheduler": {"interval_minutes": 60}}
    assert oct(os.stat(config.SETTINGS_PATH).st_mode & 0o777) == "0o600"
    # GET never returns the key either
    g = c.get("/api/settings")
    assert "abcdef123456wxyz" not in g.text and g.json()["odds_api_key_hint"] == "…wxyz"


def test_key_omitted_keeps_empty_clears(c):
    c.put("/api/settings", json={"odds_api_key": "key-1234"})
    s = c.put("/api/settings", json={"flip_margin": 2}).json()
    assert s["odds_api_key_set"] and s["flip_margin"] == 2.0
    # a client may PUT back exactly what it got
    s = c.put("/api/settings", json=s).json()
    assert s["odds_api_key_set"] and s["odds_api_key_hint"] == "…1234"
    s = c.put("/api/settings", json={"odds_api_key": ""}).json()
    assert not s["odds_api_key_set"] and s["odds_api_key_hint"] is None and config.ODDS_API_KEY == ""


def test_settings_survive_restart_and_apply_at_startup(c, cfg_guard, tmp_path):
    c.put("/api/settings", json={"swap_margin": 3, "provider": "file", "webhook_url": "https://hooks.example/x"})
    config.SWAP_MARGIN, config.PROVIDER, config.ALERT_WEBHOOK_URL = 0.5, "espn", ""   # a fresh process
    c2 = TestClient(create_app(frontend_dist=tmp_path / "no-dist"))
    assert (config.SWAP_MARGIN, config.PROVIDER, config.ALERT_WEBHOOK_URL) == (3.0, "file", "https://hooks.example/x")
    assert c2.get("/api/settings").json()["provider"] == "file"
    assert c2.get("/api/meta").json()["config"]["webhook_set"]


@pytest.mark.parametrize("body", [
    {"provider": "bovada"},
    {"swap_margin": -0.1},
    {"flip_margin": 10.5},
    {"swap_margin": "big"},
    {"provider": "oddsapi"},                               # no key
    {"scheduler": {"interval_minutes": 0}},
    {"scheduler": {"bogus": True}},
    {"webhook_url": "ftp://x"},
    {"surprise": 1},
])
def test_validation(c, body):
    r = c.put("/api/settings", json=body)
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
    assert not config.SETTINGS_PATH.exists()


def test_corrupt_settings_file_falls_back_to_defaults(cfg_guard):
    config.SETTINGS_PATH.write_text("{not json")
    assert settings_mod.load()["provider"] == "espn"
    config.SETTINGS_PATH.write_text(json.dumps({"swap_margin": 99}))
    assert settings_mod.load()["swap_margin"] == 0.5


def test_scheduler_toggle_starts_thread_when_allowed(cfg_guard, tmp_path):
    from cover5.server.scheduler import Scheduler
    sched = Scheduler(tick_seconds=3600)
    c = TestClient(create_app(frontend_dist=tmp_path / "x", scheduler=sched, scheduler_allowed=True))
    c.put("/api/settings", json={"scheduler": {"enabled": False}})
    assert not sched.running and c.get("/api/scheduler").json()["enabled"] is False
    c.put("/api/settings", json={"scheduler": {"enabled": True}})
    try:
        assert sched.running and c.get("/api/scheduler").json()["running"]
    finally:
        sched.stop()
