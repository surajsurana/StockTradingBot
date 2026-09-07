"""
Pool D -- the intraday paper-trading pool. Runs research_lab's VWAP
Extension Exhaustion Fade strategy (EXP-008, REJECT) LIVE, purely to
keep the intraday pipeline itself exercised while further intraday
research continues separately -- see pool_d/state.py's own module
docstring for the full context (deliberately a known-REJECT strategy,
never conflate with Pool A/B/C's genuine-edge requirement).

Intended to run every 5 minutes, 9:15am-3:30pm IST, Monday-Friday, via
cron:

    */5 9-15 * * 1-5  cd .../StockTradingBot && venv/bin/python run_pool_d.py

This script itself is the ONLY place that checks the clock -- every
other module (pool_d/engine.py, research_lab/*) stays trivially testable
with any `now` a test wants to pass in. A tick outside market hours (a
weekend, a holiday, or simply the cron firing a few minutes either side
of the window) is a fast, cheap no-op -- no Kite calls made at all.

    python run_pool_d.py             # normal tick -- what cron calls
    python run_pool_d.py --force     # bypass the market-hours check (manual testing only)
"""

import argparse
import datetime
import sys

from config import settings
from data.fetch_kite_intraday import fetch_all_intraday
from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from reporting.telegram_notifier import send_telegram_message
from run_experiment import LIQUID_UNIVERSE

from pool_d.engine import compute_todays_context, process_tick
from pool_d.state import load_state, save_state

POOL_D_SYMBOLS = LIQUID_UNIVERSE[:20]   # matches EXP-008's own --limit=20
CAPITAL_PER_SYMBOL = settings.RESEARCH_LAB_VIRTUAL_CAPITAL
CONTEXT_HISTORY_DAYS = 90   # comfortably covers atr_14d (needs 15) + the 20-day extension baseline

MARKET_OPEN_HOUR = 9.25    # 9:15
MARKET_CLOSE_HOUR = 15.50  # 15:30


def _is_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    hour = now.hour + now.minute / 60
    return MARKET_OPEN_HOUR <= hour <= MARKET_CLOSE_HOUR


def _format_notification(result: dict) -> str:
    lines = ["*Pool D (Intraday, VWAP Extension Fade -- known REJECT, framework test only)*"]
    for e in result["new_entries"]:
        lines.append(f"ENTRY {e['direction']} {e['symbol']} @ {e['entry_price']:.2f} "
                     f"(qty {e['quantity']}, stop {e['stop_loss']:.2f}, target {e['target']:.2f})")
    for x in result["new_exits"]:
        lines.append(f"EXIT {x['symbol']} @ {x['exit_price']:.2f} ({x['reason']}) -- P&L Rs.{x['pnl']:.2f}")
    return "\n".join(lines)


def run_tick(now: datetime.datetime = None, force: bool = False) -> dict:
    now = now or datetime.datetime.now()
    if not force and not _is_market_hours(now):
        print(f"Outside market hours ({now.strftime('%H:%M')}) -- skipping, no Kite calls made.")
        return {"new_entries": [], "new_exits": []}

    state = load_state()
    today = now.date()
    today_iso = today.isoformat()

    if state["last_processed_date"] != today_iso:
        print(f"First tick of {today_iso} -- resetting daily state and computing fresh context "
              f"({len(POOL_D_SYMBOLS)} symbols, {CONTEXT_HISTORY_DAYS}-day history)...")
        from_date = today - datetime.timedelta(days=CONTEXT_HISTORY_DAYS)
        history = fetch_all_intraday(POOL_D_SYMBOLS, "5minute", from_date, today, settings)
        state["context_by_symbol"] = compute_todays_context(history, today)
        state["cash_by_symbol"] = {s: CAPITAL_PER_SYMBOL for s in POOL_D_SYMBOLS}
        state["positions"] = {}
        state["trades_today_by_symbol"] = {s: 0 for s in POOL_D_SYMBOLS}
        state["realized_pnl_today_by_symbol"] = {s: 0.0 for s in POOL_D_SYMBOLS}
        state["last_processed_date"] = today_iso

    todays_bars = fetch_all_intraday(POOL_D_SYMBOLS, "5minute", today, today, settings)
    if not todays_bars:
        print("No today's-bars data available yet (pre-market, or a data delay) -- skipping this tick.")
        return {"new_entries": [], "new_exits": []}

    result = process_tick(state, todays_bars, state["context_by_symbol"], CAPITAL_PER_SYMBOL, now=now)
    save_state(state)

    print(f"[{now.strftime('%H:%M')}] {len(result['new_entries'])} new entr{'y' if len(result['new_entries']) == 1 else 'ies'}, "
          f"{len(result['new_exits'])} new exit{'s' if len(result['new_exits']) != 1 else ''}.")
    if result["new_entries"] or result["new_exits"]:
        send_telegram_message(_format_notification(result), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bypass the market-hours check (manual testing only)")
    args = parser.parse_args()
    run_tick(force=args.force)


if __name__ == "__main__":
    main()
