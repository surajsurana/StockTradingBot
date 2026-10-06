"""
The live runner (Pool L) -- Gate H, step 1 of 2.

*** THIS PLACES NO ORDERS. *** It runs the SAME engine the paper pools run, against a
separate live book sized to the strategy's assigned capital, and reports the orders it
WOULD place. Actually sending them to Kite is a separate change
(deployment/live_guard.py gates it) that is deliberately not in this file yet.

WHY A SEPARATE RUNNER AND NOT A FLAG ON THE PAPER ONE. The paper book must keep
running untouched alongside live -- it is the control. If live underperforms paper on
identical signals, the gap is slippage and execution cost, which is precisely what
paper cannot model and what most often kills a live strategy. A flag that switched the
existing runner over would destroy the comparison it exists to make.

HOW IT STAYS IDENTICAL TO PAPER. Nothing about the strategy changes. The engine's
sizing is

    quantity = floor( min(cash, sizing_cap) * risk_pct_per_unit / risk_per_share )

which is strictly proportional to capital, so a book funded with a fraction of paper's
capital takes proportionally smaller positions by itself -- same signals, same entries,
same exits, same percentages. The one thing that does NOT scale is the floor(): shares
are indivisible, so a quantity below one share becomes zero. Measured on 306 real Pool A
orders, a 5% book loses 92% of them. That is physics, not a bug, and it is why
deployment/LIVE_PROMOTION_CRITERIA.md says exact replication needs 1:1 capital.

SAFETY. It refuses to run a strategy that is not PILOT_LIVE/PRODUCTION, or that has no
assigned capital. It writes only under deployment/state/live/ -- never the paper book,
whose track record is the thing being compared against.
"""

import argparse
import os
import sys
from datetime import date as date_type
from typing import Optional

import deployment.paper_trading_engine as pte
from deployment.base import DeploymentStatus
from deployment.deployment_manager import list_strategies
from deployment.live_allocations import allocation_for
from deployment.settings import STATE_DIR
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS

LIVE_STATE_DIR = os.path.join(STATE_DIR, "live", "paper_trading")
_SPECS_BY_KEY = {spec.strategy_key: spec for spec in PAPER_TRADING_STRATEGY_SPECS}


def eligible_strategies(state_dir: str = STATE_DIR) -> list:
    """(record, allocated) for every strategy that is BOTH promoted to live AND funded. Promotion
    without funding is a deliberate, safe intermediate state -- the strategy sizes every position to
    zero and holds nothing -- so it is skipped here rather than treated as an error."""
    out = []
    for record in list_strategies():
        if record.deployment_status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
            continue
        allocated = allocation_for(state_dir, record.strategy_key)
        if allocated > 0:
            out.append((record, allocated))
    return out


def intended_orders(result: dict) -> list:
    """The orders this run WOULD place, read off the engine's own result. Entries and exits that the
    engine queued for the next open count too -- they are decisions already made."""
    orders = []
    for entry in result.get("new_entries", []) or []:
        orders.append({"side": "BUY", "symbol": entry.get("symbol"), "quantity": entry.get("quantity"),
                       "price": entry.get("entry_price"), "when": "filled"})
    for exit_ in result.get("new_exits", []) or []:
        orders.append({"side": "SELL", "symbol": exit_.get("symbol"), "quantity": exit_.get("quantity"),
                       "price": exit_.get("exit_price"), "when": "filled"})
    for pending in result.get("new_pending_entries", []) or []:
        orders.append({"side": "BUY", "symbol": pending.get("symbol"), "quantity": pending.get("quantity"),
                       "price": None, "when": "next open"})
    for pending in result.get("new_pending_exits", []) or []:
        orders.append({"side": "SELL", "symbol": pending.get("symbol"), "quantity": pending.get("quantity"),
                       "price": None, "when": "next open"})
    return orders


