"""
Portfolio B's own isolated state -- a third, separate directory tree
(deployment/state/portfolio_b/) from both deployment/state/paper_trading/
and deployment/state/portfolio_c/. This module never imports either of
those other two state modules, so a bug here can never read or write
Portfolio A's or Portfolio C's files, and vice versa. Structurally
identical to portfolio_c/state.py by design -- same schema, same file
layout -- just its own namespace.
"""

import json
import os
from datetime import date as date_type

from deployment.settings import STATE_DIR

PORTFOLIO_B_STATE_DIR = os.path.join(STATE_DIR, "portfolio_b")

# Same starting-capital convention as Portfolio C (portfolio_c/state.py's
# own PORTFOLIO_C_STARTING_CAPITAL) -- never drawn from or added to
# either Portfolio A's or Portfolio C's own pool. Kept as its own
# constant, not imported from either, so changing one can never silently
# change another.
PORTFOLIO_B_STARTING_CAPITAL = 100_000.0


def _portfolio_path() -> str:
    return os.path.join(PORTFOLIO_B_STATE_DIR, "portfolio.json")


def _trades_path() -> str:
    return os.path.join(PORTFOLIO_B_STATE_DIR, "trades.jsonl")


def _daily_equity_path() -> str:
    return os.path.join(PORTFOLIO_B_STATE_DIR, "daily_equity.jsonl")


def _decision_log_path() -> str:
    return os.path.join(PORTFOLIO_B_STATE_DIR, "decision_log.jsonl")


def _watchlist_path() -> str:
    return os.path.join(PORTFOLIO_B_STATE_DIR, "watchlist.json")


def _telegram_offset_path() -> str:
    return os.path.join(PORTFOLIO_B_STATE_DIR, "telegram_offset.json")


def load_portfolio() -> dict:
    path = _portfolio_path()
    if not os.path.exists(path):
        return {
            "cash": PORTFOLIO_B_STARTING_CAPITAL,
            "starting_capital": PORTFOLIO_B_STARTING_CAPITAL,
            "positions": {},        # symbol -> {direction, entry_price, entry_date, quantity,
                                     #            stop_loss, target, strategy_name, confidence}
            "pending_entries": {},  # symbol -> {direction, stop_loss, target, signal_date,
                                     #            signal_price, strategy_name, confidence, quantity}
            "pending_exits": {},    # symbol -> {exit_reason, signal_date}
            "last_processed_date": None,
        }
    with open(path, "r", encoding="utf-8") as f:
        portfolio = json.load(f)
    portfolio.setdefault("pending_entries", {})
    portfolio.setdefault("pending_exits", {})
    return portfolio


def save_portfolio(portfolio: dict) -> None:
    path = _portfolio_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2)


def append_trade(trade: dict) -> None:
    """trade: a plain dict (symbol, entry_date, exit_date, entry_price,
    exit_price, quantity, pnl, exit_reason, direction, strategy_name,
    confidence)."""
    path = _trades_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade) + "\n")


def append_daily_equity(as_of_date: date_type, cash: float, equity: float) -> None:
    path = _daily_equity_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": as_of_date.isoformat(), "cash": cash, "equity": equity}) + "\n")


def append_decision_log(entry: dict) -> None:
    """One entry per watchlist symbol per day -- same audit schema as
    Portfolio C's own decision_log.jsonl (see portfolio_c/daily.py)."""
    path = _decision_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_watchlist(default: list) -> list:
    """
    Reads the live watchlist from watchlist.json. `default` (see
    portfolio_b/engine.py's DEFAULT_WATCHLIST) is used ONLY to seed a
    brand-new file on first ever call -- once the file exists, it is the
    single source of truth, so /addstock and /removestock changes (see
    portfolio_b/telegram_bot.py) persist across every future call and
    every future cron run, never silently reverting to the code default.
    """
    path = _watchlist_path()
    if not os.path.exists(path):
        save_watchlist(default)
        return list(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(symbols: list) -> None:
    path = _watchlist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2)


def load_telegram_offset() -> int:
    """The Telegram update_id of the last message this bot has already
    processed -- getUpdates(offset=this+1) then only ever returns
    messages it hasn't seen yet, so restarting the poll (a fresh cron
    invocation every ~2 minutes, not a long-lived process) never
    reprocesses an old command. 0 (Telegram's own "no updates yet"
    starting value) if this bot has never polled before."""
    path = _telegram_offset_path()
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("last_update_id", 0)


def save_telegram_offset(last_update_id: int) -> None:
    path = _telegram_offset_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_update_id": last_update_id}, f)
