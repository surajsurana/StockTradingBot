"""
Pool D -- the intraday paper-trading pool. Runs research_lab's VWAP
Extension Exhaustion Fade strategy (EXP-008, REJECT) LIVE, purely to
keep the intraday pipeline itself exercised while further intraday
research continues separately -- see pool_d/state.py's own module
docstring for the full context (deliberately a known-REJECT strategy,
never conflate with Pool A/B/C's genuine-edge requirement; ONE shared
Rs.1,00,000 book across the whole liquid universe since 2026-09-10).

Intended to run every 5 minutes, 9:15am-3:30pm IST, Monday-Friday, via
cron:

    */5 9-15 * * 1-5  cd .../StockTradingBot && venv/bin/python run_pool_d.py

This script itself is the ONLY place that checks the clock -- every
other module (pool_d/engine.py, research_lab/*) stays trivially testable
with any `now` a test wants to pass in. A tick outside market hours (a
weekend, a holiday, or simply the cron firing a few minutes either side
of the window) is a fast, cheap no-op -- no Kite calls made at all.

Universe: the full LIQUID_UNIVERSE (~150 names, 2026-09-10; was its
first 20 before). Each tick fetches only today's bars for every symbol
(~150 Kite calls at 3 requests/second, well inside a 5-minute slot);
the first tick of a day additionally fetches 90 days of history for the
strategy's context. A lock file guards against two ticks overlapping.

    python run_pool_d.py             # normal tick -- what cron calls
    python run_pool_d.py --force     # bypass the market-hours check (manual testing only)
"""

import argparse
import datetime
import os
import sys

from config import settings
from data.fetch_kite_intraday import fetch_all_intraday
from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_SINGLE_DAILY_SUMMARY
from reporting.telegram_notifier import send_telegram_message
from run_experiment import LIQUID_UNIVERSE

from pool_d.engine import compute_todays_context, process_tick
from pool_d.state import POOL_D_LOCK_PATH, POOL_D_STATE_DIR, load_state, save_state

POOL_D_SYMBOLS = list(LIQUID_UNIVERSE)
STARTING_CAPITAL = settings.RESEARCH_LAB_VIRTUAL_CAPITAL   # Rs.1,00,000 -- ONE shared book
CONTEXT_HISTORY_DAYS = 90   # comfortably covers atr_14d (needs 15) + the 20-day extension baseline
LOCK_STALE_MINUTES = 15

MARKET_OPEN_HOUR = 9.25    # 9:15
MARKET_CLOSE_HOUR = 15.50  # 15:30


def _is_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    hour = now.hour + now.minute / 60
    return MARKET_OPEN_HOUR <= hour <= MARKET_CLOSE_HOUR


def _acquire_lock(now: datetime.datetime) -> bool:
    os.makedirs(POOL_D_STATE_DIR, exist_ok=True)
    if os.path.exists(POOL_D_LOCK_PATH):
        age_minutes = (now.timestamp() - os.path.getmtime(POOL_D_LOCK_PATH)) / 60
        if age_minutes < LOCK_STALE_MINUTES:
            return False
        print(f"Stale lock ({age_minutes:.0f} min old) -- taking over.")
    with open(POOL_D_LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} {now.isoformat()}\n")
    return True


def _release_lock() -> None:
    try:
        os.remove(POOL_D_LOCK_PATH)
    except FileNotFoundError:
        pass


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
    if not _acquire_lock(now):
        print("Previous tick still running -- skipping this one.")
        return {"new_entries": [], "new_exits": []}
    try:
        return _run_tick_locked(now)
    finally:
        _release_lock()


def _run_tick_locked(now: datetime.datetime) -> dict:
    state = load_state(STARTING_CAPITAL)
    today = now.date()
    today_iso = today.isoformat()

    if state["last_processed_date"] != today_iso:
        print(f"First tick of {today_iso} -- resetting daily counters and computing fresh context "
              f"({len(POOL_D_SYMBOLS)} symbols, {CONTEXT_HISTORY_DAYS}-day history)...")
        from_date = today - datetime.timedelta(days=CONTEXT_HISTORY_DAYS)
        history = fetch_all_intraday(POOL_D_SYMBOLS, "5minute", from_date, today, settings)
        state["context_by_symbol"] = compute_todays_context(history, today)
        state["positions"] = {}   # nothing is ever held overnight
        state["trades_today_by_symbol"] = {}
        state["realized_pnl_today"] = 0.0
        state["last_processed_date"] = today_iso
        save_state(state)

    todays_bars = fetch_all_intraday(POOL_D_SYMBOLS, "5minute", today, today, settings)
    if not todays_bars:
        print("No today's-bars data available yet (pre-market, or a data delay) -- skipping this tick.")
        return {"new_entries": [], "new_exits": []}

    result = process_tick(state, todays_bars, state["context_by_symbol"], now=now)
    save_state(state)

    print(f"[{now.strftime('%H:%M')}] {len(result['new_entries'])} new entr{'y' if len(result['new_entries']) == 1 else 'ies'}, "
          f"{len(result['new_exits'])} new exit{'s' if len(result['new_exits']) != 1 else ''}; "
          f"{len(state['positions'])} open, cash Rs.{state['cash']:,.0f}, today's realised Rs.{state['realized_pnl_today']:,.0f}.")
    if result["new_entries"] or result["new_exits"]:
        if TELEGRAM_SINGLE_DAILY_SUMMARY:
            print(_format_notification(result))   # fills roll up into send_daily_pool_summary.py's message
        else:
            send_telegram_message(_format_notification(result), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bypass the market-hours check (manual testing only)")
    args = parser.parse_args()
    run_tick(force=args.force)


if __name__ == "__main__":
    main()
