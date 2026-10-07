"""
The live runner (Pool L) -- Gate H, step 1 of 2.

It runs the SAME engine the paper pools run, against a separate live book sized to the
strategy's assigned capital, then places what it decided through
deployment/live_executor.py -- which calls deployment/live_guard.py itself, so there is no
path from here to a broker that skips the guard. dry_run=True is the default: the whole
path runs and nothing is sent.

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
from swing_research.broker_costs import min_viable_notional
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
    """The orders this run decided on, each marked with whether it can actually be SENT yet.

    A filled entry or exit has a real price and a real quantity, so it can be placed now. A QUEUED
    one has neither: a next-day-open entry is sized from tomorrow's open, which does not exist yet,
    so `quantity` is None and there is nothing to send. They are still reported, because they are
    decisions already made and the dashboard should show them -- but `placeable` is False, and the
    runner must not hand them to an executor. Before 2026-10-07 it did, which on the equity side
    meant every after-close run tried to buy a None quantity at a None price."""
    orders = []
    for entry in result.get("new_entries", []) or []:
        orders.append({"side": "BUY", "symbol": entry.get("symbol"), "quantity": entry.get("quantity"),
                       "price": entry.get("entry_price"), "when": "filled", "placeable": True})
    for exit_ in result.get("new_exits", []) or []:
        orders.append({"side": "SELL", "symbol": exit_.get("symbol"), "quantity": exit_.get("quantity"),
                       "price": exit_.get("exit_price"), "when": "filled", "placeable": True})
    for pending in result.get("new_pending_entries", []) or []:
        orders.append({"side": "BUY", "symbol": pending.get("symbol"), "quantity": pending.get("quantity"),
                       "price": None, "when": "next open", "placeable": False})
    for pending in result.get("new_pending_exits", []) or []:
        orders.append({"side": "SELL", "symbol": pending.get("symbol"), "quantity": pending.get("quantity"),
                       "price": None, "when": "next open", "placeable": False})
    return orders


def _engine_plan(strategy_key: str):
    """How to run one strategy: its factory, its state folder, and the engine settings its pool uses.

    Pool A/F and Pool E both run deployment/paper_trading_engine.run_daily(); they differ only in
    where the books live, how fills are timed, and the sizing cap. Resolving that here is what lets a
    single live runner serve both instead of each pool growing its own copy."""
    spec = _SPECS_BY_KEY.get(strategy_key)
    if spec is not None:
        # min_value: the same charge-derived floor run_paper_trading.py and run_pool_f.py apply.
        # Without it the LIVE equity book would take the Rs3,532 positions the paper books were
        # taking -- with real money, and paying the flat DP charge for real.
        return {"factory": spec.strategy_factory, "extra_fn": spec.compute_extra_columns_fn,
                "dirname": "paper_trading", "execution": None, "cap": None,
                "min_value": min_viable_notional()}
    try:
        from run_pool_e import POOL_E_STARTING_CAPITAL_USDT, POOL_E_STRATEGIES
    except Exception:
        return None
    entry = POOL_E_STRATEGIES.get(strategy_key)
    if entry is None:
        return None
    factory, _symbols, extra_fn = entry
    return {"factory": factory, "extra_fn": extra_fn, "dirname": "pool_e",
            "execution": pte.ExecutionRealismConfig(fill_timing="same_day_close"),
            "cap": POOL_E_STARTING_CAPITAL_USDT, "min_value": 5.0}


def run_live(strategy_key: str, fetch_data_fn, as_of: Optional[date_type] = None,
             force: bool = False, state_dir: str = STATE_DIR, settings=None, client=None,
             place_fn=None, dry_run: bool = True, now=None, usdinr: float = 0.0,
             resolve_at_open: bool = False) -> dict:
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
    # Resolved here rather than just before placing, because reconciliation needs it too: the venue
    # says both which broker executes and how that broker spells an instrument.
    from deployment.venues import COINDCX, venue_of
    venue = venue_of(record)
    allocated = allocation_for(state_dir, strategy_key)
    if allocated <= 0:
        return {"status": "refused",
                "reason": f"{strategy_key} has no live capital assigned -- assign it on the Live view first."}
    # WHICH ENGINE CONFIGURATION this strategy runs under. Pool A/F and Pool E are the same engine
    # with different settings and different state directories, so one runner serves both -- but it
    # must use the right ones, or a crypto strategy would be run against the equity book's folder
    # with the equity book's fill timing.
    plan = _engine_plan(strategy_key)
    if plan is None:
        return {"status": "refused",
                "reason": f"{strategy_key} has no live adapter -- no engine knows how to run it."}

    live_dir = os.path.join(state_dir, "live", plan["dirname"])
    book_path = os.path.join(live_dir, strategy_key, "portfolio.json")

    # BEFORE the cycle, not after -- see run_pool_g_live.py for the full reasoning. The engine writes
    # its decisions into the book as part of deciding, so reconciling afterwards compares this run's
    # own unplaced decisions against a broker that cannot know about them, and halts forever.
    recon = {"ok": True, "problems": [], "notes": []}
    if not dry_run and client is not None:
        from deployment.reconciliation import reconcile_against_exchange
        # venue decides how instruments are spelled on each side: the book holds RELIANCE.NS while
        # Kite reports RELIANCE, and comparing those raw would halt every equity run on day one.
        check = reconcile_against_exchange(book_path, client, now=now, venue=venue)
        recon = {"ok": check.ok, "checked_at": check.checked_at, "problems": list(check.problems),
                 "notes": list(check.notes),
                 "positions": [{"symbol": p.symbol, "book": p.book_quantity,
                                "exchange": p.exchange_quantity, "verdict": p.verdict}
                               for p in check.positions]}
        if not check.ok:
            return {"status": "halted", "allocated": allocated, "orders": [], "placed": [],
                    "reconciliation": recon,
                    "reason": "The book and the broker disagree, so the strategy did not run. "
                              + " ".join(check.problems)}

    previous = pte.PAPER_TRADING_STATE_DIR
    try:
        pte.PAPER_TRADING_STATE_DIR = live_dir       # same redirection run_pool_f.py uses
        _ensure_book(live_dir, strategy_key, allocated)
        extra_fn = plan["extra_fn"]
        engine_kwargs = {}
        if plan["execution"] is not None:
            engine_kwargs["execution_config"] = plan["execution"]
        if plan["cap"] is not None:
            engine_kwargs["sizing_capital_cap"] = plan["cap"]
        if plan["min_value"] is not None:
            engine_kwargs["min_position_value_rupees"] = plan["min_value"]
        if resolve_at_open:
            # THE EQUITY SIDE'S SECOND HALF. An Indian strategy decides after the close and queues
            # its entries for the next open, because that is when they can actually be bought. The
            # after-close run therefore has nothing to send; this run is where a queued decision
            # becomes a real order, priced and sized against today's actual Open. Crypto never
            # takes this path -- it fills same-day-close, so its orders are placeable immediately.
            # No signal detection happens here, so it cannot decide anything new.
            result = pte.resolve_pending_fills_at_open(
                strategy_key, plan["factory"](), fetch_open_data_fn=fetch_data_fn,
                as_of_date=as_of,
                **({"execution_config": plan["execution"]} if plan["execution"] is not None else {}),
            )
        else:
            result = pte.run_daily(
                strategy_key, plan["factory"](), fetch_data_fn=fetch_data_fn,
                compute_extra_columns_fn=(lambda d, fn=extra_fn: fn(d)) if extra_fn else None,
                as_of_date=as_of, force=force, **engine_kwargs,
            )
    finally:
        pte.PAPER_TRADING_STATE_DIR = previous       # always restored, even on an exception
    orders = intended_orders(result)
    if dry_run:
        return {"status": "dry_run", "allocated": allocated, "engine": result, "orders": orders,
                "placed": []}

    if settings is None:
        from config import settings as settings            # noqa: PLC0415

    # THE EXECUTOR FOLLOWS THE VENUE. This was hardcoded to the equity one, so a Pool E crypto
    # strategy promoted here would have had its orders sent to Kite -- the right book, the wrong
    # exchange, and nothing in the code to notice.
    place = place_fn
    if place is None:
        if venue == COINDCX:
            from deployment.crypto_executor import place_crypto_order as place
        else:
            from deployment.live_executor import place_live_order as place
    placed = []
    for order in orders:
        if not order.get("placeable", True):
            continue          # queued for the next open; it becomes an order when it has a price
        common = dict(settings=settings, record=record, state_dir=state_dir,
                      symbol=order["symbol"], side=order["side"], quantity=order["quantity"],
                      strategy_key=strategy_key, now=now)
        if venue == COINDCX:
            # a crypto book prices in USDT and the caps are in rupees, so the executor needs the rate
            outcome = place(reference_price_usdt=order.get("price"), usdinr=usdinr,
                            client=client, **common)
        else:
            outcome = place(reference_price=order.get("price"), **common)
        placed.append({**order, "placed": bool(outcome.placed), "order_id": outcome.order_id,
                       "fill_price": outcome.fill_price, "reasons": list(outcome.reasons)})
    unplaced = [p for p in placed if not p["placed"]]
    return {"status": result.get("status"), "allocated": allocated, "engine": result,
            "orders": orders, "placed": placed, "reconciliation": recon,
            "divergence": [f"{p['side']} {p['quantity']} {p['symbol']}: "
                           f"{' '.join(p['reasons'] or ['not placed'])}" for p in unplaced]}


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
