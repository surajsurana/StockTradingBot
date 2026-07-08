"""
Turns real closed trades (execution/position_state.py's closed_trades_log.csv)
into the same BacktestResult/ClosedTrade shape the backtester produces, so
Chief Investment AI's review_month() and report_generator.py's
build_monthly_review_text() -- which only ever read the aggregate
total_pnl/len(trades)/win_rate/max_drawdown, all computed properties on
BacktestResult -- can be reused as-is against real trading history instead
of a backtest stand-in.

closed_trades_log.csv doesn't track everything ClosedTrade has fields for
(strategy_name, stop_loss, target, a strict exit_reason enum) -- those get
inert placeholders below since nothing downstream reads them per-trade here,
only the aggregate stats matter for a monthly review.
"""

import csv
import os
from datetime import datetime

from backtest.backtester import BacktestResult, ClosedTrade

CLOSED_TRADES_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "closed_trades_log.csv")


def load_closed_trades_for_month(year: int, month: int, starting_capital: float,
                                  path: str = CLOSED_TRADES_LOG_PATH) -> BacktestResult:
    """
    Reads every closed trade whose timestamp falls in the given calendar
    month and has a known realized_pnl (rows logged with an unknown exit
    price -- see position_state.py's "closed on an earlier day" fallback --
    are skipped from the aggregate rather than guessed at).
    """
    trades = []

    if os.path.exists(path):
        with open(path, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp.year != year or timestamp.month != month:
                    continue
                if not row["realized_pnl"]:
                    continue  # exit price unknown -- can't count towards P&L

                entry_price = float(row["entry_price"])
                exit_price = float(row["exit_price"])
                date_str = timestamp.date().isoformat()

                trades.append(ClosedTrade(
                    symbol=row["symbol"],
                    strategy_name="",
                    entry_date=date_str,
                    exit_date=date_str,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_loss=entry_price,
                    target=exit_price,
                    quantity=int(row["quantity"]),
                    pnl=float(row["realized_pnl"]),
                    exit_reason=row["reason"],
                ))

    result = BacktestResult(trades=trades, starting_capital=starting_capital)
    result.ending_capital = starting_capital + result.total_pnl
    return result
