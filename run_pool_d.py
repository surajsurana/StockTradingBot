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

Universe (2026-09-10, per explicit direction): the Nifty 500 -- the same
frozen universe Pool A trades (swing_research/universe.py, ~457 names,
Kite bare symbols). Was the 20-name, then 150-name, LIQUID_UNIVERSE.
At Kite's 3-requests/second ceiling that is ~2.5 minutes of fetching per
tick, done concurrently (data/fetch_kite_intraday.py's rate limiter
paces every request) so it fits the 5-minute cron slot; a lock file
guards against two ticks overlapping if one runs long.

The day's context (90 days of history per symbol -> the strategy's ATR
and VWAP-extension baselines) is computed BEFORE the open by a separate
--prepare run at 09:00, one symbol at a time so the whole universe's
history is never held in memory (the VPS has 458 MB). If --prepare did
not run, the first tick does it itself (slower first tick, same result).

    python run_pool_d.py --prepare   # 09:00 cron: today's context + daily counters reset
    python run_pool_d.py             # */5 09:15-15:30 cron: one tick
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
from research_lab.backtesting_engineer import _compute_day_context
from swing_research.universe import get_swing_universe

from pool_d.engine import process_tick
from pool_d.state import POOL_D_LOCK_PATH, POOL_D_STATE_DIR, load_state, save_state

POOL_D_SYMBOLS = [s[:-3] if s.endswith(".NS") else s for s in get_swing_universe()]   # Kite wants bare symbols
STARTING_CAPITAL = settings.RESEARCH_LAB_VIRTUAL_CAPITAL   # Rs.1,00,000 -- ONE shared book
CONTEXT_HISTORY_DAYS = 90   # comfortably covers atr_14d (needs 15) + the 20-day extension baseline
LOCK_STALE_MINUTES = 15
FETCH_WORKERS = 3

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


def prepare_day(state: dict, today: datetime.date) -> None:
    """Today's context for every symbol (one symbol in memory at a time)
    plus the daily counter reset. Idempotent per day."""
    print(f"Preparing {today.isoformat()} -- resetting daily counters and computing fresh context "
          f"({len(POOL_D_SYMBOLS)} symbols, {CONTEXT_HISTORY_DAYS}-day history, {FETCH_WORKERS} workers)...")
    from_date = today - datetime.timedelta(days=CONTEXT_HISTORY_DAYS)
    context_by_symbol = {}

    def reduce_to_context(symbol, df):
        context_by_symbol[symbol] = _compute_day_context(df, today)

    fetch_all_intraday(POOL_D_SYMBOLS, "5minute", from_date, today, settings,
                       max_workers=FETCH_WORKERS, on_symbol=reduce_to_context)
    state["context_by_symbol"] = context_by_symbol
    state["positions"] = {}   # nothing is ever held overnight
    state["trades_today_by_symbol"] = {}
    state["realized_pnl_today"] = 0.0
    state["last_processed_date"] = today.isoformat()
    save_state(state)
    print(f"Context ready for {len(context_by_symbol)} symbol(s).")


def run_prepare(now: datetime.datetime = None) -> None:
    now = now or datetime.datetime.now()
    if now.weekday() >= 5:
        print("Weekend -- nothing to prepare.")
        return
    if not _acquire_lock(now):
        print("Another Pool D process is running -- skipping prepare.")
        return
    try:
        state = load_state(STARTING_CAPITAL)
        if state["last_processed_date"] == now.date().isoformat():
            print("Already prepared for today.")
            return
        prepare_day(state, now.date())
    finally:
        _release_lock()


def _run_tick_locked(now: datetime.datetime) -> dict:
    state = load_state(STARTING_CAPITAL)
    today = now.date()

    if state["last_processed_date"] != today.isoformat():
        prepare_day(state, today)   # --prepare didn't run (or failed): do it now, slower first tick

    todays_bars = fetch_all_intraday(POOL_D_SYMBOLS, "5minute", today, today, settings, max_workers=FETCH_WORKERS)
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
    parser.add_argument("--prepare", action="store_true",
                         help="Compute today's context before the open (09:00 cron); no trading")
    args = parser.parse_args()
    if args.prepare:
        run_prepare()
    else:
        run_tick(force=args.force)


if __name__ == "__main__":
    main()
