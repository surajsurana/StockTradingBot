"""
Manual sell from the dashboard (2026-09-16, per direction: "an option to
manually sell ... ask me no of shares and price market or manual").

apply_manual_exit() closes all or part of ONE open paper position in any
pool, at a price the caller has already resolved (the server turns
"market" into the latest cached quote before calling this), books the
P&L exactly the way that pool's own engine does, appends the trade to the
pool's trades.jsonl with exit_reason "manual", and appends an audit line
to deployment/state/manual_actions.jsonl. Pure over the filesystem --
unit-tested on a temp state tree.

Pool layouts (each pool's own state module is the reference):
  A  deployment/state/paper_trading/<book>/     paper_trading_engine layout, whole shares
  E  deployment/state/pool_e/<book>/            same layout, fractional coins (USDT)
  B  deployment/state/portfolio_b/              portfolio_b.state layout (direction, strategy_name)
  C  deployment/state/portfolio_c/              portfolio_c.state layout (same shape as B)
  D  deployment/state/pool_d/                   pool_d.state layout (one shared intraday book; the
                                                tick lock is honoured so a manual sell never races a tick)

A partial sell leaves the remainder of the position in place with its
stop untouched. Cash is credited the way the pool books an exit: A/B/C/E
credit quantity x exit price; Pool D releases the reserved entry value
plus the P&L (its shorts reserve cash too).
"""

import json
import os
from datetime import date, datetime
from typing import Optional

POOL_DIRS = {"A": "paper_trading", "B": "portfolio_b", "C": "portfolio_c", "D": "pool_d", "E": "pool_e"}
AUDIT_FILENAME = "manual_actions.jsonl"
POOL_D_LOCK_STALE_MINUTES = 10


class ManualExitError(ValueError):
    pass


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _book_dir(state_dir: str, pool: str, book_key: Optional[str]) -> str:
    if pool not in POOL_DIRS:
        raise ManualExitError(f"unknown pool {pool!r}")
    base = os.path.join(state_dir, POOL_DIRS[pool])
    if pool in ("A", "E"):
        if not book_key:
            raise ManualExitError("a Pool A / Pool E sell needs the book (strategy) key")
        base = os.path.join(base, book_key)
    if not os.path.exists(os.path.join(base, "portfolio.json")):
        raise ManualExitError(f"no book at {base}")
    return base


def _pnl(direction: str, entry_price: float, exit_price: float, quantity: float) -> float:
    if direction == "SELL":
        return (entry_price - exit_price) * quantity
    return (exit_price - entry_price) * quantity


def apply_manual_exit(state_dir: str, pool: str, book_key: Optional[str], symbol: str, quantity: float,
                      price: float, today: Optional[date] = None, now: Optional[datetime] = None,
                      price_mode: str = "manual") -> dict:
    """Closes `quantity` of `symbol` in the given pool/book at `price`.
    Returns the trade record written. Raises ManualExitError on anything
    that should be shown to the user (unknown position, bad quantity,
    Pool D mid-tick)."""
    today = today or date.today()
    now = now or datetime.now()
    if price is None or float(price) <= 0:
        raise ManualExitError("price must be a positive number")
    price = float(price)
    book_dir = _book_dir(state_dir, pool, book_key)
    pf_path = os.path.join(book_dir, "portfolio.json")
    pf = _read_json(pf_path)
    positions = pf.get("positions") or {}
    if symbol not in positions:
        raise ManualExitError(f"{symbol} is not an open position in Pool {pool}{' / ' + book_key if book_key else ''}")
    pos = positions[symbol]
    held = float(pos["quantity"])
    fractional = pool == "E"
    qty = float(quantity) if fractional else int(quantity)
    if qty <= 0 or qty > held + 1e-9:
        raise ManualExitError(f"quantity must be between {'0' if fractional else '1'} and {held:g} (held)")
    if abs(qty - held) < 1e-9:
        qty = held
    direction = pos.get("direction", "BUY")
    pnl = round(_pnl(direction, float(pos["entry_price"]), price, qty), 2)

    if pool == "D":
        lock = os.path.join(book_dir, "tick.lock")
        if os.path.exists(lock) and (now.timestamp() - os.path.getmtime(lock)) / 60 < POOL_D_LOCK_STALE_MINUTES:
            raise ManualExitError("Pool D is in the middle of a 5-minute tick -- try again in a minute")
        pf["cash"] = float(pf.get("cash", 0)) + float(pos["entry_price"]) * qty + pnl
        pf["realized_pnl_today"] = float(pf.get("realized_pnl_today", 0)) + pnl
        counts = pf.setdefault("trades_today_by_symbol", {})
        counts[symbol] = int(counts.get(symbol, 0)) + 1
        trade = {"symbol": symbol, "entry_price": pos["entry_price"], "exit_price": price, "quantity": qty,
                 "pnl": pnl, "reason": "manual", "direction": direction, "exit_date": today.isoformat(),
                 "entry_timestamp": pos.get("entry_timestamp"), "exit_timestamp": now.isoformat(timespec="minutes"),
                 "price_mode": price_mode}
    else:
        pf["cash"] = float(pf.get("cash", 0)) + price * qty
        trade = {"symbol": symbol, "entry_date": pos.get("entry_date"), "exit_date": today.isoformat(),
                 "entry_price": pos["entry_price"], "exit_price": price, "quantity": qty, "pnl": pnl,
                 "exit_reason": "manual", "direction": direction, "price_mode": price_mode}
        if pool in ("B", "C"):
            trade["strategy_name"] = pos.get("strategy_name", "")

    remaining = held - qty
    if remaining <= (1e-9 if fractional else 0):
        positions.pop(symbol)
        if pool in ("A", "B", "C", "E"):
            (pf.get("pending_exits") or {}).pop(symbol, None)
    else:
        pos["quantity"] = round(remaining, 6) if fractional else int(remaining)
    pf["positions"] = positions
    _write_json(pf_path, pf)
    _append_jsonl(os.path.join(book_dir, "trades.jsonl"), trade)
    _append_jsonl(os.path.join(state_dir, AUDIT_FILENAME), {
        "at": now.isoformat(timespec="seconds"), "pool": pool, "book": book_key, "symbol": symbol,
        "quantity": qty, "price": price, "price_mode": price_mode, "pnl": pnl,
        "remaining": pos["quantity"] if symbol in positions else 0,
    })
    return {**trade, "pool": pool, "book": book_key, "remaining": pos["quantity"] if symbol in positions else 0}


