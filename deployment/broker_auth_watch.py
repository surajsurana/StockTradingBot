"""
Speaks up the moment a broker stops accepting our key or login -- once when it breaks, once when it
is fixed, and never in between.

WHY (2026-10-29). CoinDCX expired the API key at 20:35 on 28 Oct (its email claimed "no API trading
in 30 days"; the key had placed real orders on 20, 22 and 24 Oct). Every live crypto run after that
halted correctly -- it will not trade a book it cannot verify -- and said so only in logs/live.log.
Pool G sat stopped until Suraj happened to ask. Suraj, the same day: a permanent fix so a silent key
expiry can never stop live trading again.

Two ways in, one state file (deployment/state/broker_auth.json), so they never double up:
  * check_broker_auth.py, ~30 minutes before each live run, asks the broker one read-only question
    (CoinDCX: the balances; Kite: is the session good, logging in the way the run itself would);
  * run_live.py, after every --live run, reports what the run itself ran into.
Either one that sees a key/login failure while the venue was last known good sends a Telegram
(kind "broker_auth") and records an event for the Notifications page; the first success afterwards
sends the all-clear. An answer that cannot be told -- the network down, the exchange having a bad
minute -- changes nothing: a flaky connection is not a dead key, and crying wolf gets alerts ignored.
"""

import json
import os
import re
from datetime import datetime
from typing import Callable, Optional

STATE_FILE = "broker_auth.json"
MAX_EVENTS = 50
DASHBOARD_HTTPS = "https://168.144.66.161:8443"

VENUE_NAMES = {"coindcx": "CoinDCX", "kite": "Kite (Zerodha)"}
WHAT_STOPS = {"coindcx": "live crypto trading (Pool G)", "kite": "live India trading"}
HOW_TO_FIX = {
    "coindcx": ("Create a new CoinDCX API key (trading only, bound to IP 168.144.66.161) and save it "
                f"in the dashboard's Settings tab at {DASHBOARD_HTTPS}"),
    "kite": "The automatic Kite login failed. Check the Zerodha password / TOTP, or run refresh_kite_token.py.",
}

# What a refused key or login looks like, in any of the places it can surface: CoinDCX's own words
# (expired, invalid), a bare 401, Kite's TokenException, and run_live.py's own refusals when the Kite
# session cannot be refreshed or no credential is configured at all.
_AUTH_FAILURE = re.compile(
    r"api key expired|generate new keys|invalid credentials|HTTP 401\b|TokenException|"
    r"Incorrect .api_key. or .access_token.|Kite session could not be refreshed|"
    r"Refreshing the Kite session failed|credentials are not configured", re.IGNORECASE)


def is_auth_failure(text: str) -> bool:
    return bool(_AUTH_FAILURE.search(str(text or "")))


def _path(state_dir: str) -> str:
    return os.path.join(state_dir, STATE_FILE)


def load(state_dir: str) -> dict:
    try:
        with open(_path(state_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state_dir: str, state: dict) -> None:
    from deployment.atomic_write import write_json
    write_json(_path(state_dir), state)


def _message(venue: str, ok: bool, detail: str) -> str:
    name = VENUE_NAMES.get(venue, venue)
    if ok:
        return f"✅ {name} key working again -- {WHAT_STOPS.get(venue, 'live trading')} resumes at its next run."
    return (f"⚠️ {name} key/login not working -- {WHAT_STOPS.get(venue, 'live trading')} is paused "
            f"until it is fixed.\n{name} said: {str(detail)[:160]}\nFix: {HOW_TO_FIX.get(venue, '')}")


def _default_send(text: str) -> None:
    from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    from reporting.telegram_notifier import send_telegram_message
    send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, kind="broker_auth")


