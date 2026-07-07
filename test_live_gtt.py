"""
Standalone live GTT test -- places ONE small real BUY order (with its
matching GTT stop-loss/target attached, exactly like execution_engine.py
does for every live trade) via Kite, confirms the GTT actually shows up on
your account, then cleans up: cancels the GTT and sells the shares back.

This exercises the *exact* production code path (execution/execution_engine.py's
ExecutionEngine.place_order -> _place_gtt_exit), not a reimplementation --
if this script works, run_daily.py's live GTT placement works too. This is
deliberately separate from run_daily.py's full agent pipeline, the same way
test_live_order.py was kept separate: its only purpose is to validate order
placement (this time, order + GTT) against your real account in isolation.

IMPORTANT, read before running:
- This spends REAL MONEY (a tiny amount if you pick a cheap stock and
  quantity 1). It asks for a typed "CONFIRM" before placing the BUY+GTT, and
  again before the cleanup SELL.
- Only works during NSE market hours (9:15 AM - 3:30 PM IST, Mon-Fri).
- Run this yourself in your own terminal (python test_live_gtt.py) --
  the typed confirmations are a deliberate safety gate, not something to
  automate away.
- Refreshes your Kite session automatically (auth.kite_auto_login) if
  today's token has gone stale -- no need to run refresh_kite_token.py first.
- Uses CNC (delivery), 1% above/below the price you type in for BUY/SELL
  limit pricing -- same reasoning as test_live_order.py (no live-quote
  access on this account's tier).
- Stop-loss/target for the GTT are set tight (0.5% away from your entry) on
  purpose, purely to prove the GTT gets accepted and appears on your
  account -- not a realistic trading range. The cleanup step cancels it
  regardless of whether it would have triggered.

Usage:
    python test_live_gtt.py
"""

import time

import requests

from config import settings
from auth.kite_auto_login import ensure_fresh_kite_session
from strategies.base import Signal
from risk.risk_manager import ApprovedTrade
from execution.execution_engine import ExecutionEngine, _round_to_tick

BASE_URL = "https://api.kite.trade"


def _headers() -> dict:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {settings.KITE_API_KEY}:{settings.KITE_ACCESS_TOKEN}",
    }


def prompt_for_price(label: str) -> float:
    while True:
        raw = input(f"{label} (check your Kite app for the current price): ").strip()
        try:
            price = float(raw)
            if price <= 0:
                print("Price must be positive, try again.")
                continue
            return price
        except ValueError:
            print("Not a valid number, try again.")


