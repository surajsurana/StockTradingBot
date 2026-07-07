"""
Standalone live order test -- places ONE small real BUY order via Kite,
waits for it to fill, then places a matching SELL for the same quantity.

This is deliberately separate from run_daily.py and the whole agent
pipeline. Its only purpose is to confirm, in isolation, that real order
placement through your Kite account actually works end to end: the order
reaches the exchange, fills, and you can close it out again. Once this
passes, you can trust the same order-placement code path inside
run_daily.py's live mode -- execution/execution_engine.py's
_place_live_order() hits the identical Kite endpoint this script uses.

IMPORTANT, read before running:
- This spends REAL MONEY. It asks for an explicit typed confirmation
  ("CONFIRM") before placing the buy order, and pauses again before
  placing the sell order.
- Only works during NSE market hours (9:15 AM - 3:30 PM IST, Mon-Fri).
  Kite rejects regular equity orders placed outside market hours -- if
  you run this after hours, expect the BUY order to fail or be rejected,
  which is expected Kite behavior, not a bug in this script.
- Refresh your KITE_ACCESS_TOKEN first: python refresh_kite_token.py
  (it expires daily).
- Uses CNC (delivery) product -- the same product type the rest of this
  system trades with (swing positions, not intraday/MIS). Zerodha allows
  buying and selling the same CNC position on the same day; it's simply
  squared off before the shares ever settle into your demat account.
- Pick a cheap, liquid stock and a small quantity (start with 1) when
  prompted, so the real money at risk is small.
- Uses LIMIT orders priced through the current market (1% above the
  price you enter for BUY, 1% below for SELL) rather than plain MARKET
  orders -- Kite's API rejects plain MARKET orders unless "market
  protection" is configured on your account. This also avoids the
  /quote/ltp endpoint, which isn't available on Kite's free "Personal"
  API tier (only the paid Connect tier gets live quotes) -- so this
  script just asks you to type in the current price, which you can read
  straight off your Kite app/web dashboard.

Usage:
    python test_live_order.py
"""

import time

import requests

from config import settings

BASE_URL = "https://api.kite.trade"


def _headers() -> dict:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {settings.KITE_API_KEY}:{settings.KITE_ACCESS_TOKEN}",
    }


def prompt_for_price(label: str) -> float:
    """
    Asks the user to type in the current market price for the symbol
    (readable straight off the Kite app/web dashboard), rather than fetching
    it via Kite's /quote/ltp endpoint -- that endpoint returns a
    PermissionException on Kite's free "Personal" API tier (only the paid
    Connect tier includes live quote data), so this keeps the script fully
    usable on the free tier.
    """
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


def _round_to_tick(price: float, tick: float = 0.05) -> float:
    """NSE equity prices must be in multiples of 0.05."""
    return round(round(price / tick) * tick, 2)


def place_order(symbol: str, quantity: int, transaction_type: str, limit_price: float) -> str:
    """
    Places a real regular equity LIMIT order (CNC). Buys are priced slightly
    above LTP and sells slightly below, so the order is "marketable" (fills
    immediately against the best available price) without using Kite's
    unsupported plain MARKET-via-API order type.
    """
    payload = {
        "exchange": "NSE",
        "tradingsymbol": symbol,
        "transaction_type": transaction_type,
        "quantity": quantity,
        "order_type": "LIMIT",
        "price": limit_price,
        "product": "CNC",
        "validity": "DAY",
    }
    resp = requests.post(f"{BASE_URL}/orders/regular", headers=_headers(), data=payload)
    result = resp.json()
    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(f"Order placement failed (status {resp.status_code}): {result}")
    return result["data"]["order_id"]