def record(venue: str, ok: Optional[bool], detail: str, state_dir: str, *, source: str,
           now: Optional[datetime] = None, send: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Notes what was just seen at `venue`; returns the message sent, if this was a change.

    ok=None (could not tell) is ignored entirely. A venue never seen before counts as working, so the
    very first failure is announced and a first success is silent."""
    if ok is None:
        return None
    now = now or datetime.now()
    state = load(state_dir)
    venues = state.setdefault("venues", {})
    previous = venues.get(venue, {})
    was_ok = previous.get("ok", True)
    stamp = now.isoformat(timespec="seconds")
    entry = {"ok": ok, "since": (was_ok == ok and previous.get("since")) or stamp,
             "checked_at": stamp, "source": source, "detail": "" if ok else str(detail)[:300]}
    venues[venue] = entry
    sent = None
    if was_ok != ok:
        sent = _message(venue, ok, detail)
        state.setdefault("events", []).append({"at": now.isoformat(timespec="seconds"), "venue": venue,
                                               "ok": ok, "detail": entry["detail"], "source": source})
        state["events"] = state["events"][-MAX_EVENTS:]
        try:
            (send or _default_send)(sent)
        except Exception as e:                  # noqa: BLE001 -- the record must survive a failed send
            print(f"broker_auth: could not send the alert: {type(e).__name__}: {e}")
    _save(state_dir, state)
    return sent


def observe_run(results: list, state_dir: str, *, now: Optional[datetime] = None,
                send: Optional[Callable[[str], None]] = None) -> list:
    """run_live.py's results -> one record() per venue. A key/login failure anywhere in a venue's
    results means that venue failed; a strategy that got as far as "processed" means the exchange
    took our key. Anything else (a book mismatch, a crash in an engine) says nothing about the key."""
    by_venue = {}
    for r in results or []:
        venue = r.get("venue")
        if not venue:
            continue
        text = " ".join([str(r.get("reason") or "")]
                        + [str(p) for p in ((r.get("reconciliation") or {}).get("problems") or [])])
        if is_auth_failure(text):
            by_venue[venue] = (False, text.strip())
        elif r.get("status") == "processed" and venue not in by_venue:
            by_venue[venue] = (True, "")
    return [record(v, ok, detail, state_dir, source="live run", now=now, send=send)
            for v, (ok, detail) in by_venue.items()]


def probe(venue: str, settings, config_dir: str) -> tuple:
    """(ok, detail) from one read-only question to the broker. ok is None when it cannot be told."""
    try:
        if venue == "coindcx":
            from deployment.credential_store import credential
            from execution.coindcx_client import CoinDCXClient
            key = credential("COINDCX_API_KEY", config_dir, settings)
            secret = credential("COINDCX_API_SECRET", config_dir, settings)
            if not (key and secret):
                return False, "coindcx credentials are not configured"
            CoinDCXClient(key, secret).balances()
            return True, ""
        if venue == "kite":
            # The same refresh the live run does before trading, so this costs at most the one login
            # the run would have made anyway -- and the run then finds the session already fresh.
            from auth.kite_auto_login import ensure_fresh_kite_session
            if ensure_fresh_kite_session(settings):
                return True, ""
            return False, "The Kite session could not be refreshed"
    except Exception as e:                      # noqa: BLE001
        text = f"{type(e).__name__}: {e}"
        return (False if is_auth_failure(text) else None), text[:300]
    return None, f"no probe for {venue}"


def timeline_rows(state_dir: str) -> list:
    """Events for reporting/lifecycle.timeline -- what Notifications shows next to research results."""
    rows = []
    for e in load(state_dir).get("events") or []:
        try:
            at = datetime.fromisoformat(e["at"])
        except (KeyError, ValueError):
            continue
        name = VENUE_NAMES.get(e.get("venue"), e.get("venue"))
        rows.append({"at": at, "kind": "auth_ok" if e.get("ok") else "auth_fail",
                     "what": f"{name} key working" if e.get("ok") else f"{name} key failed",
                     "name": name, "detail": e.get("detail") or ""})
    return rows
