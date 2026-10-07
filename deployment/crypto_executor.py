"""
Places real crypto orders at CoinDCX. The crypto counterpart of deployment/live_executor.py.

SAME SHAPE, SAME GUARANTEES, DIFFERENT VENUE. Every property live_executor.py establishes holds here
and for the same reasons:

  - the guard is called by this function itself, so there is no path to the exchange that skips it;
  - the attempt is logged and fsynced BEFORE the order is sent, because a crash between sending and
    recording leaves a real position this program has no memory of;
  - nothing raises -- a broker error, a network failure or a malformed response each return a
    structured refusal, since an exception escaping mid-loop would silently skip the rest of the run.

They share one order log, so the daily cap counts every real order across both venues rather than
allowing twenty per broker, and so reconciliation has a single file to read.

CURRENCIES, which is the whole subtlety here. Pool G decides in USDT -- it must, because its snapshot
divides today's price by 300 days of Binance USDT closes, and feeding it rupee prices would turn
every percentage into nonsense. But the money is rupees at an Indian exchange, and the caps are in
rupees. So a decision arrives as a QUANTITY IN COIN UNITS (currency-agnostic) plus its USDT reference
price, and this module prices that quantity in rupees at CoinDCX's own INR market to check the caps
and to place the order.

The gap between the rupee price CoinDCX quotes and the USDT reference times the exchange rate is the
India premium, measured at about +2.2% to +3.0% on 2026-10-06. It is recorded on every order rather
than hidden, because when the live book later diverges from the paper book that figure is how you
tell premium drift from slippage.
"""

import os
from datetime import datetime
from typing import Optional

from deployment.live_executor import (OrderOutcome, _append_log, _log_quietly, order_log_path,
                                      orders_placed_today)

BROKER = "coindcx"
INR_MARKET_SUFFIX = "INR"


def market_for(symbol: str, quote: str = INR_MARKET_SUFFIX) -> str:
    """CoinDCX names a market by concatenation: BTC + INR -> BTCINR. The coin symbol is taken as the
    program already spells it (BTC, ETH, SOL, XRP, BNB); nothing is translated or guessed."""
    return f"{str(symbol).strip().upper()}{quote.upper()}"


