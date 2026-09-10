"""
The single Telegram message of the day -- see reporting/pool_summary.py.
Cron, after every pool's end-of-day run has finished (Pool B's 15:50 is
the last):

    5 16 * * 1-5  cd .../StockTradingBot && venv/bin/python send_daily_pool_summary.py

    python send_daily_pool_summary.py            # build + send
    python send_daily_pool_summary.py --print    # build + print only, no Telegram
"""

import argparse

from data.fetch_historical import fetch_all
from deployment.base import DeploymentStatus
from deployment.deployment_manager import list_strategies
from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from reporting.pool_summary import build_pool_summary, format_pool_summary
from reporting.telegram_notifier import send_telegram_message


def _latest_prices(symbols: list) -> dict:
    if not symbols:
        return {}
    prices = {}
    for symbol, df in fetch_all(symbols, period="5d").items():
        if df is not None and not df.empty:
            prices[symbol] = float(df["Close"].iloc[-1])
    return prices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", help="Print the message instead of sending it")
    args = parser.parse_args()

    active = {r.strategy_key: r.display_name for r in list_strategies()
              if r.deployment_status == DeploymentStatus.PAPER_TRADING}
    summary = build_pool_summary(STATE_DIR, active, _latest_prices)
    text = format_pool_summary(summary)
    if args.print:
        print(text)
        return
    send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    print("Daily pool summary sent.")


if __name__ == "__main__":
    main()
