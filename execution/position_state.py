"""
Persists what the bot believes it holds (data/known_positions.json) and
reconciles that against what Kite actually shows, so a position closed by a
GTT trigger (or by monitor_positions.py's own early exit) gets logged with
its realized P&L and a Telegram notification -- without anyone watching it
happen. This is the audit trail that used to only exist for entries
(paper_trades_log.csv); closed_trades_log.csv is its counterpart for exits.

Known limitation: exit fill price is looked up from Kite's /orders endpoint,
which only covers the current trading day. If a position closes and nobody
runs run_daily.py or monitor_positions.py again until a later day, the exact
fill price can't be recovered -- the closure is still logged and notified,
just with exit_price left as None and a note to check Kite directly. This is
a deliberate "don't guess" fallback (same philosophy as the rest of this
project), not a silent gap.
"""

import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

import requests

from reporting.telegram_notifier import send_telegram_message

KNOWN_POSITIONS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "known_positions.json")
CLOSED_TRADES_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "closed_trades_log.csv")


@dataclass
class KnownPosition:
    symbol: str
    quantity: int
    entry_price: float
    gtt_id: int | None
    opened_at: str


def load_known_positions(path: str = KNOWN_POSITIONS_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return {}  # an empty (0-byte) file is treated the same as a missing one
    raw = json.loads(content)
    return {symbol: KnownPosition(**fields) for symbol, fields in raw.items()}


def save_known_positions(positions: dict, path: str = KNOWN_POSITIONS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({symbol: asdict(p) for symbol, p in positions.items()}, f, indent=2)


def record_new_position(symbol: str, quantity: int, entry_price: float, gtt_id, path: str = KNOWN_POSITIONS_PATH):
    """Called right after a new BUY (and its GTT) are placed."""
    positions = load_known_positions(path)
    positions[symbol] = KnownPosition(
        symbol=symbol, quantity=quantity, entry_price=entry_price,
        gtt_id=gtt_id, opened_at=datetime.now().isoformat(),
    )
    save_known_positions(positions, path)


def _find_todays_exit_fill(api_key: str, access_token: str, symbol: str):
    """Best-effort lookup of today's completed SELL fill for this symbol from
    Kite's /orders (today-only order book). Returns the average fill price,
    or None if no matching completed SELL order is found today."""
    tradingsymbol = symbol.replace(".NS", "")
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    resp = requests.get("https://api.kite.trade/orders", headers=headers)
    result = resp.json()
    if resp.status_code != 200 or "data" not in result:
        return None

    matches = [
        o for o in result["data"]
        if o.get("tradingsymbol") == tradingsymbol
        and o.get("transaction_type") == "SELL"
        and o.get("status") == "COMPLETE"
    ]
    if not matches:
        return None
    matches.sort(key=lambda o: o.get("order_timestamp", ""))
    return float(matches[-1]["average_price"])


def _log_closed_trade(position: KnownPosition, exit_price, reason: str, path: str = CLOSED_TRADES_LOG_PATH):
    realized_pnl = (exit_price - position.entry_price) * position.quantity if exit_price is not None else None
    row = {
        "timestamp": datetime.now().isoformat(),
        "symbol": position.symbol,
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "exit_price": exit_price if exit_price is not None else "",
        "realized_pnl": f"{realized_pnl:.2f}" if realized_pnl is not None else "",
        "reason": reason,
    }
    file_exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    return realized_pnl


def _trading_days_between(start: date, end: date) -> int:
    """Weekdays strictly after `start`, up to and including `end`. An
    approximation that ignores exchange holidays -- close enough for a
    cooldown window, and errs on the side of a slightly longer wait."""
    if end <= start:
        return 0
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            days += 1
    return days


def symbols_in_cooldown(cooldown_trading_days: int, today: date = None,
                         path: str = CLOSED_TRADES_LOG_PATH) -> set:
    """
    Symbols that were closed AT A LOSS within the last `cooldown_trading_days`
    trading days -- the bot should not re-enter these yet. A stop-out is the
    market saying the setup failed; the same technical signal re-firing hours
    later on the same depressed price pattern is usually the same failed
    setup, not a new opportunity (seen live: PATANJALI stopped out at a loss
    in the morning run, then re-bought the same afternoon at a HIGHER price
    than the morning's entry, with a wider stop and a bigger position).

    Profit-target exits do NOT trigger a cooldown -- there's nothing wrong
    with a setup that worked. A closure whose exit price couldn't be
    recovered (realized_pnl blank in the log) is treated as a loss --
    conservative, same "don't guess" philosophy as the rest of this module.
    """
    if today is None:
        today = date.today()
    if not os.path.exists(path):
        return set()

    cooling = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pnl_text = (row.get("realized_pnl") or "").strip()
            if pnl_text and float(pnl_text) > 0:
                continue  # profitable exit -- no cooldown
            try:
                closed_on = datetime.fromisoformat(row["timestamp"]).date()
            except (KeyError, ValueError):
                continue
            if _trading_days_between(closed_on, today) < cooldown_trading_days:
                cooling.add(row["symbol"])
    return cooling


def reconcile_closed_positions(current_holdings: list, api_key: str, access_token: str,
                                bot_token: str, chat_id: str, path: str = KNOWN_POSITIONS_PATH,
                                reason: str = "GTT stop-loss/target triggered") -> list:
    """
    Diffs what's known (data/known_positions.json) against what Kite
    actually holds right now. Anything known but no longer held was closed
    -- by a GTT firing, or by monitor_positions.py's own early exit (pass
    reason="Exited early by monitor_positions.py" from there). Logs each to
    closed_trades_log.csv, removes it from known_positions.json, and sends
    one Telegram message per closure. Returns the list of symbols closed.
    """
    known = load_known_positions(path)
    held_symbols = {h.symbol for h in current_holdings}
    closed_symbols = [s for s in known if s not in held_symbols]

    for symbol in closed_symbols:
        position = known.pop(symbol)
        exit_price = _find_todays_exit_fill(api_key, access_token, symbol)
        realized_pnl = _log_closed_trade(position, exit_price, reason)

        if exit_price is not None:
            pnl_text = f"Rs.{realized_pnl:,.2f}"
        else:
            pnl_text = "unknown (check Kite -- fill happened on an earlier day)"

        send_telegram_message(
            f"*Position closed -- {symbol}*\n\n"
            f"Reason: {reason}\n"
            f"Quantity: {position.quantity}\n"
            f"Entry: Rs.{position.entry_price:.2f}\n"
            f"Exit: {'Rs.' + format(exit_price, ',.2f') if exit_price is not None else 'unknown'}\n"
            f"Realized P&L: {pnl_text}",
            bot_token, chat_id,
        )

    save_known_positions(known, path)
    return closed_symbols
