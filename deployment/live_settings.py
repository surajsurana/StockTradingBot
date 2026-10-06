"""
The numeric live-trading limits, settable from the dashboard instead of by editing code.

WHY SEPARATE FROM credential_store.py. These are not secrets. They are shown back to the page (that
is the point -- you need to see the cap you set), they need bounds checking, and they are read by
code that must never be given a credential by accident. Different data, different rules, different
file.

WHY NOT config/settings.py. Same reason credentials are not written there: it is Python, imported at
startup, and letting a web request write it means letting a web request inject code. This is plain
JSON, so the worst a bad value can do is be refused by the bounds below.

WHAT IS DELIBERATELY NOT HERE. `LIVE_TRADING` itself. Real orders need four independent human acts --
promote, fund, LIVE_TRADING on, no kill switch -- and the value of that is diluted if every one of
them is a button behind the same access key. The master switch stays a deliberate edit on the
server, and the kill switch stays a file. Those two are the ones worth the friction; a capital
ceiling is not, which is exactly why it moved here.

FAIL-CLOSED IS PRESERVED. An unset or zero cap still means nothing can be allocated. Moving where it
is set does not change what it does.
"""

import os
from typing import Optional

STORE_FILENAME = "live_settings.json"

# name -> (label, minimum, maximum, help). The maximum is a sanity bound, not a policy: it is there so
# a fat-fingered extra zero is refused rather than silently accepted on a money ceiling.
KNOWN_SETTINGS = {
    "LIVE_CAPITAL_POOL_RUPEES": (
        "Deployment cap", 0.0, 10_000_000.0,
        "The most the bot may ever deploy, across all strategies. Nothing can be assigned above it, "
        "and each strategy is additionally capped by what its own broker account actually holds. "
        "Zero means nothing can be allocated at all."),
}


def store_path(config_dir: str) -> str:
    return os.path.join(config_dir, STORE_FILENAME)


def load(config_dir: str) -> dict:
    """Every stored setting. An unreadable or malformed store is EMPTY -- which, because zero blocks
    allocation, fails closed rather than falling back to something permissive."""
    import json
    try:
        with open(store_path(config_dir), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        out = {}
        for name, value in data.items():
            if name in KNOWN_SETTINGS:
                try:
                    out[name] = float(value)
                except (TypeError, ValueError):
                    continue          # a junk value is absent, never a guess
        return out
    except (OSError, ValueError, TypeError):
        return {}


def save(config_dir: str, updates: dict) -> list:
    """Merges `updates` in and returns the names written. Unknown names are ignored, so the form
    cannot be used to write arbitrary keys; out-of-range values raise, so a mistyped ceiling is
    refused rather than stored."""
    from deployment.atomic_write import write_json

    current = load(config_dir)
    written = []
    for name, raw in (updates or {}).items():
        if name not in KNOWN_SETTINGS:
            continue
        label, low, high, _ = KNOWN_SETTINGS[name]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number, got {raw!r}")
        if not (low <= value <= high):
            raise ValueError(f"{label} must be between {low:,.0f} and {high:,.0f}, got {value:,.0f}")
        current[name] = value
        written.append(name)
    if written:
        write_json(store_path(config_dir), current, sort_keys=True)
    return written


def setting(name: str, config_dir: str = "", settings_module=None, default: float = 0.0) -> float:
    """The value for `name`: the dashboard's store first, then config/settings.py, then `default`.

    Same resolution order as credentials, so a value set on the Settings tab takes effect without an
    edit or a restart, and anything already in settings.py keeps working untouched."""
    if config_dir:
        found = load(config_dir).get(name)
        if found is not None:
            return float(found)
    if settings_module is None:
        try:
            from config import settings as settings_module       # noqa: PLC0415
        except ImportError:
            return default
    try:
        return float(getattr(settings_module, name, default) or default)
    except (TypeError, ValueError):
        return default


def status(config_dir: str = "", settings_module=None) -> list:
    """What the dashboard shows: the value and where it came from. Unlike a credential, the value
    itself is returned -- a cap you cannot see is a cap you cannot trust."""
    stored = load(config_dir) if config_dir else {}
    out = []
    for name, (label, low, high, help_text) in KNOWN_SETTINGS.items():
        value = setting(name, config_dir, settings_module)
        out.append({"name": name, "label": label, "value": value, "min": low, "max": high,
                    "help": help_text,
                    "source": "dashboard" if name in stored else ("settings.py" if value else "")})
    return out
