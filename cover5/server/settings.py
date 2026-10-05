"""Settings saved from the web app (data/settings.json), applied over environment variables.

The file stores only what the user changed; everything else falls back to the
values ``cover5.config`` read from the environment at import time. ``apply``
writes the effective values onto the ``cover5.config`` module so the service,
providers and alerts pick them up without a restart.

The odds API key is stored in the file (it is local and git ignored) but is
never returned by ``public``: callers get ``odds_api_key_set`` and the last four
characters instead.
"""
from __future__ import annotations

import copy
import json
import os
import threading

from cover5 import config

PROVIDERS = config.PROVIDERS
MARGIN_MAX = 10.0
INTERVAL_MIN, INTERVAL_MAX = 1, 24 * 60

SCHEDULER_DEFAULTS = {
    "enabled": True,
    # The Odds API free tier is 500 requests a month: every 2 hours plus every
    # 15 minutes in the Sunday morning window stays well under it.
    "interval_minutes": 120,
    "sunday_interval_minutes": 15,
    "auto_init_wednesday": True,
}

# config attribute <- settings key
CONFIG_ATTRS = {
    "PROVIDER": "provider",
    "ODDS_API_KEY": "odds_api_key",
    "NTFY_TOPIC": "ntfy_topic",
    "NTFY_SERVER": "ntfy_server",
    "ALERT_WEBHOOK_URL": "webhook_url",
    "SWAP_MARGIN": "swap_margin",
    "FLIP_MARGIN": "flip_margin",
}

_lock = threading.Lock()


class SettingsError(ValueError):
    """An invalid settings value (HTTP 400)."""


def _env_defaults() -> dict:
    return {key: getattr(config, attr) for attr, key in CONFIG_ATTRS.items()}


# Snapshot of the environment-driven config, taken before any settings are applied.
ENV_DEFAULTS = _env_defaults()


def defaults() -> dict:
    d = copy.deepcopy(ENV_DEFAULTS)
    d["scheduler"] = dict(SCHEDULER_DEFAULTS)
    return d


def _stored() -> dict:
    p = config.SETTINGS_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[settings] ignoring unreadable {p}: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if k == "scheduler" and isinstance(v, dict):
            out["scheduler"] = {**out.get("scheduler", {}), **v}
        else:
            out[k] = v
    return out


def load() -> dict:
    """Effective settings, including the raw API key. Invalid stored values fall back to defaults."""
    d = defaults()
    stored = _stored()
    try:
        return validate(_merge(d, stored))
    except SettingsError as e:
        print(f"[settings] ignoring invalid {config.SETTINGS_PATH}: {e}")
        return d


def validate(s: dict) -> dict:
    s = copy.deepcopy(s)
    if s.get("provider") not in PROVIDERS:
        raise SettingsError(f"provider must be one of {', '.join(PROVIDERS)}")
    for k in ("odds_api_key", "ntfy_topic", "ntfy_server", "webhook_url"):
        v = s.get(k)
        if v is None:
            v = ""
        if not isinstance(v, str):
            raise SettingsError(f"{k} must be a string")
        s[k] = v.strip()
    if not s["ntfy_server"]:
        s["ntfy_server"] = "https://ntfy.sh"
    for k in ("ntfy_server", "webhook_url"):
        if s[k] and not s[k].startswith(("http://", "https://")):
            raise SettingsError(f"{k} must start with http:// or https://")
    for k in ("swap_margin", "flip_margin"):
        try:
            v = float(s[k])
        except (TypeError, ValueError) as e:
            raise SettingsError(f"{k} must be a number") from e
        if not 0 <= v <= MARGIN_MAX:
            raise SettingsError(f"{k} must be between 0 and {MARGIN_MAX:g}")
        s[k] = v
    sch = s.get("scheduler")
    if not isinstance(sch, dict):
        raise SettingsError("scheduler must be an object")
    unknown = set(sch) - set(SCHEDULER_DEFAULTS)
    if unknown:
        raise SettingsError(f"unknown scheduler settings: {sorted(unknown)}")
    for k in ("enabled", "auto_init_wednesday"):
        if not isinstance(sch[k], bool):
            raise SettingsError(f"scheduler.{k} must be true or false")
    for k in ("interval_minutes", "sunday_interval_minutes"):
        v = sch[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            raise SettingsError(f"scheduler.{k} must be a whole number of minutes")
        if not INTERVAL_MIN <= v <= INTERVAL_MAX:
            raise SettingsError(f"scheduler.{k} must be between {INTERVAL_MIN} and {INTERVAL_MAX}")
        sch[k] = int(v)
    return s


def public(s: dict | None = None) -> dict:
    """Settings as the API returns them: never the key itself."""
    s = s if s is not None else load()
    key = s.get("odds_api_key") or ""
    return {
        "provider": s["provider"],
        "odds_api_key_set": bool(key),
        "odds_api_key_hint": ("…" + key[-4:]) if key else None,
        "ntfy_topic": s["ntfy_topic"],
        "ntfy_server": s["ntfy_server"],
        "webhook_url": s["webhook_url"],
        "swap_margin": s["swap_margin"],
        "flip_margin": s["flip_margin"],
        "scheduler": dict(s["scheduler"]),
    }


def apply(s: dict | None = None) -> dict:
    """Write effective settings onto the cover5.config module."""
    s = s if s is not None else load()
    for attr, key in CONFIG_ATTRS.items():
        setattr(config, attr, s[key])
    return s


READ_ONLY = ("odds_api_key_set", "odds_api_key_hint")
TOP_KEYS = set(CONFIG_ATTRS.values()) | {"scheduler"}


def update(patch: dict) -> dict:
    """Merge a partial update, validate, persist and apply. Returns the effective settings.

    ``odds_api_key: ""`` clears the key; leaving it out keeps it. The read-only
    fields of ``public`` are accepted and ignored so a client can PUT back what it got.
    """
    patch = {k: v for k, v in patch.items() if k not in READ_ONLY}
    unknown = set(patch) - TOP_KEYS
    if unknown:
        raise SettingsError(f"unknown settings: {sorted(unknown)}")
    if "scheduler" in patch:
        if patch["scheduler"] is None:
            patch.pop("scheduler")
        elif not isinstance(patch["scheduler"], dict):
            raise SettingsError("scheduler must be an object")
    patch = {k: v for k, v in patch.items() if v is not None or k == "odds_api_key"}
    if patch.get("odds_api_key") is None:
        patch.pop("odds_api_key", None)
    with _lock:
        stored = _stored()
        new_stored = _merge(stored, patch)
        effective = validate(_merge(defaults(), new_stored))
        if effective["provider"] == "oddsapi" and not effective["odds_api_key"]:
            raise SettingsError("the oddsapi provider needs an API key; enter one or pick espn")
        _write(new_stored)
        apply(effective)
    return effective


def _write(data: dict) -> None:
    p = config.SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)
