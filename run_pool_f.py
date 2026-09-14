"""
Pool F -- the crypto paper book (2026-09-13, per explicit direction:
"build portfolio F ... research with our agents and paper trade").

One book per PASS crypto strategy under deployment/state/pool_f/<key>/,
1,000 USDT each, run by the SAME deployment/paper_trading_engine.run_daily()
every Pool A strategy uses -- redirected to the pool_f folder exactly the
way run_pool_a1_legacy.py redirects to its own -- with the SAME
swing_research Strategy class the research verdict was computed with.

Differences from Pool A, all disclosed:
  - Data: Binance daily candles (data/fetch_crypto.py), UTC days, 7 days
    a week. The daily run is for the LAST COMPLETED UTC day, so the cron
    fires after the 00:00 UTC close = 05:30 IST:
        45 5 * * *  cd .../StockTradingBot && venv/bin/python run_pool_f.py
  - Fills: same_day_close. Crypto never closes, so the "next day open" is
    the same instant as the close the signal was computed on; queuing to
    a next open would be a fiction here, not realism.
  - Quantities are fractional (Strategy.fractional_quantities).
  - The engine books RAW price P&L into cash. Fees and India's VDA tax
    are laid over the trade records by reporting/pool_f.py, so the
    Telegram summary and dashboard show pre-tax and post-tax side by side.

    python run_pool_f.py                 # today's run (last completed UTC day)
    python run_pool_f.py --date=2026-09-12 [--force]
"""

import argparse
import datetime
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from data.fetch_crypto import CRYPTO_MAJORS, fetch_all_crypto_daily
from deployment import paper_trading_engine as pte
from deployment.base import DeploymentStatus, is_crypto_record
from deployment.deployment_manager import get_strategy
from deployment.settings import STATE_DIR
from reporting.pool_f import POOL_F_STARTING_CAPITAL_USDT
from swing_research.strategies.crypto_trend_timing import CryptoTrendTimingStrategy, compute_month_end_sma

POOL_F_STATE_DIR = os.path.join(STATE_DIR, "pool_f")
HISTORY_YEARS = 1.5   # 10 month-ends for the SMA plus margin

# strategy_key -> (strategy factory, symbols, extra-columns fn)
POOL_F_STRATEGIES = {
    "crypto_trend_timing": (
        CryptoTrendTimingStrategy, CRYPTO_MAJORS,
        lambda data: {s: compute_month_end_sma(df)[["sma_month_end"]] for s, df in data.items()},
    ),
}


def _seed_if_missing(strategy_key: str) -> None:
    import json
    path = os.path.join(POOL_F_STATE_DIR, strategy_key, "portfolio.json")
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cash": POOL_F_STARTING_CAPITAL_USDT, "starting_capital": POOL_F_STARTING_CAPITAL_USDT,
                   "positions": {}, "last_processed_date": None, "pending_entries": {}, "pending_exits": {},
                   "book_currency": "USDT"}, f, indent=2)
    print(f"[{strategy_key}] Pool F: new book seeded with {POOL_F_STARTING_CAPITAL_USDT:.0f} USDT")


def _guard(strategy_key: str) -> bool:
    record = get_strategy(strategy_key)
    if record is None or not is_crypto_record(record):
        print(f"[{strategy_key}] not registered as a crypto strategy -- skipping.")
        return False
    if record.deployment_status != DeploymentStatus.PAPER_TRADING:
        print(f"[{strategy_key}] deployment status is {record.deployment_status} -- skipping.")
        return False
    return True


def last_completed_utc_day(now: datetime.datetime = None) -> datetime.date:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(days=1)).date()


def run_one(strategy_key: str, as_of: datetime.date, force: bool = False) -> dict:
    factory, symbols, extra_fn = POOL_F_STRATEGIES[strategy_key]
    _seed_if_missing(strategy_key)
    print(f"[{strategy_key}] Pool F: fetching {HISTORY_YEARS}y of Binance daily candles for {len(symbols)} coin(s)...")
    data = fetch_all_crypto_daily(symbols, years=HISTORY_YEARS)
    result = pte.run_daily(
        strategy_key, factory(), fetch_data_fn=lambda: data, compute_extra_columns_fn=extra_fn,
        as_of_date=as_of, force=force,
        execution_config=pte.ExecutionRealismConfig(fill_timing="same_day_close"),
        min_position_value_rupees=5.0, sizing_capital_cap=POOL_F_STARTING_CAPITAL_USDT,
    )
    print(f"[{strategy_key}] {result}")
    return result


def main(as_of: datetime.date = None, force: bool = False) -> None:
    pte.PAPER_TRADING_STATE_DIR = POOL_F_STATE_DIR
    as_of = as_of or last_completed_utc_day()
    for strategy_key in POOL_F_STRATEGIES:
        try:
            if _guard(strategy_key):
                run_one(strategy_key, as_of, force=force)
        except Exception as e:
            print(f"ERROR: Pool F '{strategy_key}' failed for {as_of}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="UTC day to process (default: the last completed one)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(as_of=datetime.date.fromisoformat(args.date) if args.date else None, force=args.force)
