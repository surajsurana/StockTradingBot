"""
Pool F -- Pool A's twin with PARTIAL PROFIT BOOKING (2026-09-16, per
direction: "a pool F is better rather than an overlay so we can totally
make out if strategies are better off as is or with a partial booking
mechanism. do not change anything in strategies or pool A").

Every Pool A strategy runs here too (PEAD included, through its own
event engine with the same overlay), on its own fresh Rs.1,00,000 book
under deployment/state/pool_f/<strategy_key>/, with the SAME strategy
class, universe, data, fill timing and sizing as Pool A -- the ONE
difference is deployment.paper_trading_engine.PartialBookingConfig:

    when a position's day High reaches entry x (1 + 5%), half of it is
    sold at that level and the stop on the rest is raised to the entry
    price; the rest then follows the strategy's own exit rule as usual.

Pool A's books and strategy rules are untouched, so the two pools can be
compared book by book. The 5% / half / stop-to-entry numbers are a
disclosed a-priori choice, not an optimised one.

Cron (weekdays, IST):
    38 15 * * 1-5  venv/bin/python run_pool_f.py                    # after Pool A's 15:35 run
    33 9  * * 1-5  venv/bin/python run_pool_f.py --resolve-at-open  # fills queued at the open
"""

import argparse
import datetime
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import deployment.paper_trading_engine as pte
from data.fetch_historical import fetch_all
from deployment.base import DeploymentStatus, is_crypto_record
from deployment.deployment_manager import get_strategy
from deployment.settings import STATE_DIR
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS
from swing_research.universe import get_swing_universe

PEAD_KEY = "pead"   # event-driven, outside the catalog -- run through its own engine with the same overlay

POOL_F_STATE_DIR = os.path.join(STATE_DIR, "pool_f")
PARTIAL_BOOKING = pte.PartialBookingConfig(trigger_pct=0.05, book_fraction=0.5, move_stop_to_entry=True)
_DEFAULT_EXECUTION_CONFIG = pte.ExecutionRealismConfig(fill_timing="next_day_open")   # same as Pool A
_SPECS_BY_KEY = {spec.strategy_key: spec for spec in PAPER_TRADING_STRATEGY_SPECS}


def pool_f_strategy_keys() -> list:
    """Every catalog strategy that is PAPER_TRADING in the registry and is
    not a crypto strategy -- i.e. exactly Pool A's active books."""
    keys = []
    for spec in PAPER_TRADING_STRATEGY_SPECS:
        record = get_strategy(spec.strategy_key)
        if record is not None and record.deployment_status == DeploymentStatus.PAPER_TRADING and not is_crypto_record(record):
            keys.append(spec.strategy_key)
    return keys


def _pead_active() -> bool:
    record = get_strategy(PEAD_KEY)
    return record is not None and record.deployment_status == DeploymentStatus.PAPER_TRADING


def _execution_config(spec) -> pte.ExecutionRealismConfig:
    return spec.execution_config_factory() if getattr(spec, "execution_config_factory", None) else _DEFAULT_EXECUTION_CONFIG


def run_all(as_of: datetime.date = None, force: bool = False) -> None:
    pte.PAPER_TRADING_STATE_DIR = POOL_F_STATE_DIR
    keys = pool_f_strategy_keys()
    if not keys:
        print("Pool F: no active Pool A strategies to mirror.")
        return
    symbols = get_swing_universe()
    print(f"Pool F: fetching 3y of data for {len(symbols)} symbol(s) once, shared by {len(keys)} book(s)...")
    data = fetch_all(symbols, period="3y")
    print(f"Pool F: data available for {len(data)} symbol(s)")
    for key in keys:
        spec = _SPECS_BY_KEY[key]
        try:
            strategy = spec.strategy_factory()
            extra_fn = spec.compute_extra_columns_fn
            result = pte.run_daily(
                key, strategy, fetch_data_fn=lambda d=data: d,
                compute_extra_columns_fn=(lambda d, fn=extra_fn: fn(d)) if extra_fn else None,
                as_of_date=as_of, force=force, execution_config=_execution_config(spec),
                partial_booking=PARTIAL_BOOKING,
            )
            print(f"[{key}] Pool F: {result['status']} entries {len(result.get('new_entries', []))} "
                  f"exits {len(result.get('new_exits', []))} partial {len(result.get('new_partial_exits', []))} "
                  f"queued {len(result.get('new_pending_entries', []))}/{len(result.get('new_pending_exits', []))} "
                  f"cash {result.get('cash')}")
        except Exception as e:
            print(f"ERROR: Pool F '{key}' failed: {type(e).__name__}: {e}")
    if _pead_active():
        try:
            from deployment.pead_forward_engine import run_pead_daily
            result = run_pead_daily(symbols, as_of_date=as_of, force=force,
                                    fetch_ohlcv_fn=lambda syms: {s: data[s] for s in syms if s in data} or fetch_all(syms, period="3y"),
                                    execution_config=_DEFAULT_EXECUTION_CONFIG, partial_booking=PARTIAL_BOOKING,
                                    state_dir=POOL_F_STATE_DIR)
            print(f"[{PEAD_KEY}] Pool F: {result.get('status')} entries {len(result.get('new_entries', []))} "
                  f"exits {len(result.get('new_exits', []))} partial {len(result.get('new_partial_exits', []))}")
        except Exception as e:
            print(f"ERROR: Pool F '{PEAD_KEY}' failed: {type(e).__name__}: {e}")


def resolve_all_at_open() -> None:
    pte.PAPER_TRADING_STATE_DIR = POOL_F_STATE_DIR
    for key in pool_f_strategy_keys():
        spec = _SPECS_BY_KEY[key]
        try:
            portfolio = pte.load_portfolio(key)
            pending = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
            if not pending:
                continue
            result = pte.resolve_pending_fills_at_open(
                key, spec.strategy_factory(), fetch_open_data_fn=lambda p=pending: fetch_all(p, period="5d"),
                execution_config=_execution_config(spec),
            )
            print(f"[{key}] Pool F open: {result}")
        except Exception as e:
            print(f"ERROR: Pool F '{key}' failed at the open: {type(e).__name__}: {e}")
    if _pead_active():
        try:
            from swing_research.strategies.pead import PEADStrategy
            portfolio = pte.load_portfolio(PEAD_KEY)
            pending = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
            if pending:
                result = pte.resolve_pending_fills_at_open(
                    PEAD_KEY, PEADStrategy(), fetch_open_data_fn=lambda p=pending: fetch_all(p, period="5d"),
                    execution_config=_DEFAULT_EXECUTION_CONFIG)
                print(f"[{PEAD_KEY}] Pool F open: {result}")
        except Exception as e:
            print(f"ERROR: Pool F '{PEAD_KEY}' failed at the open: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve-at-open", action="store_true")
    parser.add_argument("--date", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.resolve_at_open:
        resolve_all_at_open()
    else:
        run_all(as_of=datetime.date.fromisoformat(args.date) if args.date else None, force=args.force)
