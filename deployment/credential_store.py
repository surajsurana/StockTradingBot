"""
Broker API credentials, kept out of config/settings.py and out of the repo.

WHY NOT settings.py. That file is Python, imported at startup. Writing to it from a web form means a
web request can inject code into something the program executes -- the worst class of bug to add on
purpose. This store is plain JSON: the worst a bad value can do is fail to authenticate.

WHAT IT GUARANTEES
  - the file is chmod 0600 (owner only) and lives outside the git tree's tracked files;
  - values are NEVER returned to a caller that only asked for status, so the dashboard can show
    "configured" without the secret ever travelling back to a browser;
  - nothing here logs, prints or formats a secret, and status() returns only presence, length and
    the last four characters of a KEY (never of a secret), which is enough to tell two keys apart
    and useless to anyone who intercepts it.

RESOLUTION ORDER. credential() reads this store first, then falls back to config/settings.py, so
credentials already placed in settings.py by hand keep working untouched and nothing has to be
migrated. The store wins because it is the one a human most recently set on purpose.

THIS IS NOT ENCRYPTION. Anyone who can read the file as the owning user can read the secrets, the
same as for settings.py. It protects against the file being committed, being world-readable, or
being echoed back over the network -- not against a compromised account.
"""

import json
import os
import stat
from typing import Optional

STORE_FILENAME = "credentials.json"

# The credentials the dashboard offers to set. Adding a broker later means adding a line here and
# nothing else: the endpoint, the form and the validation all read this.
KNOWN_CREDENTIALS = (
    {"name": "COINDCX_API_KEY", "group": "CoinDCX", "label": "API key", "secret": False},
    {"name": "COINDCX_API_SECRET", "group": "CoinDCX", "label": "API secret", "secret": True},
    {"name": "KITE_API_KEY", "group": "Zerodha Kite", "label": "API key", "secret": False},
    {"name": "KITE_API_SECRET", "group": "Zerodha Kite", "label": "API secret", "secret": True},
    {"name": "KITE_TOTP_SECRET", "group": "Zerodha Kite", "label": "TOTP secret", "secret": True},
)
_NAMES = {c["name"] for c in KNOWN_CREDENTIALS}
_SECRET_NAMES = {c["name"] for c in KNOWN_CREDENTIALS if c["secret"]}


def store_path(config_dir: str) -> str:
    return os.path.join(config_dir, STORE_FILENAME)


def load(config_dir: str) -> dict:
    """Every stored credential. An unreadable or malformed store is EMPTY, not an exception -- a
    corrupt file must not stop the dashboard booting, and an empty store simply falls back to
    settings.py."""
    try:
        with open(store_path(config_dir), encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))} \
            if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save(config_dir: str, updates: dict) -> list:
    """Merges `updates` into the store and returns the names actually written.

    An empty or whitespace-only value REMOVES that credential rather than storing a blank, so the
    form's "clear this field" does what it looks like it does. Unknown names are ignored outright --
    the form cannot be used to write arbitrary keys into the file."""
    from deployment.atomic_write import write_json

    current = load(config_dir)
    written = []
    for name, value in (updates or {}).items():
        if name not in _NAMES:
            continue                      # not a credential this program knows about
        text = str(value or "").strip()
        if text:
            current[name] = text
        else:
            current.pop(name, None)
        written.append(name)
    if not written:
        return []
    path = store_path(config_dir)
    write_json(path, current, sort_keys=True)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)        # 0600, owner only
    except OSError:
        pass                              # a filesystem without POSIX modes is not a reason to fail
    return written


def credential(name: str, config_dir: str = "", settings_module=None) -> str:
    """The value for `name`: the store first, then config/settings.py. "" when neither has it.

    Everything that talks to a broker should read credentials through here rather than importing
    settings directly, so a credential set on the dashboard takes effect without editing code."""
    if config_dir:
        found = load(config_dir).get(name)
        if found:
            return str(found)
    if settings_module is None:
        try:
            from config import settings as settings_module       # noqa: PLC0415
        except ImportError:
            return ""
    return str(getattr(settings_module, name, "") or "")


def status(config_dir: str = "", settings_module=None) -> list:
    """What the dashboard may display: presence and provenance, never a value.

    `hint` is the last four characters of a NON-secret credential (an API key, which is an
    identifier) so two keys can be told apart at a glance. A secret never gets a hint, because four
    characters of a secret is four characters more than a browser needs."""
    stored = load(config_dir) if config_dir else {}
    out = []
    for spec in KNOWN_CREDENTIALS:
        name = spec["name"]
        in_store = bool(str(stored.get(name, "") or "").strip())
        value = credential(name, config_dir, settings_module)
        out.append({
            "name": name, "group": spec["group"], "label": spec["label"], "secret": spec["secret"],
            "configured": bool(value),
            "source": "dashboard" if in_store else ("settings.py" if value else ""),
            "hint": value[-4:] if (value and name not in _SECRET_NAMES and len(value) > 4) else "",
        })
    return out
