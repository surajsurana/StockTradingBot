"""
Portfolio B's Telegram command-polling CLI -- checks for new /watchlist,
/addstock, /removestock, /help messages and processes them. A SEPARATE,
frequently-run cron entry (every ~2 minutes) from run_portfolio_b.py's
own twice-daily trading cycle -- watchlist commands should feel
reasonably responsive even though this VPS has no always-on server
process to answer them instantly.

Safe to run even when nothing new has been sent -- a fast no-op.
"""

from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from portfolio_b.telegram_bot import poll_and_process_commands


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured -- nothing to poll.")
        return

    processed = poll_and_process_commands(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    for command_text, reply in processed:
        print(f"Processed: {command_text!r} -> {reply!r}")


if __name__ == "__main__":
    main()
