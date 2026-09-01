"""
Portfolio C's daily CLI -- the entry point a cron job would call, once
separately approved and wired in (see PROMOTION_CHECKLIST.md's own
philosophy: nothing runs live until explicitly checked and approved).

NOT currently in any crontab -- this script exists and is tested, but
running it is a manual/explicit action until told otherwise. Mirrors
run_paper_trading.py's shape (fetch -> process -> report -> Telegram) but
for Portfolio C's own single, agent-allocated portfolio rather than one
call per SW-numbered strategy.

Sends to the SAME Telegram bot/chat as Portfolio A's own messages
(deployment.settings.TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) -- confirmed
2026-09-01, per explicit direction -- clearly labeled "Portfolio C" in
the message itself (see portfolio_c/report.py) so it's distinguishable
from Portfolio A's own strategy notifications in the same chat.
"""

import argparse
import datetime

from data.fetch_historical import fetch_all
from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from portfolio_c.daily import run_portfolio_c_daily
from portfolio_c.report import format_portfolio_c_message
from reporting.telegram_notifier import send_telegram_message
from swing_research.universe import get_swing_universe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                         help="reprocess today even if already marked processed")
    args = parser.parse_args()

    symbols = get_swing_universe()
    print(f"Fetching 3y of daily data for {len(symbols)} symbol(s)...")
    data = fetch_all(symbols, period="3y")
    print(f"Data available for {len(data)} symbol(s)")

    result = run_portfolio_c_daily(data, as_of_date=datetime.date.today(), force=args.force)
    print(result)

    if result["status"] == "processed":
        message = format_portfolio_c_message(result)
        send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
