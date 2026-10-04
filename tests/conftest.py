import pytest

from cover5 import config


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Point every data directory at a temp dir and forbid schedule downloads."""
    data = tmp_path / "data"
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(config, "LEAGUE_DIR", data / "league_lines")
    monkeypatch.setattr(config, "STATE_DIR", data / "state")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", data / "snapshots")
    monkeypatch.setattr(config, "CACHE_DIR", data / "cache")
    monkeypatch.setattr(config, "OVERRIDES_DIR", data / "overrides")
    monkeypatch.setattr(config, "DB_PATH", data / "cover5.db")
    monkeypatch.setattr(config, "SETTINGS_PATH", data / "settings.json")
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "")
    config.ensure_dirs()
    from cover5 import service
    service._games_cache.update(mtime=None, df=None)
    return tmp_path
