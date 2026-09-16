"""
Portfolio G's own isolated state -- Pool G (2026-09-17, per direction:
"can we do a llm call crypto strategy as another pool?"), a live AI-
judgment book for crypto, the same category as Portfolio B/C for
equities: NO historical backtest (an LLM's judgment on past data cannot
be honestly walk-forward tested -- the model's own training already
contains what happened), so it is paper-traded from day one and judged
on its live record, not a research verdict.

Deliberately its own isolated tree (deployment/state/pool_g/), never
touching Pool A/E/F's files, mirroring portfolio_c/state.py's isolation
rule. One shared book across the five majors (BTC, ETH, BNB, XRP, SOL),
like Pool D's shared intraday book, since the LLM reasons about the
whole basket each run, not one coin's own isolated signal.

Kept in USDT with fractional quantities, like every Pool E book.
"""

import json
import os

from deployment.settings import STATE_DIR

PORTFOLIO_G_STATE_DIR = os.path.join(STATE_DIR, "pool_g")
PORTFOLIO_G_STARTING_CAPITAL_USDT = 1_000.0


def _portfolio_path() -> str:
    return os.path.join(PORTFOLIO_G_STATE_DIR, "portfolio.json")


def _trades_path() -> str:
    return os.path.join(PORTFOLIO_G_STATE_DIR, "trades.jsonl")


def _decision_log_path() -> str:
    return os.path.join(PORTFOLIO_G_STATE_DIR, "decision_log.jsonl")


def load_portfolio() -> dict:
    path = _portfolio_path()
    if not os.path.exists(path):
        return {
            "cash": PORTFOLIO_G_STARTING_CAPITAL_USDT,
            "starting_capital": PORTFOLIO_G_STARTING_CAPITAL_USDT,
            # symbol -> {entry_price, entry_date, entry_time, quantity, stop_loss, reasoning}
            "positions": {},
            "last_run_at": None,
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(portfolio: dict) -> None:
    path = _portfolio_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2)


def append_trade(trade: dict) -> None:
    """trade: symbol, entry_date, exit_date, entry_price, exit_price,
    quantity, pnl, exit_reason ("stop_loss" or "llm_sell"), reasoning."""
    path = _trades_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade) + "\n")


def append_decision_log(entry: dict) -> None:
    """One line per run: the full set of LLM decisions and the raw
    reasoning, kept even for symbols the run didn't act on -- the
    complete audit trail of what the model was told and what it said."""
    path = _decision_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_trades() -> list:
    path = _trades_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
