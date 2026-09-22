"""
Pool E1 -- Pool E's twin with PARTIAL PROFIT BOOKING (2026-09-22, per direction:
"we should do pools like E1 for partial booking [in crypto]" -- exactly Pool F's
relationship to Pool A, 2026-09-16).

Every Pool E crypto strategy runs here too, on its own fresh 1,000 USDT book
under deployment/state/pool_e1/<strategy_key>/, with the SAME strategy class,
coins, data and fill timing as Pool E -- the ONE difference is
deployment.paper_trading_engine.PartialBookingConfig, the identical numbers
Pool F uses:

    when a position's day High reaches entry x (1 + 5%), half of it is
    sold at that level and the stop on the rest is raised to the entry
    price; the rest then follows the strategy's own exit rule as usual.

Pool E's books and strategy rules are untouched, so the two pools can be
compared book by book -- the same crypto-vs-equities question Pool F/A
already answers for swing, now asked for crypto. The 5% / half / stop-to-entry
numbers are the same disclosed a-priori choice as Pool F's, not a separately
optimised one.

Reuses run_pool_e.py's strategy catalogue, guard and UTC-day logic directly
(POOL_E_STRATEGIES is the one source of truth for which crypto strategies
exist -- a new one added there needs no matching edit here).

Cron (IST, after Pool E's own 05:45 run so both see the same completed UTC day):
    50 5 * * *  cd .../StockTradingBot && venv/bin/python run_pool_e1.py
"""

import argparse
import datetime
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from data.fetch_crypto import fetch_all_crypto_daily
from deployment import paper_trading_engine as pte
from deployment.settings import STATE_DIR
from reporting.pool_e import POOL_E_STARTING_CAPITAL_USDT
from run_pool_e import HISTORY_YEARS, POOL_E_STRATEGIES, _guard, last_completed_utc_day

POOL_E1_STATE_DIR = os.path.join(STATE_DIR, "pool_e1")
PARTIAL_BOOKING = pte.PartialBookingConfig(trigger_pct=0.05, book_fraction=0.5, move_stop_to_entry=True)


def _seed_if_missing(strategy_key: str) -> None:
    import json
    path = os.path.join(POOL_E1_STATE_DIR, strategy_key, "portfolio.json")
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cash": POOL_E_STARTING_CAPITAL_USDT, "starting_capital": POOL_E_STARTING_CAPITAL_USDT,
                   "positions": {}, "last_processed_date": None, "pending_entries": {}, "pending_exits": {},
                   "book_currency": "USDT"}, f, indent=2)
    print(f"[{strategy_key}] Pool E1: new book seeded with {POOL_E_STARTING_CAPITAL_USDT:.0f} USDT")


def run_one(strategy_key: str, as_of: datetime.date, force: bool = False) -> dict:
    factory, symbols, extra_fn = POOL_E_STRATEGIES[strategy_key]
    _seed_if_missing(strategy_key)
    print(f"[{strategy_key}] Pool E1: fetching {HISTORY_YEARS}y of Binance daily candles for {len(symbols)} coin(s)...")
    data = fetch_all_crypto_daily(symbols, years=HISTORY_YEARS)
    result = pte.run_daily(
        strategy_key, factory(), fetch_data_fn=lambda: data, compute_extra_columns_fn=extra_fn,
        as_of_date=as_of, force=force,
        execution_config=pte.ExecutionRealismConfig(fill_timing="same_day_close"),
        min_position_value_rupees=5.0, sizing_capital_cap=POOL_E_STARTING_CAPITAL_USDT,
        partial_booking=PARTIAL_BOOKING,
    )
    print(f"[{strategy_key}] {result}")
    return result


def main(as_of: datetime.date = None, force: bool = False) -> None:
    pte.PAPER_TRADING_STATE_DIR = POOL_E1_STATE_DIR
    as_of = as_of or last_completed_utc_day()
    for strategy_key in POOL_E_STRATEGIES:
        try:
            if _guard(strategy_key):
                run_one(strategy_key, as_of, force=force)
        except Exception as e:
            print(f"ERROR: Pool E1 '{strategy_key}' failed for {as_of}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="UTC day to process (default: the last completed one)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(as_of=datetime.date.fromisoformat(args.date) if args.date else None, force=args.force)
