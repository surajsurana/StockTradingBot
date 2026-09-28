"""
Watches deployment/state/research_queue.json (research_queue.py) so a stuck research pick never goes
unnoticed. The unattended Strategy Implementer routine (a scheduled cloud agent, weekly Sundays 7pm IST)
is the only thing that ever locks a candidate in or resolves it -- if its own run fails for any reason
(2026-09-28: a real run on 2026-09-27 couldn't reach the dashboard at all, a transient network blip;
its only alert on that path is a mobile push notification, which is not reliable), the queue is left
exactly where it was with nobody told. This script is the independent, VPS-local backstop: it reads the
SAME state file the dashboard and the routine already read/write, no network call to anywhere but
Telegram, so it keeps working even when the routine's own connectivity to this VPS is the problem.

  * stuck queued       -- current.in_progress is False and it has sat there longer than a normal weekly
                          cycle (STUCK_QUEUED_DAYS) without the routine ever claiming it. Repeats daily
                          until it resolves, same as check_kite_subscription.py's low-balance alert.
  * stuck in progress   -- current.in_progress is True for far longer than a real run ever takes
                          (STUCK_IN_PROGRESS_HOURS) -- the routine almost certainly crashed mid-run
                          without calling /api/research/resolve, leaving the queue locked forever.
  * nothing queued      -- current is null for longer than a normal 6-hourly advance cycle should allow
                          (NOTHING_QUEUED_HOURS) -- advance_research_queue.py isn't running, or every
                          candidate has been exhausted.

    python check_research_queue.py            # print what it would send (safe default)
    python check_research_queue.py --send     # also send it by Telegram

Cron, once a day:
    15 9 * * *  cd .../StockTradingBot && venv/bin/python check_research_queue.py --send
"""

import argparse
import json
import os
from datetime import datetime
from typing import List, Optional, Tuple

STATE_FILE = "research_queue_watchdog.json"
STUCK_QUEUED_DAYS = 8
STUCK_IN_PROGRESS_HOURS = 24
NOTHING_QUEUED_HOURS = 18


def _hours_since(iso_date: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(iso_date)).total_seconds() / 3600.0


def plan(now: datetime, queue: dict, sent: dict) -> Tuple[List[Tuple[str, str]], dict]:
    """The alerts due now as [(id, message)] and `sent` after them. Pure: no network, no clock, no
    files -- `now` and `queue` (research_queue.load()'s own shape) are passed in. An alert id already
    in `sent` is never sent again; each id folds in the day so a still-stuck issue repeats daily."""
    sent = dict(sent)
    out: List[Tuple[str, str]] = []
    today = now.date().isoformat()

    def add(alert_id: str, text: str) -> None:
        if alert_id not in sent:
            out.append((alert_id, text))

    current = queue.get("current")
    if not current:
        started_dates = [h["started"] for h in queue.get("history", []) if h.get("started")]
        reference = max(started_dates) if started_dates else None
        if reference and _hours_since(reference, now) >= NOTHING_QUEUED_HOURS:
            add(f"empty-{today}",
                "*Research queue is empty* and has been for a while -- advance_research_queue.py "
                "(the 6-hourly job that should refill it) may not be running. Check its cron and logs.")
        return out, sent

    key = current["key"]
    # research_queue.json's `current` only ever carries the key -- the friendly name lives on its
    # matching (still-open) history row, appended by _set_current() the same day it was queued.
    name = next((h["name"] for h in queue.get("history", []) if h.get("key") == key and h.get("resolved") is None), key)
    if current.get("in_progress"):
        if _hours_since(current["started"], now) >= STUCK_IN_PROGRESS_HOURS:
            add(f"stuck-progress-{key}-{today}",
                f"*Research routine looks stuck*: '{name}' has been marked in-progress since {current['started']} "
                f"-- a real run normally finishes in minutes. It likely crashed without reporting back. "
                f"Check the routine's run log at claude.ai/code, and consider resolving it by hand on the "
                f"dashboard so the queue can move on.")
    else:
        if _hours_since(current["started"], now) >= STUCK_QUEUED_DAYS * 24:
            add(f"stuck-queued-{key}-{today}",
                f"*Research routine hasn't run*: '{name}' has been queued since {current['started']} with nobody "
                f"having started it -- past a normal weekly cycle. Check the routine's last run on claude.ai/code; "
                f"it may have failed to reach the dashboard (see StockTradingBot's own notes on this).")
    return out, sent


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"sent": {}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--state-dir", default=None)
    args = ap.parse_args()
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import research_queue
    state_dir = args.state_dir or STATE_DIR
    watchdog_path = os.path.join(state_dir, STATE_FILE)
    watchdog_state = load_state(watchdog_path)
    now = datetime.now()

    queue = research_queue.load(state_dir)
    due, sent = plan(now, queue, watchdog_state.get("sent", {}))
    for alert_id, text in due:
        print(text)
        if args.send:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
            sent[alert_id] = now.isoformat(timespec="seconds")
    if not due:
        current = queue.get("current")
        print(f"Nothing to send (current={current['key'] if current else None}, "
              f"in_progress={current.get('in_progress') if current else None}).")
    if args.send:
        os.makedirs(state_dir, exist_ok=True)
        with open(watchdog_path, "w", encoding="utf-8") as f:
            json.dump({"sent": sent}, f, indent=2)


if __name__ == "__main__":
    main()
