"""
Portfolio C's daily CLI. Two modes, mirroring run_paper_trading.py's own
EOD / near-open split -- both wired into the VPS crontab as of 2026-09-01:

  python run_portfolio_c.py                  -- EOD run (~15:45 IST cron):
      resolves anything still queued, re-checks holdings, evaluates new
      candidates, sends the daily Telegram summary.
  python run_portfolio_c.py --resolve-at-open -- near-open run (~9:30 IST
      cron): resolves ONLY entries/exits already queued by a PRIOR day's
      EOD call, against today's real, now-available Open, and sends an
      immediate Telegram message for anything that actually filled. Does
      NOT detect new candidates and does NOT touch last_processed_date --
      the later EOD call is unaffected and still required. Safe to run
      even if nothing is pending (a fast no-op).

Sends to the SAME Telegram bot/chat as Portfolio A's own messages
(deployment.settings.TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) -- confirmed
2026-09-01, per explicit direction -- clearly labeled "Portfolio C" in
the message itself (see portfolio_c/report.py) so it's distinguishable
from Portfolio A's own strategy notifications in the same chat.
"""

import argparse
import datetime

from data.fetch_historical import fetch_all
from deployment.daily_details_store import save_detail
from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from portfolio_c import state as pcs
from portfolio_c.daily import resolve_portfolio_c_at_open, run_portfolio_c_daily
from portfolio_c.report import format_portfolio_c_message
from reporting.telegram_notifier import send_telegram_message
from swing_research.universe import get_swing_universe


def _run_eod(force: bool) -> None:
    symbols = get_swing_universe()
    print(f"Fetching 3y of daily data for {len(symbols)} symbol(s)...")
    data = fetch_all(symbols, period="3y")
    print(f"Data available for {len(data)} symbol(s)")

    result = run_portfolio_c_daily(data, as_of_date=datetime.date.today(), force=force)
    print(result)

    if result["status"] == "processed":
        message = format_portfolio_c_message(result)
        # Still sent directly (Portfolio C is already just one message a
        # day, not the per-strategy noise problem Portfolio A had) --
        # ALSO cached so the Details command can surface it again on
        # request (deployment/daily_details_store.py).
        save_detail("Portfolio C", message)
        send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def _run_resolve_at_open() -> None:
    portfolio = pcs.load_portfolio()
    pending_symbols = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
    if not pending_symbols:
        print("No pending Portfolio C entries/exits to resolve.")
        return

    print(f"Resolving {len(pending_symbols)} pending fill(s) at today's real Open...")
    result = resolve_portfolio_c_at_open(fetch_open_data_fn=lambda: fetch_all(pending_symbols, period="5d"))
    print(result)

    if result["new_entries"] or result["new_exits"]:
        updated = pcs.load_portfolio()
        positions_value = sum(pos["quantity"] * pos["entry_price"] for pos in updated["positions"].values())
        message = format_portfolio_c_message({
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
    args = parser.parse_args()

    if args.resolve_at_open:
        _run_resolve_at_open()
    else:
        _run_eod(force=args.force)


if __name__ == "__main__":
    main()
