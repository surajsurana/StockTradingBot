"""
Pool G against real money (Gate H, the crypto path).

WHAT IT DOES. Runs Pool G's OWN cycle -- the same LLM judgement, the same 18% mechanical stop, the
same sizing -- against a separate live book, then sends the orders it decided on to CoinDCX through
deployment/crypto_executor.py. Nothing about the strategy changes; only the book it runs against and
the fact that its orders are real.

WHY A SEPARATE RUNNER, NOT A FLAG. The paper book must keep running untouched alongside: it is the
control. If live underperforms paper on identical decisions, the gap is slippage, fees and the India
premium -- exactly what paper cannot model and what most often kills a live strategy. A flag on the
existing runner would destroy the comparison it exists to make.

THE CURRENCY SPLIT, which is the one genuinely subtle thing here. Pool G decides in USDT and must:
build_snapshot() divides today's price by 300 days of Binance USDT closes, so feeding it rupee prices
would turn every percentage into nonsense and corrupt the judgement itself. So the live book is kept
in USDT exactly like the paper one, seeded with the rupees you allocated converted at the day's rate,
and the decisions come out as QUANTITIES IN COIN UNITS -- which are currency-agnostic. Only at the
execution boundary does anything become rupees, where crypto_executor prices that quantity at
CoinDCX's own INR market.

The consequence is deliberate: the live book's own P&L is a USDT view that will NOT exactly match the
rupees that moved, because of the India premium (+2.2% to +3.0% measured 2026-10-06). Every order
records the rupee price, the USDT reference and the premium between them, so the difference is
measurable rather than mysterious.

SAFETY. It refuses to run a strategy that is not PILOT_LIVE/PRODUCTION or that has no assigned
capital. Every order still passes deployment/live_guard.py -- LIVE_TRADING, the kill switch,
credentials, deployment status, the pilot gates and the hard caps -- so this runner cannot place an
order the guard would not allow. It writes only under deployment/state/live/, never the paper book.
"""

import argparse
import os
from typing import Optional

from deployment.base import DeploymentStatus
from deployment.deployment_manager import list_strategies
from deployment.live_allocations import allocation_for
from deployment.settings import STATE_DIR

POOL_G_KEY = "portfolio_g"
LIVE_POOL_G_DIRNAME = os.path.join("live", "pool_g")


def live_dir(state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, LIVE_POOL_G_DIRNAME)


def intended_orders(result: dict) -> list:
    """The orders this cycle decided on, read off Pool G's own result.

    Three sources, and all three are real orders: `stopped` is the mechanical 18% stop firing,
    `sold` is the model deciding to exit, `bought` is it deciding to enter. The stop is listed first
    because it is the one that must never be dropped -- it is the risk control."""
    orders = []
    for trade in result.get("stopped", []) or []:
        orders.append({"side": "SELL", "symbol": trade.get("symbol"), "quantity": trade.get("quantity"),
                       "price": trade.get("exit_price"), "why": "stop loss"})
    for trade in result.get("sold", []) or []:
        orders.append({"side": "SELL", "symbol": trade.get("symbol"), "quantity": trade.get("quantity"),
                       "price": trade.get("exit_price"), "why": "model sell"})
    for buy in result.get("bought", []) or []:
        orders.append({"side": "BUY", "symbol": buy.get("symbol"), "quantity": buy.get("quantity"),
                       "price": buy.get("price"), "why": "model buy"})
    return [o for o in orders if o["symbol"] and o["quantity"] and o["price"]]


def _ensure_book(path: str, starting_usdt: float) -> None:
    """Creates the live book on first run. NEVER resets an existing one: its cash and positions are
    the real, already-traded balance, and overwriting it would silently rewrite the live record."""
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from deployment.atomic_write import write_json
    write_json(path, {"cash": starting_usdt, "starting_capital": starting_usdt, "positions": {},
                      "last_run_at": None})


