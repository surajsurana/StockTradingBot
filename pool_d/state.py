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

Capital structure (changed 2026-09-10, per explicit direction -- "i want
to keep total capital of 100000 only for all scrips and not for each
scrip seperately"): ONE shared book of settings.RESEARCH_LAB_VIRTUAL_CAPITAL
(Rs.1,00,000) across the whole liquid universe (run_experiment.py's
LIQUID_UNIVERSE, ~150 names), the same convention as Pool A/B/C. This
knowingly differs from research_lab/backtesting_engineer.py's own
per-symbol-capital convention that EXP-008 was judged on -- disclosed,
not hidden: Pool D is a framework test, and the shared book is what the
rest of the program reports against. Cash carries over from day to day
(a real book), so cumulative realised P&L = cash - starting_capital
once everything is squared off; only the per-day counters reset.

State shape (deployment/state/pool_d/portfolio.json):
{
  "last_processed_date": "2026-09-10",       # the trading day this state reflects
  "starting_capital": 100000.0,
  "cash": float,                              # carries over; entries reserve entry_price x qty, exits release it + P&L
  "context_by_symbol": {SYM: {...context dict from _compute_day_context()...}},
  "positions": {SYM: {entry_price, stop_loss, target, quantity, direction, entry_hour, entry_timestamp}},
  "trades_today_by_symbol": {SYM: int},       # research's per-symbol max_trades_per_day cap
  "realized_pnl_today": float,                # POOL-level daily loss limit (RiskParameters.daily_loss_limit_pct of capital)
}
A pre-2026-09-10 file (per-symbol cash_by_symbol) is migrated on load:
the shared book opens at starting_capital plus whatever the log of
closed trades had already booked, so history and cash stay consistent.
"""

import json
import os

from deployment.settings import STATE_DIR

POOL_D_STATE_DIR = os.path.join(STATE_DIR, "pool_d")
POOL_D_PORTFOLIO_PATH = os.path.join(POOL_D_STATE_DIR, "portfolio.json")
POOL_D_TRADES_PATH = os.path.join(POOL_D_STATE_DIR, "trades.jsonl")
POOL_D_LOCK_PATH = os.path.join(POOL_D_STATE_DIR, "tick.lock")


def new_state(starting_capital: float) -> dict:
    return {
        "last_processed_date": None, "starting_capital": float(starting_capital), "cash": float(starting_capital),
        "context_by_symbol": {}, "positions": {}, "trades_today_by_symbol": {}, "realized_pnl_today": 0.0,
    }


def read_trades() -> list:
    if not os.path.exists(POOL_D_TRADES_PATH):
        return []
    rows = []
    with open(POOL_D_TRADES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _migrate_per_symbol_state(old: dict, starting_capital: float) -> dict:
    booked = sum(float(t.get("pnl", 0) or 0) for t in read_trades())
    state = new_state(starting_capital)
    state["cash"] = float(starting_capital) + booked
    state["last_processed_date"] = old.get("last_processed_date")
    state["context_by_symbol"] = old.get("context_by_symbol") or {}
    state["positions"] = old.get("positions") or {}
    state["trades_today_by_symbol"] = old.get("trades_today_by_symbol") or {}
    state["realized_pnl_today"] = float(sum((old.get("realized_pnl_today_by_symbol") or {}).values()))
    print(f"Pool D state migrated to a single shared book: cash Rs.{state['cash']:,.2f} "
          f"(= Rs.{starting_capital:,.0f} + Rs.{booked:,.2f} already booked)")
    return state


def load_state(starting_capital: float) -> dict:
    if not os.path.exists(POOL_D_PORTFOLIO_PATH):
        return new_state(starting_capital)
    with open(POOL_D_PORTFOLIO_PATH, encoding="utf-8") as f:
        state = json.load(f)
    if "cash" not in state:
        state = _migrate_per_symbol_state(state, starting_capital)
    return state


def save_state(state: dict) -> None:
    os.makedirs(POOL_D_STATE_DIR, exist_ok=True)
    with open(POOL_D_PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def append_trade(trade: dict) -> None:
    os.makedirs(POOL_D_STATE_DIR, exist_ok=True)
    with open(POOL_D_TRADES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade) + "\n")