def run_live(strategy_key: str, fetch_data_fn, as_of: Optional[date_type] = None,
             force: bool = False, state_dir: str = STATE_DIR) -> dict:
    """
    Runs one strategy against its LIVE book and returns what it would do.

    Refuses rather than guesses: an unpromoted or unfunded strategy returns a refusal with a reason
    and touches nothing. The engine is pointed at the live state directory for the duration of the
    call and restored afterwards, the same mechanism run_pool_f.py uses to give a pool its own book.
    """
    record = next((r for r in list_strategies() if r.strategy_key == strategy_key), None)
    if record is None:
        return {"status": "refused", "reason": f"{strategy_key} is not in the registry."}
    if record.deployment_status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
        return {"status": "refused",
                "reason": f"{strategy_key} is {record.deployment_status.value}, not PILOT_LIVE or PRODUCTION."}
    allocated = allocation_for(state_dir, strategy_key)
    if allocated <= 0:
        return {"status": "refused",
                "reason": f"{strategy_key} has no live capital assigned -- assign it on the Live view first."}
    spec = _SPECS_BY_KEY.get(strategy_key)
    if spec is None:
        return {"status": "refused", "reason": f"{strategy_key} has no paper-trading spec to run."}

    live_dir = os.path.join(state_dir, "live", "paper_trading")
    previous = pte.PAPER_TRADING_STATE_DIR
    try:
        pte.PAPER_TRADING_STATE_DIR = live_dir       # same redirection run_pool_f.py uses
        _ensure_book(live_dir, strategy_key, allocated)
        extra_fn = spec.compute_extra_columns_fn
        result = pte.run_daily(
            strategy_key, spec.strategy_factory(), fetch_data_fn=fetch_data_fn,
            compute_extra_columns_fn=(lambda d, fn=extra_fn: fn(d)) if extra_fn else None,
            as_of_date=as_of, force=force,
        )
    finally:
        pte.PAPER_TRADING_STATE_DIR = previous       # always restored, even on an exception
    return {"status": result.get("status"), "allocated": allocated, "engine": result,
            "orders": intended_orders(result)}


def _ensure_book(live_dir: str, strategy_key: str, allocated: float) -> None:
    """Creates the live book on first run, funded with the assigned capital. Never resets an existing
    book: its cash is the real, already-traded balance, and overwriting it would silently rewrite the
    live track record being compared against paper."""
    path = os.path.join(live_dir, strategy_key, "portfolio.json")
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"starting_capital": allocated, "cash": allocated, "positions": {},
                   "pending_entries": [], "pending_exits": []}, f, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the live book and report the orders it would place.")
    ap.add_argument("--strategy", default=None, help="one strategy key (default: every eligible one)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    targets = ([(r, a) for r, a in eligible_strategies() if r.strategy_key == args.strategy]
               if args.strategy else eligible_strategies())
    if not targets:
        print("Nothing to run: no strategy is both promoted to live and funded.")
        print("Promote one to PILOT_LIVE, then assign it capital on the dashboard's Live view.")
        return

    from data.fetch_historical import fetch_all
    from swing_research.universe import get_swing_universe
    data = fetch_all(get_swing_universe(), period="2y")

    for record, allocated in targets:
        result = run_live(record.strategy_key, fetch_data_fn=lambda d=data: d, force=args.force)
        if result["status"] == "refused":
            print(f"[{record.strategy_key}] refused: {result['reason']}")
            continue
        print(f"[{record.strategy_key}] live book Rs{allocated:,.0f}: {result['status']}")
        for order in result["orders"]:
            price = "market" if order["price"] is None else f"{order['price']:.2f}"
            print(f"    WOULD {order['side']:4s} {order['quantity']} x {order['symbol']} @ {price} ({order['when']})")
        if not result["orders"]:
            print("    no orders today")
    print("\nNo orders were placed. Sending them to the broker is a separate, "
          "deliberately unbuilt step (see deployment/live_guard.py).")


if __name__ == "__main__":
    main()
