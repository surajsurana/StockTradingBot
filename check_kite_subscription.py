"""
Watches the paid Kite Connect market-data app so its monthly subscription never lapses unnoticed.

Background (2026-09-25): the app expired on 23 Sep, the dashboard silently fell back to Yahoo prices (wrong
"today's P&L") and Pool D stopped trading for two days before anyone noticed. This script speaks up instead:

  * dead key   -- every run checks that Kite still accepts the app's API key (one unauthenticated GET, no
                  login, so it can be run often). If it does not, a Telegram alert repeats every 6 hours.
  * reminders  -- from 7 days before the recorded expiry: the Zerodha balance is read (at most one login a
                  day) and a Telegram message says whether it covers the fee, and how much to add if not.
                  Reminders go out at 7, 3, 2, 1 and 0 days; a low balance is repeated daily.
  * roll-over  -- once the expiry date has passed and the key still works, the recorded expiry moves on one
                  month and Telegram confirms the renewal.

The expiry date and fee live in deployment/state/kite_subscription.json (the first run creates it). Kite has no
API for either, so change them there if they are wrong:  {"expires": "2026-10-26", "fee": 1000}

    python check_kite_subscription.py            # print what it would send (safe default)
    python check_kite_subscription.py --send     # also send it by Telegram

Cron, every 30 minutes:
    */30 * * * *  cd .../StockTradingBot && venv/bin/python check_kite_subscription.py --send
"""

import argparse
import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

STATE_FILE = "kite_subscription.json"
DEFAULT_STATE = {"expires": "2026-10-26", "fee": 1000, "sent": {}}
REMIND_AT_DAYS = (7, 3, 2, 1, 0)
LOW_BALANCE_WINDOW_DAYS = 7
DEAD_REPEAT_HOURS = 6
MARGINS_URL = "https://api.kite.trade/user/margins"


def add_month(d: date) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def key_is_alive(api_key: str, timeout: int = 20) -> Optional[bool]:
    """True if Kite accepts the API key, False if it says the key is invalid, None if it cannot be told
    (network trouble, an unexpected reply) -- an unknown answer never raises an alarm."""
    try:
        r = requests.get(f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3", allow_redirects=False, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code in (301, 302, 303, 307, 308):
        return True
    if r.status_code == 400 and "api_key" in r.text.lower():
        return False
    return None


def fetch_balance(settings) -> Optional[float]:
    """Cash available in the Zerodha equity account, or None if it cannot be read."""
    from data.fetch_kite_intraday import get_market_data_session
    try:
        headers = get_market_data_session(settings)
        r = requests.get(MARGINS_URL, headers=headers, timeout=20)
        r.raise_for_status()
        return float(r.json()["data"]["equity"]["available"]["live_balance"])
    except Exception:
        return None


def money(n: float) -> str:
    return f"Rs {n:,.0f}"


def plan(today: date, now: datetime, state: dict, key_alive: Optional[bool],
         balance: Optional[float]) -> Tuple[List[Tuple[str, str]], dict]:
    """The alerts due now as [(id, message)] and the state after them. Pure: no network, no clock, no files.
    An alert id already in state["sent"] is never sent again."""
    state = {**state, "sent": dict(state.get("sent", {}))}
    expires, fee = date.fromisoformat(state["expires"]), float(state["fee"])
    days_left = (expires - today).days
    out: List[Tuple[str, str]] = []

    def add(alert_id: str, text: str) -> None:
        if alert_id not in state["sent"]:
            out.append((alert_id, text))

    if key_alive is False:
        add(f"dead-{today.isoformat()}-{now.hour // DEAD_REPEAT_HOURS}",
            "*Kite market-data app is NOT working* (expired or deleted). Dashboard prices fall back to Yahoo, so today's P&L "
            "will be wrong, and Pool D cannot trade.\nRenew it: developers.kite.trade, My apps.")
        return out, state

    if key_alive and days_left < 0:
        nxt = add_month(expires)
        while nxt < today:
            nxt = add_month(nxt)
        state["expires"] = nxt.isoformat()
        state["sent"] = {}
        out.append((f"renewed-{nxt.isoformat()}", f"Kite market-data subscription is active. Next expiry recorded: {nxt.strftime('%d %b %Y')}."))
        return out, state

    if 0 <= days_left <= LOW_BALANCE_WINDOW_DAYS:
        low = balance is not None and balance < fee
        head = f"Kite market-data subscription expires {expires.strftime('%d %b %Y')} ({'today' if days_left == 0 else f'in {days_left} day' + ('s' if days_left != 1 else '')})."
        if balance is None:
            body = f"I could not read your Zerodha balance. Make sure at least {money(fee)} is there so it can renew."
        elif low:
            body = f"Zerodha balance is {money(balance)}, below the {money(fee)} needed. *Add at least {money(fee - balance)} now* so it can renew itself."
        else:
            body = f"Zerodha balance {money(balance)} covers the {money(fee)} fee."
        if low or balance is None:
            add(f"low-{expires.isoformat()}-{today.isoformat()}", head + "\n" + body)
        elif days_left in REMIND_AT_DAYS:
            add(f"remind-{expires.isoformat()}-{days_left}", head + "\n" + body)
    return out, state


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return {**DEFAULT_STATE, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT_STATE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--state-dir", default=None)
    args = ap.parse_args()
    from config import settings
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    path = os.path.join(args.state_dir or STATE_DIR, STATE_FILE)
    state = load_state(path)
    now = datetime.now()
    today = now.date()

    alive = key_is_alive(settings.KITE_MARKET_DATA_API_KEY)
    days_left = (date.fromisoformat(state["expires"]) - today).days
    balance = None
    if alive is not False and 0 <= days_left <= LOW_BALANCE_WINDOW_DAYS:
        if state.get("balance_date") == today.isoformat() and state.get("balance") is not None:
            balance = float(state["balance"])
        else:
            balance = fetch_balance(settings)          # at most one login a day; failures are paused by the login guard
            state["balance_date"], state["balance"] = today.isoformat(), balance

    due, state = plan(today, now, state, alive, balance)
    for alert_id, text in due:
        print(text)
        if args.send:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
            state["sent"][alert_id] = now.isoformat(timespec="seconds")
    if not due:
        print(f"Nothing to send (key {'ok' if alive else 'DEAD' if alive is False else 'unknown'}, expires {state['expires']}, {days_left} days).")
    if args.send:                                        # a dry run never changes what has been sent or rolled over
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)


if __name__ == "__main__":
    main()
