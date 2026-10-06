"""
Places real orders -- Gate H, step 2 of 2. The only module in the deployment package
that can move money.

IT OWNS NO ORDER-PLACEMENT CODE OF ITS OWN. The actual Kite call is
execution/execution_engine.py's ExecutionEngine, which already handles the things that
are easy to get wrong and were learned from real trades: CNC product type for delivery,
a LIMIT order priced through the market because Kite rejects plain MARKET orders
without market protection configured, tick-size rounding, and reading back the ACTUAL
average fill price afterwards (a LIMIT buy can and did fill better than its limit).
Rewriting any of that here would mean relearning it with real money.

WHAT THIS ADDS is the part that was missing: a gate, and a record.

  - Every order goes through deployment/live_guard.py first. The guard fails closed on
    everything -- LIVE_TRADING off, kill switch present, strategy not PILOT_LIVE,
    pilot gates no longer met, caps exceeded, credentials absent.

  - Every ATTEMPT is logged before the order is sent, not after. A crash between
    sending and recording would otherwise leave an order at the broker that this
    program has no memory of -- the worst possible state, because reconciliation would
    see a position it never placed and could close it as foreign.

  - Nothing raises. A broker error, a network failure, a malformed response: each
    returns a structured refusal. An exception escaping mid-loop would skip the
    remaining orders silently and leave the log inconsistent with reality.

THE GUARD IS NOT OPTIONAL AND NOT BYPASSABLE: place_live_order() calls it itself rather
than trusting the caller to have done so, so there is no code path to a broker that
skips it.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

ORDER_LOG_FILENAME = "live_orders.jsonl"


@dataclass
class OrderOutcome:
    placed: bool
    reasons: list = field(default_factory=list)
    order_id: str = ""
    fill_price: Optional[float] = None
    raw: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.placed


def order_log_path(state_dir: str) -> str:
    return os.path.join(state_dir, ORDER_LOG_FILENAME)


def _append_log(state_dir: str, row: dict) -> None:
    """Append-only, one JSON object per line, flushed and fsynced before returning.

    fsync because this is the record of real money moving: an order sent and then lost to a buffer
    on a power cut is exactly the state that leaves a position nobody knows about. A logging failure
    is raised to the caller rather than swallowed -- if we cannot record an order we must not send
    one."""
    os.makedirs(state_dir, exist_ok=True)
    with open(order_log_path(state_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def orders_placed_today(state_dir: str, today: Optional[str] = None) -> int:
    """How many live orders were SENT today -- counted from the log, the only record that survives a
    restart. Counts attempts that reached the broker, since the daily cap exists to contain a
    runaway signal generator and a rejected order still represents one request made."""
    today = today or datetime.now().date().isoformat()
    try:
        with open(order_log_path(state_dir), encoding="utf-8") as f:
            return sum(1 for line in f
                       if line.strip() and json.loads(line).get("stage") == "sent"
                       and str(json.loads(line).get("at", "")).startswith(today))
    except (OSError, ValueError):
        return 0      # an unreadable log is handled by the guard's own caps, not by guessing


def place_live_order(*, settings, record, state_dir: str, symbol: str, side: str,
                     quantity: int, reference_price: float, strategy_key: str,
                     eligibility=None, current_live_exposure_rupees: float = 0.0,
                     engine=None, now: Optional[datetime] = None) -> OrderOutcome:
    """
    Places ONE real order, or explains why it did not.

    `reference_price` is the price the strategy's decision was based on; the execution engine prices
    its LIMIT through the market from it. `engine` is injectable so tests never construct a real one.
    """
    from deployment.live_guard import check_order_allowed

    now = now or datetime.now()
    order_value = float(quantity or 0) * float(reference_price or 0)

    decision = check_order_allowed(
        settings=settings, record=record, state_dir=state_dir,
        order_value_rupees=order_value,
        current_live_exposure_rupees=current_live_exposure_rupees,
        orders_placed_today=orders_placed_today(state_dir, now.date().isoformat()),
        eligibility=eligibility,
    )
    if not decision.allowed:
        try:
            _append_log(state_dir, {"at": now.isoformat(), "stage": "refused", "strategy": strategy_key,
                                    "symbol": symbol, "side": side, "quantity": quantity,
                                    "value": round(order_value, 2), "reasons": decision.reasons})
        except OSError:
            pass          # a refusal that cannot be logged is still a refusal; nothing was sent
        return OrderOutcome(placed=False, reasons=list(decision.reasons))

    # Recorded BEFORE sending: a crash between here and the broker leaves a row saying an order was
    # attempted, which reconciliation can investigate. The reverse order would leave a real position
    # with no record of it at all.
    try:
        _append_log(state_dir, {"at": now.isoformat(), "stage": "sent", "strategy": strategy_key,
                                "symbol": symbol, "side": side, "quantity": quantity,
                                "value": round(order_value, 2), "reference_price": reference_price})
    except OSError as e:
        return OrderOutcome(placed=False,
                            reasons=[f"Could not write the order log, so no order was sent ({e})."])

    try:
        if engine is None:
            from execution.execution_engine import ExecutionEngine
            engine = ExecutionEngine(live_trading=True, api_key=settings.KITE_API_KEY,
                                     access_token=settings.KITE_ACCESS_TOKEN,
                                     limit_order_buffer_pct=getattr(settings, "LIMIT_ORDER_BUFFER_PCT", 0.015))
        result = engine.place_order(_approved_trade(symbol, side, quantity, reference_price, strategy_key))
    except Exception as e:                    # never let a broker problem escape into the runner
        outcome = OrderOutcome(placed=False, reasons=[f"Order failed: {type(e).__name__}: {e}"])
        _log_quietly(state_dir, now, "failed", strategy_key, symbol, side, quantity, outcome.reasons)
        return outcome

    # A response that is not a dict at all is treated as a rejection, not an exception. The engine
    # should always return one, but "should" is not a guarantee worth risking here: an AttributeError
    # escaping this function would skip every remaining order in the run.
    body = result if isinstance(result, dict) else {}
    ok = str(body.get("status", "")).lower() == "success"
    order_id = str((body.get("data") or {}).get("order_id") or "")
    fill = body.get("price")
    if not ok:
        reasons = [f"Broker did not accept the order: {body.get('message') or result!r}"]
        _log_quietly(state_dir, now, "rejected", strategy_key, symbol, side, quantity, reasons)
        return OrderOutcome(placed=False, reasons=reasons, raw=body)

    try:
        fill_price = None if fill is None else float(fill)
    except (TypeError, ValueError):
        fill_price = None        # an unparseable price does not undo an order that was accepted

    # Accepted but with no order id is a real and awkward state: the money moved, yet there is no
    # handle to reconcile it by. It stays placed=True -- claiming otherwise would invite the caller to
    # retry and double the position -- and carries a reason so reconciliation knows to look for it.
    reasons = [] if order_id else ["Broker accepted the order but returned no order id."]
    _log_quietly(state_dir, now, "accepted", strategy_key, symbol, side, quantity,
                 reasons, order_id=order_id, fill_price=fill_price)
    return OrderOutcome(placed=True, reasons=reasons, order_id=order_id,
                        fill_price=fill_price, raw=body)


def _log_quietly(state_dir, now, stage, strategy_key, symbol, side, quantity, reasons,
                 order_id: str = "", fill_price=None) -> None:
    """A logging failure here must not turn an order that WAS sent into an exception the caller
    reads as 'not sent'. The outcome is already decided; this is the record of it."""
    try:
        _append_log(state_dir, {"at": now.isoformat(), "stage": stage, "strategy": strategy_key,
                                "symbol": symbol, "side": side, "quantity": quantity,
                                "reasons": reasons, "order_id": order_id, "fill_price": fill_price})
    except OSError:
        pass


def _approved_trade(symbol: str, side: str, quantity: int, reference_price: float, strategy_key: str):
    from risk.risk_manager import ApprovedTrade
    from strategies.base import Signal
    signal = Signal(symbol=symbol, direction="BUY" if str(side).upper() == "BUY" else "SELL",
                    entry_price=float(reference_price), stop_loss=0.0, target=0.0,
                    confidence=1.0, strategy_name=strategy_key,
                    reason="live order from the live runner")
    return ApprovedTrade(signal=signal, quantity=int(quantity),
                         capital_deployed=float(quantity) * float(reference_price))