def get_order_status(order_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/orders", headers=_headers())
    result = resp.json()
    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(f"Could not fetch order status (status {resp.status_code}): {result}")
    matches = [o for o in result["data"] if o.get("order_id") == order_id]
    return matches[-1] if matches else {"status": "UNKNOWN"}


def wait_for_fill(order_id: str, timeout_seconds: int = 30, poll_every: int = 2) -> dict:
    waited = 0
    order = {"status": "UNKNOWN"}
    while waited < timeout_seconds:
        order = get_order_status(order_id)
        status = order.get("status", "UNKNOWN")
        print(f"    ... order {order_id} status: {status}")
        if status in ("COMPLETE", "REJECTED", "CANCELLED"):
            return order
        time.sleep(poll_every)
        waited += poll_every
    return order


def fetch_open_gtts() -> list:
    resp = requests.get(f"{BASE_URL}/gtt/triggers", headers=_headers())
    result = resp.json()
    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(f"Could not fetch GTTs (status {resp.status_code}): {result}")
    return result["data"]


def main():
    print("=" * 60)
    print("LIVE GTT TEST -- this places a REAL order + REAL GTT with real money.")
    print("=" * 60)

    if not ensure_fresh_kite_session(settings):
        print("\nCould not establish a valid Kite session. Fix that first "
              "(check KITE_USER_ID/KITE_PASSWORD/KITE_TOTP_SECRET, or run refresh_kite_token.py).")
        return

    symbol = input("\nEnter the NSE trading symbol to test with (e.g. GOLDBEES) -- "
                    "pick something cheap and liquid: ").strip().upper()
    if not symbol:
        print("No symbol entered, aborting.")
        return

    quantity_input = input("Quantity to buy (start with 1): ").strip() or "1"
    try:
        quantity = int(quantity_input)
    except ValueError:
        print("Invalid quantity, aborting.")
        return

    ltp = prompt_for_price(f"\nCurrent market price of {symbol}")
    stop_loss = _round_to_tick(ltp * 0.995)   # 0.5% below -- tight on purpose, just to validate the GTT
    target = _round_to_tick(ltp * 1.005)      # 0.5% above

    print(f"\nAbout to place a REAL BUY: {quantity} share(s) of {symbol}.NS, CNC, "
          f"~Rs.{ltp:.2f}, with a GTT stop-loss {stop_loss} / target {target} attached.")
    confirm = input("Type CONFIRM to proceed, anything else to cancel: ").strip()
    if confirm != "CONFIRM":
        print("Cancelled -- nothing placed.")
        return

    engine = ExecutionEngine(live_trading=True, api_key=settings.KITE_API_KEY,
                              access_token=settings.KITE_ACCESS_TOKEN,
                              limit_order_buffer_pct=settings.LIMIT_ORDER_BUFFER_PCT)
    buy_signal = Signal(symbol=f"{symbol}.NS", direction="BUY", entry_price=ltp,
                         stop_loss=stop_loss, target=target, confidence=1.0,
                         strategy_name="test_live_gtt", reason="Manual live GTT validation")
    trade = ApprovedTrade(signal=buy_signal, quantity=quantity, capital_deployed=quantity * ltp)

    print("\nPlacing BUY (+ GTT)...")
    result = engine.place_order(trade)
    print(f"Result: {result}")

    order_id = result.get("data", {}).get("order_id") if isinstance(result.get("data"), dict) else None
    if not order_id:
        print("\nBUY order does not look like it was accepted -- stopping here. Check your Kite app.")
        return

    print("Waiting for BUY to fill...")
    buy_result = wait_for_fill(order_id)
    if buy_result.get("status") != "COMPLETE":
        print(f"\nBUY final status: {buy_result.get('status')} -- did not complete, stopping here.")
        return
    print(f"BUY filled at avg price: {buy_result.get('average_price')}")

    gtt_id = result.get("gtt_id")
    if gtt_id is None:
        print("\nWARNING: BUY filled but no gtt_id came back -- the GTT placement likely failed. "
              "Check the [GTT PLACED] / WARNING line printed above, and check your Kite app's GTT tab.")
    else:
        print(f"\nGTT placed, trigger_id={gtt_id}. Confirming it shows up in your account's GTT list...")
        open_gtts = fetch_open_gtts()
        found = any(g.get("id") == gtt_id for g in open_gtts)
        print(f"Found in /gtt/triggers: {found} ({len(open_gtts)} total open GTT(s) on your account)")

    input("\nPress Enter to clean up (cancel the GTT and sell the shares back), "
          "or Ctrl+C to stop here and handle it manually in the Kite app...")

    if gtt_id is not None:
        print("Cancelling GTT...")
        engine.cancel_gtt(gtt_id)

    current_ltp = prompt_for_price(f"Current market price of {symbol}, for the cleanup SELL")
    confirm = input(f"Type CONFIRM to sell {quantity} share(s) of {symbol} back: ").strip()
    if confirm != "CONFIRM":
        print("Cleanup SELL cancelled -- you still hold the shares (and the GTT, if it wasn't cancelled above). "
              "Check your Kite app.")
        return

    sell_signal = Signal(symbol=f"{symbol}.NS", direction="SELL", entry_price=current_ltp,
                          stop_loss=current_ltp, target=current_ltp, confidence=1.0,
                          strategy_name="test_live_gtt", reason="Manual live GTT test cleanup")
    sell_trade = ApprovedTrade(signal=sell_signal, quantity=quantity, capital_deployed=quantity * current_ltp)
    sell_result = engine.place_order(sell_trade)
    print(f"SELL result: {sell_result}")

    print("\n" + "=" * 60)
    print("LIVE GTT TEST COMPLETE -- check your Kite app to confirm the position and GTT are both gone.")
    print("=" * 60)


if __name__ == "__main__":
    main()
