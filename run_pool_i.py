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

Pools I2 and I3 (2026-10-19, per explicit direction -- see how the same US
strategies do with more money, without retesting and without restarting
Pool I): the SAME three strategies on their own fresh books of 2x and 3x
POOL_I_STARTING_CAPITAL_USD, under deployment/state/pool_i2/ and pool_i3/.
Everything else is identical -- engine call, Strategy classes, execution
config, notional floor -- except sizing_capital_cap, which is each pool's own
starting capital, so positions scale with the book (FUND ALLOCATION in
ARCHITECTURE.md: size is a percentage of the book's own capital). They run in
THIS invocation, off the one data set it fetches: the VPS has 458 MB of RAM,
so fetching the S&P 500 three times is not an option, and there is no new
cron line. Each pool, and each strategy within it, fails on its own -- one
error never stops the rest.

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
from reporting.pool_i import POOL_I_STARTING_CAPITAL_USD, pool_i_starting_capital  # noqa: F401
from swing_research.cross_sectional import (compute_momentum_percentile_ranks,
                                            compute_rs_percentile_ranks,
                                            compute_short_term_reversal_percentile_ranks)
from swing_research.strategies.minervini_trend_template_filter import MinerviniTrendTemplateFilterStrategy
from swing_research.strategies.cross_sectional_momentum import CrossSectionalMomentumStrategy
from swing_research.strategies.short_term_reversal import ShortTermReversalStrategy
from swing_research.strategy_catalog import PAPER_US_STRATEGIES
from swing_research.universe_us import get_swing_universe_us

POOL_I_STATE_DIR = os.path.join(STATE_DIR, "pool_i")
# label -> folder under STATE_DIR. Pool I first within each strategy.
POOL_I_SUBPOOLS = {"Pool I": "pool_i", "Pool I2": "pool_i2", "Pool I3": "pool_i3"}
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
    # Added 2026-10-10 when its research came back PASS (EXP-096). Same Strategy class and same
    # extra column as research_director.run_short_term_reversal_us_experiment used to produce that
    # verdict -- nothing about the strategy changes between the backtest and the paper book.
    "us_short_term_reversal": (
        ShortTermReversalStrategy,
        lambda data: {s: series.rename("reversal_percentile") for s, series in compute_short_term_reversal_percentile_ranks(data).items()},
    ),
}
# Strategies research wired for paper in swing_research/strategy_catalog.py (2026-10-30) -- a research
# branch may not edit this file, so a passing US strategy is listed there instead.
POOL_I_STRATEGIES.update({k: v for k, v in PAPER_US_STRATEGIES.items() if k not in POOL_I_STRATEGIES})


def _state_dir_of(dirname: str) -> str:
    # Pool I's own folder goes through POOL_I_STATE_DIR so a test that points it elsewhere moves it.
    return POOL_I_STATE_DIR if dirname == "pool_i" else os.path.join(os.path.dirname(POOL_I_STATE_DIR), dirname)


def _seed_if_missing(strategy_key: str, dirname: str = "pool_i", label: str = "Pool I") -> None:
    import json
    capital = pool_i_starting_capital(dirname)
    path = os.path.join(_state_dir_of(dirname), strategy_key, "portfolio.json")
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cash": capital, "starting_capital": capital,
                   "positions": {}, "last_processed_date": None, "pending_entries": {}, "pending_exits": {},
                   "book_currency": "USD"}, f, indent=2)
    print(f"[{strategy_key}] {label}: new book seeded with ${capital:,.2f}")


def _guard(strategy_key: str) -> bool:
    record = get_strategy(strategy_key)
    if record is None or not is_us_equity_record(record):
        print(f"[{strategy_key}] not registered as a US equity strategy -- skipping.")
        return False
    if record.deployment_status != DeploymentStatus.PAPER_TRADING:
        print(f"[{strategy_key}] deployment status is {record.deployment_status} -- skipping.")
        return False
    return True


def run_one(strategy_key: str, symbols: list, data: dict, as_of: datetime.date, force: bool = False,
            dirname: str = "pool_i", label: str = "Pool I", extra_fn=None) -> dict:
    """`extra_fn` lets main() hand the sub-pools the percentile columns it already computed for this
    strategy, rather than ranking the whole S&P 500 again per pool -- same data, same answer."""
    factory, own_extra_fn = POOL_I_STRATEGIES[strategy_key]
    _seed_if_missing(strategy_key, dirname, label)
    pte.PAPER_TRADING_STATE_DIR = _state_dir_of(dirname)
    result = pte.run_daily(
        strategy_key, factory(), fetch_data_fn=lambda: data, compute_extra_columns_fn=extra_fn or own_extra_fn,
        as_of_date=as_of, force=force,
        execution_config=pte.ExecutionRealismConfig(fill_timing="next_day_open"),
        min_position_value_rupees=5.0, sizing_capital_cap=pool_i_starting_capital(dirname),
    )
    print(f"[{strategy_key}] {label}: {result}")
    return result


def _cached(extra_fn):
    """The strategy's extra-columns function, computed at most once per run."""
    memo = {}

    def fn(data):
        if "v" not in memo:
            memo["v"] = extra_fn(data)
        return memo["v"]
    return fn


def main(as_of: datetime.date = None, force: bool = False) -> None:
    symbols = get_swing_universe_us()
    print(f"Pool I: fetching {HISTORY_YEARS}y of yfinance daily candles for {len(symbols)} US symbol(s)...")
    data = fetch_all(symbols, period=f"{HISTORY_YEARS}y")
    if not data:
        print("ERROR: Pool I: no data fetched -- aborting this run entirely.")
        return
    resolved_as_of = as_of or max(df.index.date.max() for df in data.values())
    print(f"Pool I: as-of date {resolved_as_of} (latest date in the fetched data)")
    # Strategy by strategy, each through all three pools, so one strategy's percentile columns are
    # computed once and released before the next strategy's are built -- the VPS's RAM is tight.
    for strategy_key, (_factory, extra_fn) in POOL_I_STRATEGIES.items():
        try:
            if not _guard(strategy_key):
                continue
        except Exception as e:
            print(f"ERROR: Pool I '{strategy_key}' registry check failed: {type(e).__name__}: {e}")
            continue
        shared_extra = _cached(extra_fn)
        for label, dirname in POOL_I_SUBPOOLS.items():
            try:
                run_one(strategy_key, symbols, data, resolved_as_of, force=force,
                        dirname=dirname, label=label, extra_fn=shared_extra)
            except Exception as e:
                print(f"ERROR: {label} '{strategy_key}' failed for {resolved_as_of}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="US trading day to process (default: the latest in the fetched data)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(as_of=datetime.date.fromisoformat(args.date) if args.date else None, force=args.force)