def place_crypto_order(*, settings, record, state_dir: str, symbol: str, side: str,
                       quantity: float, reference_price_usdt: float, strategy_key: str,
                       usdinr: float, eligibility=None, current_live_exposure_rupees: float = 0.0,
                       client=None, now: Optional[datetime] = None,
                       limit_buffer_pct: float = 0.004) -> OrderOutcome:
    """
    Places ONE real crypto order, or explains why it did not.

    `quantity` is in coin units, as Pool G decided it. `reference_price_usdt` is the price that
    decision was made at. `client` is injectable so tests never construct a real one.

    The order is a LIMIT priced a little through the market, never a plain market order: CoinDCX's
    INR books are thinner than the global ones, and a market order in a thin book can fill a long way
    from the price the decision assumed. `limit_buffer_pct` is how far through to price it.
    """
    from deployment.live_guard import check_order_allowed

    now = now or datetime.now()
    market = market_for(symbol)
    today = now.date().isoformat()

    def refuse(reasons, stage="refused", extra=None):
        row = {"at": now.isoformat(), "stage": stage, "broker": BROKER, "strategy": strategy_key,
               "symbol": symbol, "market": market, "side": side, "quantity": quantity,
               "reasons": list(reasons)}
        row.update(extra or {})
        try:
            _append_log(state_dir, row)
        except OSError:
            pass                      # a refusal that cannot be logged is still a refusal
        return OrderOutcome(placed=False, reasons=list(reasons))

    if client is None:
        try:
            from deployment.credential_store import credential
            from execution.coindcx_client import CoinDCXClient
            config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
            client = CoinDCXClient(credential("COINDCX_API_KEY", config_dir, settings),
                                   credential("COINDCX_API_SECRET", config_dir, settings))
        except Exception as e:
            return refuse([f"Could not build a CoinDCX client: {type(e).__name__}: {e}"])

    # The rupee price this will actually trade at. Read from the exchange, never derived from the
    # USDT reference: the whole point is that the two differ, and the caps must be checked against
    # the money that will really leave the account.
    try:
        inr_price = client.last_price(market)
    except Exception as e:
        return refuse([f"Could not read the {market} price: {type(e).__name__}: {e}"])
    if not inr_price or inr_price <= 0:
        return refuse([f"CoinDCX quoted no usable price for {market}; refusing to size an order blind."])

    order_value = float(quantity or 0) * float(inr_price)
    fair_inr = float(reference_price_usdt or 0) * float(usdinr or 0)
    premium_pct = round((inr_price / fair_inr - 1) * 100, 3) if fair_inr else None

    # ROUND TO THE EXCHANGE'S OWN RULES. CoinDCX rejects an order outright for a price with too many
    # decimals ("INR precision should be 1"), which is exactly how the first real order this program
    # ever sent was turned away. The rules are asked for, never assumed: a market the exchange does
    # not list is one we must not send to, not one with no limits.
    try:
        rules = client.rules_for(market)
    except Exception as e:
        return refuse([f"Could not read {market}'s trading rules: {type(e).__name__}: {e}"])
    if not rules:
        return refuse([f"CoinDCX does not list {market}, so no order was sent."])

    through = 1 + limit_buffer_pct if str(side).upper() == "BUY" else 1 - limit_buffer_pct
    limit_price = round(float(inr_price) * through, rules["price_decimals"])
    quantity = round(float(quantity), rules["quantity_decimals"])
    order_value = quantity * float(inr_price)

    # The exchange's own floors. Below either of these the order is rejected, so refusing here keeps
    # the reason readable instead of surfacing a broker error for something we could see coming.
    if quantity < rules["min_quantity"]:
        return refuse([f"{quantity:g} {symbol} is below {market}'s minimum of "
                       f"{rules['min_quantity']:g}."], extra={"value": round(order_value, 2)})
    if rules["min_notional"] and order_value < rules["min_notional"]:
        return refuse([f"Rs{order_value:,.0f} is below {market}'s minimum order of "
                       f"Rs{rules['min_notional']:,.0f}."], extra={"value": round(order_value, 2)})

    decision = check_order_allowed(
        settings=settings, record=record, state_dir=state_dir, order_value_rupees=order_value,
        current_live_exposure_rupees=current_live_exposure_rupees,
        orders_placed_today=orders_placed_today(state_dir, today),
        eligibility=eligibility, broker=BROKER)
    if not decision.allowed:
        return refuse(decision.reasons, extra={"value": round(order_value, 2)})

    sent = {"at": now.isoformat(), "stage": "sent", "broker": BROKER, "strategy": strategy_key,
            "symbol": symbol, "market": market, "side": side, "quantity": quantity,
            "value": round(order_value, 2), "inr_price": inr_price, "limit_price": limit_price,
            "reference_price_usdt": reference_price_usdt, "usdinr": usdinr,
            "india_premium_pct": premium_pct, "overrides": list(decision.overrides)}
    try:
        _append_log(state_dir, sent)
    except OSError as e:
        return OrderOutcome(placed=False,
                            reasons=[f"Could not write the order log, so no order was sent ({e})."])

    try:
        raw = client.place_order(market=market, side=str(side).lower(), quantity=float(quantity),
                                 price=limit_price)
    except Exception as e:
        reasons = [f"Order failed: {type(e).__name__}: {e}"]
        _log_quietly(state_dir, now, "failed", strategy_key, symbol, side, quantity, reasons)
        return OrderOutcome(placed=False, reasons=reasons)

    body = raw if isinstance(raw, dict) else {}
    order_id = str(body.get("id") or body.get("order_id") or "")
    status = str(body.get("status", "")).lower()
    if status in ("rejected", "cancelled", "error"):
        reasons = [f"CoinDCX did not accept the order: {body.get('message') or status or raw!r}"]
        _log_quietly(state_dir, now, "rejected", strategy_key, symbol, side, quantity, reasons)
        return OrderOutcome(placed=False, reasons=reasons, raw=body)

    try:
        fill = float(body.get("avg_price") or body.get("price_per_unit") or limit_price)
    except (TypeError, ValueError):
        fill = None                 # an unparseable price does not undo an order that was accepted

    reasons = [] if order_id else ["CoinDCX accepted the order but returned no order id."]
    _log_quietly(state_dir, now, "accepted", strategy_key, symbol, side, quantity, reasons,
                 order_id=order_id, fill_price=fill)
    return OrderOutcome(placed=True, reasons=reasons, order_id=order_id, fill_price=fill, raw=body)
