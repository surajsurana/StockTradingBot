"""
Pool G -- the crypto AI judgment book (2026-09-17, per direction: "can
we do a llm call crypto strategy as another pool?"). No historical
backtest (see portfolio_g/state.py); paper-traded live from day one.

Meant to run TWICE a day so it can react within the day, since crypto
trades all day -- the mechanical 18% stop is checked every run
regardless of what the LLM says (see portfolio_g/daily.py).

Cron (IST):
    30 8  * * *  venv/bin/python run_pool_g.py
    30 20 * * *  venv/bin/python run_pool_g.py

    python run_pool_g.py [--force-history-years=2]
"""

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from data.fetch_crypto import CRYPTO_MAJORS, fetch_all_crypto_daily, fetch_crypto_last_prices
from portfolio_g.daily import run_pool_g_cycle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=float, default=1.5, help="days of history fetched for the SMA/pct-change context")
    args = parser.parse_args()

    print(f"Pool G: fetching {args.years}y of daily context for {len(CRYPTO_MAJORS)} coin(s)...")
    daily_history = fetch_all_crypto_daily(CRYPTO_MAJORS, years=args.years)
    print(f"Pool G: context ready for {len(daily_history)} coin(s)")

    result = run_pool_g_cycle(daily_history, fetch_crypto_last_prices, settings.ANTHROPIC_API_KEY)
    print(f"Pool G: {result}")


if __name__ == "__main__":
    main()