DEFAULT_STOP_PCT = {"A": 0.08, "B": 0.08, "C": 0.08, "E": 0.20}   # each pool's own protective-stop convention


def apply_manual_entry(state_dir: str, pool: str, book_key: Optional[str], symbol: str, quantity: float,
                       price: float, today: Optional[date] = None, now: Optional[datetime] = None,
                       price_mode: str = "manual") -> dict:
    """Opens (or adds to) a position by hand in Pools A, B, C or E, at
    `price`, paying from the book's cash. The position gets the pool's
    standard protective stop below the fill and is then managed by that
    pool's engine like any other (stops daily, the strategy's own exit
    rule). Pool D is refused: it is intraday and squares off by 15:25."""
    today = today or date.today()
    now = now or datetime.now()
    if pool == "D":
        raise ManualExitError("Pool D is intraday-only -- manual buys are not supported there")
    if price is None or float(price) <= 0:
        raise ManualExitError("price must be a positive number")
    price = float(price)
    book_dir = _book_dir(state_dir, pool, book_key)
    pf_path = os.path.join(book_dir, "portfolio.json")
    pf = _read_json(pf_path)
    fractional = pool == "E"
    qty = float(quantity) if fractional else int(quantity)
    if qty <= 0:
        raise ManualExitError("quantity must be positive")
    cost = price * qty
    cash = float(pf.get("cash", 0))
    if cost > cash + 1e-9:
        raise ManualExitError(f"not enough cash: {cost:,.2f} needed, {cash:,.2f} available")
    positions = pf.setdefault("positions", {})
    stop = round(price * (1 - DEFAULT_STOP_PCT[pool]), 4 if fractional else 2)
    if symbol in positions:   # add to an existing position: average the entry, keep the tighter stop
        pos = positions[symbol]
        old_qty, old_price = float(pos["quantity"]), float(pos["entry_price"])
        new_qty = old_qty + qty
        pos["entry_price"] = round((old_price * old_qty + price * qty) / new_qty, 4 if fractional else 2)
        pos["quantity"] = round(new_qty, 6) if fractional else int(new_qty)
        pos["stop_loss"] = max(float(pos.get("stop_loss", 0) or 0), stop)
    else:
        pos = {"entry_price": price, "entry_date": today.isoformat(), "quantity": qty, "stop_loss": stop,
               "manual": True}
        if pool in ("B", "C"):
            pos.update({"direction": "BUY", "target": None, "strategy_name": "manual", "confidence": 1.0})
        positions[symbol] = pos
    pf["cash"] = cash - cost
    (pf.get("pending_entries") or {}).pop(symbol, None)
    _write_json(pf_path, pf)
    _append_jsonl(os.path.join(state_dir, AUDIT_FILENAME), {
        "at": now.isoformat(timespec="seconds"), "action": "buy", "pool": pool, "book": book_key, "symbol": symbol,
        "quantity": qty, "price": price, "price_mode": price_mode, "cost": round(cost, 2),
    })
    return {"symbol": symbol, "pool": pool, "book": book_key, "quantity": qty, "entry_price": price, "cost": round(cost, 2),
            "stop_loss": positions[symbol]["stop_loss"], "position_quantity": positions[symbol]["quantity"],
            "cash_left": round(pf["cash"], 2)}
