"""
Portfolio C's own isolated state -- deliberately a separate directory tree
(deployment/state/portfolio_c/) from deployment/state/paper_trading/, per
the design review's isolation rule: "no code path could ever merge the
two." This module never imports deployment/paper_trading_engine.py's
_load_portfolio/_save_portfolio -- it has its own, so a bug here can never
read or write Portfolio A's files, and vice versa.

Portfolio C has exactly ONE portfolio (not one per anchor strategy, unlike
Portfolio A) -- a single isolated capital pool that both anchor
strategies' candidates compete for via Portfolio Manager's confidence
ranking, same as how Research Analyst/Portfolio Manager already work
across multiple technical signals for one account in the original agent
design.
"""

import json
import os
from datetime import date as date_type

from deployment.settings import STATE_DIR

PORTFOLIO_C_STATE_DIR = os.path.join(STATE_DIR, "portfolio_c")

# Fixed, explicit starting capital -- never drawn from or added to
# Portfolio A's pool (see swing_research/candidate_ranking.py /
# deployment/capital_winddown.py for Portfolio A's own, entirely separate
# capital figures). Matches this platform's own established "target
# active capital" convention (deployment/settings.py's
# PAPER_TRADING_WINDDOWN_TARGET_CAPITAL) rather than inventing a new
# number -- kept as Portfolio C's own constant, not imported from that
# setting, so changing Portfolio A's wind-down target can never silently
# change Portfolio C's starting capital.
PORTFOLIO_C_STARTING_CAPITAL = 100_000.0


def _portfolio_path() -> str:
    return os.path.join(PORTFOLIO_C_STATE_DIR, "portfolio.json")


def _trades_path() -> str:
    return os.path.join(PORTFOLIO_C_STATE_DIR, "trades.jsonl")


def _daily_equity_path() -> str:
    return os.path.join(PORTFOLIO_C_STATE_DIR, "daily_equity.jsonl")


def _decision_log_path() -> str:
    return os.path.join(PORTFOLIO_C_STATE_DIR, "decision_log.jsonl")


def load_portfolio() -> dict:
    path = _portfolio_path()
    if not os.path.exists(path):
        return {
            "cash": PORTFOLIO_C_STARTING_CAPITAL,
            "starting_capital": PORTFOLIO_C_STARTING_CAPITAL,
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
    confidence) -- kept as a dict rather than swing_research.backtesting_engine.Trade
    so this module has zero import coupling to swing_research/."""
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
    """One entry per candidate per day -- see portfolio_c/decision_log.py's
    build_decision_log_entry() for the full audit schema this expects."""
    path = _decision_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
