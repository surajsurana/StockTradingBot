"""
Portfolio B's daily CLI. Same two-mode shape as run_portfolio_c.py:

  python run_portfolio_b.py                  -- EOD run: resolves queued
      fills, re-checks holdings, evaluates the fixed watchlist, sends
      the daily Telegram summary.
  python run_portfolio_b.py --resolve-at-open -- near-open run: resolves
      ONLY already-queued fills against today's real Open, immediate
      Telegram message if anything filled. No new candidates, does not
      touch last_processed_date.

Only fetches portfolio_b/engine.py's own small, LIVE watchlist (edited
via Telegram, see portfolio_b/telegram_bot.py -- run_portfolio_b_bot.py
is the separate, frequently-polled script that handles /watchlist,
/addstock, /removestock), not the full swing universe run_portfolio_c.py
pulls -- Portfolio B has no anchor strategy needing the broader universe.

Sends to the SAME Telegram bot/chat as Portfolio A and Portfolio C
(deployment.settings.TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID), clearly
labeled "Portfolio B" in the message itself.
"""

import argparse
import datetime

from data.fetch_historical import fetch_all
from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from portfolio_b import state as pbs
from portfolio_b.daily import resolve_portfolio_b_at_open, run_portfolio_b_daily
from portfolio_b.engine import get_watchlist
from portfolio_b.report import format_portfolio_b_message
from reporting.telegram_notifier import send_telegram_message


def _run_eod(force: bool) -> None:
    watchlist = get_watchlist()
    print(f"Fetching 3y of daily data for {len(watchlist)} watchlist symbol(s)...")
    data = fetch_all(watchlist, period="3y")
    print(f"Data available for {len(data)} symbol(s)")

    result = run_portfolio_b_daily(data, as_of_date=datetime.date.today(), force=force)
    print(result)

    if result["status"] == "processed":
        message = format_portfolio_b_message(result)
        send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def _run_resolve_at_open() -> None:
    portfolio = pbs.load_portfolio()
    pending_symbols = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
    if not pending_symbols:
        print("No pending Portfolio B entries/exits to resolve.")
        return

    print(f"Resolving {len(pending_symbols)} pending fill(s) at today's real Open...")
    result = resolve_portfolio_b_at_open(fetch_open_data_fn=lambda: fetch_all(pending_symbols, period="5d"))
    print(result)

    if result["new_entries"] or result["new_exits"]:
        updated = pbs.load_portfolio()
        positions_value = sum(pos["quantity"] * pos["entry_price"] for pos in updated["positions"].values())
        message = format_portfolio_b_message({
            **result, "open_positions": len(updated["positions"]), "cash": updated["cash"],
            "mark_to_market_equity": updated["cash"] + positions_value,
        })
        send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                         help="reprocess today even if already marked processed (EOD mode only)")
    parser.add_argument("--resolve-at-open", action="store_true",
                         help="near-open pass: only resolve already-queued fills, no new candidates")
    parser.add_argument("--list-watchlist", action="store_true",
                         help="print the current live watchlist and exit -- no trading logic runs")
    args = parser.parse_args()

    if args.list_watchlist:
        print(get_watchlist())
    elif args.resolve_at_open:
        _run_resolve_at_open()
    else:
        _run_eod(force=args.force)


if __name__ == "__main__":
    main()
