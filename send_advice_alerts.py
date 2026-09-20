"""
The evening message: only what you need to DO on the next trading day, and nothing when there is nothing.
The system watches everything silently; it speaks only when there is an order to place (a limit buy for money
that has arrived, a limit sell, a stop-loss GTT, a sell at market once a limit sell had its chance).
Advice only: nothing is ever ordered.

    python send_advice_alerts.py            # print what would be sent (safe default)
    python send_advice_alerts.py --send     # send by Telegram; stays quiet when there is nothing to do

Scheduled cron line (NOT installed until you ask for it), Sunday to Thursday at 19:00 IST so the message
is ready for the next trading day:
    0 19 * * 0-4  cd .../StockTradingBot && venv/bin/python send_advice_alerts.py --send

The same task is never sent twice for the same day; a task you mark Done on the dashboard drops out.
"""

import argparse
import json
import os
import re
from datetime import date

from send_weekly_advice import load_advice


def fresh_tasks(tasks: list, when: str, sent: dict) -> list:
    """Tasks not yet sent for this trading day. `sent` is {"when": iso, "ids": [...]}."""
    already = set(sent.get("ids", [])) if sent.get("when") == when else set()
    return [t for t in tasks if t["id"] not in already]


_SPECIAL = re.compile(r"([_*`\[])")
_BOLD = re.compile(r"(\u20b9[\d,]+(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)? (?:units|shares)\b)")


def esc(text: str) -> str:
    """Backslash-escape the characters Telegram's Markdown would treat as formatting."""
    return _SPECIAL.sub(lambda m: "\\" + m.group(1), text)


def md(text: str) -> str:
    """Telegram Markdown text with the prices and quantities in bold."""
    return _BOLD.sub(lambda m: "*" + m.group(1) + "*", esc(text))


def message(tasks: list, when_label: str) -> str:
    lines = ["*Long term advice*", f"To do on {when_label}:", ""]
    lines += ["\u2022 *" + esc(t["name"]) + "*: " + md(t["title"]) for t in tasks]
    lines += ["", "Advice only. Nothing has been ordered. Press Done on the dashboard's Advice page once you have placed an order."]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--state-dir", default=None)
    args = ap.parse_args()
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    state_dir = args.state_dir or STATE_DIR
    a, _, snap = load_advice(state_dir, date.today())
    if a is None:
        print(f"Groww not connected (status {snap.get('status')}); nothing to send.")
        return
    path = os.path.join(state_dir, "advice_alert_state.json")
    sent = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    todo = fresh_tasks(a["tasks"], a["when"], sent)
    if not todo:
        print(f"Nothing new to do on {a['when_label']}; staying quiet.")
        return
    msg = message(todo, a["when_label"])
    print(msg)
    if args.send:
        from reporting.telegram_notifier import send_telegram_message
        res = send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        if res.get("ok") or res.get("status") == "not_configured":
            ids = (sent.get("ids", []) if sent.get("when") == a["when"] else []) + [t["id"] for t in todo]
            json.dump({"when": a["when"], "ids": ids}, open(path, "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