def run_live(fetch_data_fn, fetch_prices_fn, api_key: str, usdinr: float,
             state_dir: str = STATE_DIR, settings=None, client=None,
             place_fn=None, now=None, dry_run: bool = True) -> dict:
    """
    Runs Pool G's cycle against the live book and places the orders it decides on.

    `dry_run=True` is the DEFAULT and runs the whole path except the send -- the cycle, the book, the
    guard -- reporting what it would do. Placing real orders requires passing dry_run=False
    explicitly, so no caller does it by accident.
    """
    import portfolio_g.state as pg_state
    from portfolio_g.daily import run_pool_g_cycle

    record = next((r for r in list_strategies() if r.strategy_key == POOL_G_KEY), None)
    if record is None:
        return {"status": "refused", "reason": f"{POOL_G_KEY} is not in the registry."}
    if record.deployment_status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
        shown = getattr(record.deployment_status, "value", record.deployment_status)
        return {"status": "refused", "reason": f"Pool G is {shown}, not PILOT_LIVE or PRODUCTION."}
    allocated_inr = allocation_for(state_dir, POOL_G_KEY)
    if allocated_inr <= 0:
        return {"status": "refused",
                "reason": "Pool G has no live capital assigned -- assign it on the Live view first."}
    if not usdinr or usdinr <= 0:
        return {"status": "refused", "reason": "No USD/INR rate, so the book cannot be sized."}

    target = os.path.join(live_dir(state_dir), "portfolio.json")
    previous = pg_state.PORTFOLIO_G_STATE_DIR
    try:
        pg_state.PORTFOLIO_G_STATE_DIR = live_dir(state_dir)   # same redirection the paper pools use
        _ensure_book(target, round(float(allocated_inr) / float(usdinr), 4))
        result = run_pool_g_cycle(fetch_data_fn, fetch_prices_fn, api_key, now=now)
    finally:
        pg_state.PORTFOLIO_G_STATE_DIR = previous              # always restored, even on an exception

    orders = intended_orders(result)
    if dry_run:
        return {"status": "dry_run", "allocated_inr": allocated_inr, "engine": result,
                "orders": orders, "placed": []}

    if settings is None:
        from config import settings as settings           # noqa: PLC0415
    place = place_fn
    if place is None:
        from deployment.crypto_executor import place_crypto_order as place
    placed = []
    for order in orders:
        outcome = place(settings=settings, record=record, state_dir=state_dir,
                        symbol=order["symbol"], side=order["side"], quantity=order["quantity"],
                        reference_price_usdt=order["price"], strategy_key=POOL_G_KEY,
                        usdinr=usdinr, client=client, now=now)
        placed.append({**order, "placed": bool(outcome.placed), "order_id": outcome.order_id,
                       "fill_price": outcome.fill_price, "reasons": list(outcome.reasons)})
    return {"status": result.get("status"), "allocated_inr": allocated_inr, "engine": result,
            "orders": orders, "placed": placed}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run Pool G's live book.")
    ap.add_argument("--live", action="store_true",
                    help="actually place the orders (without this it reports and places nothing)")
    args = ap.parse_args()

    from config import settings
    from data.fetch_crypto import (CRYPTO_MAJORS, fetch_all_crypto_daily, fetch_crypto_last_prices,
                                   fetch_usdinr_rate)
    history = fetch_all_crypto_daily(CRYPTO_MAJORS, years=1.5)
    result = run_live(history, fetch_crypto_last_prices, settings.ANTHROPIC_API_KEY,
                      fetch_usdinr_rate(), dry_run=not args.live)
    if result["status"] == "refused":
        print(f"refused: {result['reason']}")
        return
    print(f"Pool G live book (Rs{result['allocated_inr']:,.0f} assigned): {result['status']}")
    for order in result["orders"]:
        print(f"    {order['side']:4s} {order['quantity']} {order['symbol']} "
              f"@ {order['price']} USDT ({order['why']})")
    for done in result.get("placed", []):
        state = f"placed {done['order_id']}" if done["placed"] else f"NOT placed: {' '.join(done['reasons'])}"
        print(f"    -> {done['symbol']}: {state}")
    if not result.get("placed"):
        print("    nothing was sent (use --live to place real orders)")


if __name__ == "__main__":
    main()
