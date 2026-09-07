"""
Pool D's persisted state -- the FIRST paper-trading pool in this program
that runs INTRADAY (checked every 5 minutes during market hours, not
once daily after close). Built 2026-09-07, per explicit direction, to
paper-trade research_lab's VWAP Extension Exhaustion Fade strategy
(EXP-008, REJECT) -- deliberately, KNOWINGLY running a strategy that
failed its own rigorous backtest, purely to keep the intraday pipeline
itself exercised end-to-end while further intraday research continues
separately. Never conflate this with Pool A/B/C: those only ever run
strategies with a genuine PASS (or, for the two informally-backtested
ones, a real positive-edge result) behind them.

Capital structure: matches research_lab/backtesting_engineer.py's own
run_backtest() convention EXACTLY -- each symbol gets its OWN
independent capital_per_symbol (settings.RESEARCH_LAB_VIRTUAL_CAPITAL,
Rs.1,00,000), not one shared pool the way Pool A/B/C work. This is
deliberate, not an oversight: a shared pool would change realized
position sizing (competing symbols could starve each other of capital
in a way the backtest never modeled), which is exactly the kind of
"playing with the rules" this program has explicitly ruled out this
session. With 20 symbols, Pool D's total notional paper capital is
Rs.20,00,000 (20 x Rs.1,00,000), not Rs.1,00,000 -- expect P&L reporting
that looks different in scale from every other pool, by design.

State shape (deployment/state/pool_d/portfolio.json):
{
  "last_processed_date": "2026-09-07",       # the trading day this state reflects
  "context_by_symbol": {SYM: {...context dict from _compute_day_context()...}},
  "cash_by_symbol": {SYM: float},              # starts at RESEARCH_LAB_VIRTUAL_CAPITAL each new day
  "positions": {SYM: {entry_price, stop_loss, target, quantity, direction, entry_hour}},
  "trades_today_by_symbol": {SYM: int},
  "realized_pnl_today_by_symbol": {SYM: float},
}
"""

import json
import os

from deployment.settings import STATE_DIR

POOL_D_STATE_DIR = os.path.join(STATE_DIR, "pool_d")
POOL_D_PORTFOLIO_PATH = os.path.join(POOL_D_STATE_DIR, "portfolio.json")
POOL_D_TRADES_PATH = os.path.join(POOL_D_STATE_DIR, "trades.jsonl")


def load_state() -> dict:
    if not os.path.exists(POOL_D_PORTFOLIO_PATH):
        return {
            "last_processed_date": None, "context_by_symbol": {}, "cash_by_symbol": {},
            "positions": {}, "trades_today_by_symbol": {}, "realized_pnl_today_by_symbol": {},
        }
    with open(POOL_D_PORTFOLIO_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    os.makedirs(POOL_D_STATE_DIR, exist_ok=True)
    with open(POOL_D_PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def append_trade(trade: dict) -> None:
    os.makedirs(POOL_D_STATE_DIR, exist_ok=True)
    with open(POOL_D_TRADES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade) + "\n")