def get_order_status(order_id: str) -> dict:
    """
    Fetches today's orders and returns the latest known state for order_id.
    Kite's order placement is asynchronous -- placing an order only returns
    an order_id, not confirmation of a fill. Status must be polled separately.
    """
    resp = requests.get(f"{BASE_URL}/orders", headers=_headers())
    result = resp.json()
    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(f"Could not fetch order status (status {resp.status_code}): {result}")

    matches = [o for o in result["data"] if o.get("order_id") == order_id]
    if not matches:
        return {"status": "UNKNOWN"}
    return matches[-1]  # most recent update for this order


def wait_for_fill(order_id: str, timeout_seconds: int = 30, poll_every: int = 2) -> dict:
    """Polls order status until it's COMPLETE, REJECTED, or CANCELLED, or times out."""
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
    return order  # timed out -- return whatever the last known state was


def main():
    print("=" * 60)
    print("LIVE ORDER TEST -- this places a REAL order with REAL money.")
    print("=" * 60)

    if not settings.KITE_ACCESS_TOKEN:
        print("\nKITE_ACCESS_TOKEN is empty. Run 'python refresh_kite_token.py' first, then re-run this.")
        return

    symbol = input("\nEnter the NSE trading symbol to test with (e.g. IDEA, YESBANK) -- "
                    "pick something cheap: ").strip().upper()
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
    buy_limit_price = _round_to_tick(ltp * 1.01)  # 1% above LTP so it fills immediately

    print(f"About to place a REAL BUY order: {quantity} share(s) of {symbol}, "
          f"product CNC, LIMIT @ Rs.{buy_limit_price:.2f} (1% above LTP so it fills like a market order).")
    confirm = input("Type CONFIRM to proceed, anything else to cancel: ").strip()
    if confirm != "CONFIRM":
        print("Cancelled -- no order placed.")
        return

    print("\nPlacing BUY order...")
    try:
        buy_order_id = place_order(symbol, quantity, "BUY", buy_limit_price)
    except RuntimeError as e:
        print(f"BUY order failed: {e}")
        return
    print(f"BUY order placed. order_id: {buy_order_id}")

    print("Waiting for BUY to fill...")
    buy_result = wait_for_fill(buy_order_id)
    print(f"BUY final status: {buy_result.get('status')}")

    if buy_result.get("status") != "COMPLETE":
        print("\nBUY did not complete -- stopping here. Check your Kite app for details. "
              "No SELL order will be placed.")
        return

    buy_price = buy_result.get("average_price")
    print(f"BUY filled at avg price: {buy_price}")

    input("\nPress Enter to place the matching SELL order (or Ctrl+C to stop here and check manually)...")

    current_ltp = prompt_for_price(f"Current market price of {symbol}")
    sell_limit_price = _round_to_tick(current_ltp * 0.99)  # 1% below LTP so it fills immediately
    print(f"Placing SELL LIMIT @ Rs.{sell_limit_price:.2f}")

    print("\nPlacing SELL order...")
    try:
        sell_order_id = place_order(symbol, quantity, "SELL", sell_limit_price)
    except RuntimeError as e:
        print(f"SELL order failed: {e}. You still hold the shares from the BUY -- check your Kite app.")
        return
    print(f"SELL order placed. order_id: {sell_order_id}")

    print("Waiting for SELL to fill...")
    sell_result = wait_for_fill(sell_order_id)
    print(f"SELL final status: {sell_result.get('status')}")

    if sell_result.get("status") != "COMPLETE":
        print("\nSELL did not complete -- check your Kite app, you may still hold the shares.")
        return

    sell_price = sell_result.get("average_price")
    print(f"SELL filled at avg price: {sell_price}")

    if buy_price and sell_price:
        pnl = (float(sell_price) - float(buy_price)) * quantity
        print(f"\nRound-trip P&L: Rs.{pnl:,.2f} (bought at {buy_price}, sold at {sell_price}, qty {quantity})")

    print("\n" + "=" * 60)
    print("LIVE ORDER TEST COMPLETE -- if both orders show COMPLETE above, order "
          "placement and execution are confirmed working end to end.")
    print("=" * 60)


if __name__ == "__main__":
    main()
