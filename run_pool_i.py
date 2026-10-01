"""
Pool I -- the US equities paper book (2026-10-01, per explicit direction: "lets
do USA market ... backtest the strategies in the US markets and then pass/fail
and then promote to paper trading"). See reporting/pool_i.py's own docstring
for the full picture (cost model, differences from Pool E).

One book per PASS US strategy under deployment/state/pool_i/<key>/, run by the
SAME deployment/paper_trading_engine.run_daily() every pool uses, with the SAME
swing_research Strategy class the research verdict (EXP-094, EXP-095) was
computed with.

as_of_date is taken from the data itself (the latest date actually present
across the fetched US symbols), not derived from the server clock's day-of-
week/timezone -- US market holidays and the ET/UTC/IST offset make a
date-arithmetic guess (the way Pool E's last_completed_utc_day() works for a
market that never closes) unreliable here; reading it off the real data is
unambiguous and needs no DST-aware logic.

Cron: well after the US close (16:00 ET) comfortably clears any DST
ambiguity between EDT (20:00 UTC close) and EST (21:00 UTC close) -- fires
at 7:00 AM IST (01:30 UTC), IST weekday mornings Tue-Sat (the mornings
after the US Mon-Fri trading sessions, once the IST/ET calendar-day offset
is accounted for):
    30 1 * * 2-6  cd .../StockTradingBot && venv/bin/python run_pool_i.py

    python run_pool_i.py                 # today's run (latest date in the fetched data)
    python run_pool_i.py --date=2026-09-30 [--force]
"""

import argparse
import datetime
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from data.fetch_historical import fetch_all
from deployment import paper_trading_engine as pte
from deployment.base import DeploymentStatus, is_us_equity_record
from deployment.deployment_manager import get_strategy
from deployment.settings import STATE_DIR
from reporting.pool_i import POOL_I_STARTING_CAPITAL_USD
from swing_research.cross_sectional import compute_rs_percentile_ranks, compute_momentum_percentile_ranks
from swing_research.strategies.minervini_trend_template_filter import MinerviniTrendTemplateFilterStrategy
from swing_research.strategies.cross_sectional_momentum import CrossSectionalMomentumStrategy
from swing_research.universe_us import get_swing_universe_us

POOL_I_STATE_DIR = os.path.join(STATE_DIR, "pool_i")
HISTORY_YEARS = 2   # plenty of margin for both strategies' rolling windows (max 252 trading days)

# strategy_key -> (strategy factory, extra-columns fn). Same rename gotcha
# swing_research/strategy_catalog.py's _renamed() guards against: the
# percentile Series' own .name is the SYMBOL until renamed, and
# paper_trading_engine's df.join(extra_columns[symbol]) uses that name for
# the joined column -- precompute() would otherwise never find the column
# it looks for and entry_signal_at() could never signal.
POOL_I_STRATEGIES = {
    "minervini_trend_template_filter_us": (
        MinerviniTrendTemplateFilterStrategy,
        lambda data: {s: series.rename("rs_percentile") for s, series in compute_rs_percentile_ranks(data).items()},
    ),
    "cross_sectional_momentum_us": (
        CrossSectionalMomentumStrategy,
        lambda data: {s: series.rename("momentum_percentile") for s, series in compute_momentum_percentile_ranks(data).items()},
    ),
}


def _seed_if_missing(strategy_key: str) -> None:
    import json
    path = os.path.join(POOL_I_STATE_DIR, strategy_key, "portfolio.json")
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cash": POOL_I_STARTING_CAPITAL_USD, "starting_capital": POOL_I_STARTING_CAPITAL_USD,
                   "positions": {}, "last_processed_date": None, "pending_entries": {}, "pending_exits": {},
                   "book_currency": "USD"}, f, indent=2)
    print(f"[{strategy_key}] Pool I: new book seeded with ${POOL_I_STARTING_CAPITAL_USD:,.2f}")


def _guard(strategy_key: str) -> bool:
    record = get_strategy(strategy_key)
    if record is None or not is_us_equity_record(record):
        print(f"[{strategy_key}] not registered as a US equity strategy -- skipping.")
        return False
    if record.deployment_status != DeploymentStatus.PAPER_TRADING:
        print(f"[{strategy_key}] deployment status is {record.deployment_status} -- skipping.")
        return False
    return True


def run_one(strategy_key: str, symbols: list, data: dict, as_of: datetime.date, force: bool = False) -> dict:
    factory, extra_fn = POOL_I_STRATEGIES[strategy_key]
    _seed_if_missing(strategy_key)
    result = pte.run_daily(
        strategy_key, factory(), fetch_data_fn=lambda: data, compute_extra_columns_fn=extra_fn,
        as_of_date=as_of, force=force,
        execution_config=pte.ExecutionRealismConfig(fill_timing="next_day_open"),
        min_position_value_rupees=5.0, sizing_capital_cap=POOL_I_STARTING_CAPITAL_USD,
    )
    print(f"[{strategy_key}] {result}")
    return result


def main(as_of: datetime.date = None, force: bool = False) -> None:
    pte.PAPER_TRADING_STATE_DIR = POOL_I_STATE_DIR
    symbols = get_swing_universe_us()
    print(f"Pool I: fetching {HISTORY_YEARS}y of yfinance daily candles for {len(symbols)} US symbol(s)...")
    data = fetch_all(symbols, period=f"{HISTORY_YEARS}y")
    if not data:
        print("ERROR: Pool I: no data fetched -- aborting this run entirely.")
        return
    resolved_as_of = as_of or max(df.index.date.max() for df in data.values())
    print(f"Pool I: as-of date {resolved_as_of} (latest date in the fetched data)")
    for strategy_key in POOL_I_STRATEGIES:
        try:
            if _guard(strategy_key):
                run_one(strategy_key, symbols, data, resolved_as_of, force=force)
        except Exception as e:
            print(f"ERROR: Pool I '{strategy_key}' failed for {resolved_as_of}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="US trading day to process (default: the latest in the fetched data)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(as_of=datetime.date.fromisoformat(args.date) if args.date else None, force=args.force)
