"""
Paper Trading Engine (Phase 2) -- generates live signals once per trading
day, using the EXACT SAME strategy code and execution assumptions as the
frozen swing_research/ backtests, and tracks a fully virtual portfolio.
NEVER sends a broker order, NEVER touches execution/ or cio/.

EXECUTION ASSUMPTIONS (same convention as the backtests, per explicit
direction -- documented here, not silently assumed):
  - Signals are generated using a trading day's OWN close (this function
    is meant to be run AFTER market close, using that day's final EOD
    bar -- consistent with swing_research.base.Strategy.entry_signal_at()'s
    own convention of returning entry_price = that day's Close).
  - Fills are simulated at the SAME price the signal used (today's Close)
    -- no slippage, no partial fills, no transaction costs modeled, same
    as every swing_research backtest to date.
  - Protective-stop hits are checked against the day's Low (for a long
    position) BEFORE checking the strategy's own signal-based exit --
    same "stop-loss takes priority" convention as
    swing_research.backtesting_engine.simulate_portfolio()'s day-loop
    (see that module's own _hit_stop() logic, not imported here to keep
    this engine self-contained per the isolation mandate, but the same
    documented rule).
  - Position sizing: quantity = floor(current REALIZED equity x
    risk_pct_per_unit / risk_per_share), same formula documented in
    swing_research/base.py's Strategy.risk_pct_per_unit docstring.

IDEMPOTENCY: every symbol's portfolio state file records last_processed_date.
Calling run_daily() again for a date <= last_processed_date is a safe no-op
(returns immediately with a clear message) unless force=True -- safe to
run manually multiple times, or from a scheduled task without duplicate-
run protection of its own (see this module's docstring for how to
schedule it -- OS-level scheduling is NOT built by this program, per
explicit direction; only the idempotent runner itself).

ISOLATION: imports only swing_research/ (read-only, for the Strategy
interface, Trade dataclass, and metrics) and data/fetch_historical.py
(read-only, the same real-data source the backtests use). Never imports
execution/, cio/, strategies/, risk/, portfolio/, or config/settings.py.
"""

import json
import math
import os
import time
from datetime import date as date_type, datetime
from typing import Callable, Optional

from swing_research.backtesting_engine import Trade
from swing_research.base import OpenPosition, PositionUnit, Strategy
from swing_research.metrics import compute_metrics

from deployment.settings import PAPER_TRADING_STATE_DIR, PAPER_TRADING_VIRTUAL_CAPITAL, REPORTS_DIR


def _portfolio_path(strategy_key: str) -> str:
    return os.path.join(PAPER_TRADING_STATE_DIR, strategy_key, "portfolio.json")


def _trades_path(strategy_key: str) -> str:
    return os.path.join(PAPER_TRADING_STATE_DIR, strategy_key, "trades.jsonl")


def _daily_equity_path(strategy_key: str) -> str:
    return os.path.join(PAPER_TRADING_STATE_DIR, strategy_key, "daily_equity.jsonl")


def _load_portfolio(strategy_key: str) -> dict:
    path = _portfolio_path(strategy_key)
    if not os.path.exists(path):
        return {
            "cash": PAPER_TRADING_VIRTUAL_CAPITAL, "starting_capital": PAPER_TRADING_VIRTUAL_CAPITAL,
            "positions": {}, "last_processed_date": None,
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_portfolio(strategy_key: str, portfolio: dict) -> None:
    path = _portfolio_path(strategy_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2)


def _append_trade(strategy_key: str, trade: Trade) -> None:
    path = _trades_path(strategy_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "symbol": trade.symbol, "entry_date": trade.entry_date.isoformat(),
            "exit_date": trade.exit_date.isoformat(), "entry_price": trade.entry_price,
            "exit_price": trade.exit_price, "quantity": trade.quantity, "pnl": trade.pnl,
            "exit_reason": trade.exit_reason, "direction": trade.direction,
        }) + "\n")


def _load_trades(strategy_key: str) -> list:
    path = _trades_path(strategy_key)
    if not os.path.exists(path):
        return []
    trades = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            trades.append(Trade(
                symbol=d["symbol"], entry_date=date_type.fromisoformat(d["entry_date"]),
                exit_date=date_type.fromisoformat(d["exit_date"]), entry_price=d["entry_price"],
                exit_price=d["exit_price"], quantity=d["quantity"], pnl=d["pnl"],
                exit_reason=d["exit_reason"], direction=d.get("direction", "BUY"),
            ))
    return trades


def _append_daily_equity(strategy_key: str, as_of: date_type, cash: float, mark_to_market_equity: float) -> None:
    path = _daily_equity_path(strategy_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "date": as_of.isoformat(), "cash": cash, "equity": mark_to_market_equity,
        }) + "\n")


def _load_daily_equity(strategy_key: str) -> dict:
    """Returns {date: realized_cash_equity} -- for compute_metrics(), which
    expects a REALIZED (cash-basis) equity curve, same convention
    swing_research.backtesting_engine.simulate_portfolio() uses. The
    mark-to-market `equity` field is tracked separately (see
    _append_daily_equity()) for reporting/drift purposes, but the metrics
    computation intentionally uses cash, for consistency with the
    backtests' own equity definition."""
    path = _daily_equity_path(strategy_key)
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            result[date_type.fromisoformat(d["date"])] = d["cash"]
    return result


def run_daily(strategy_key: str, strategy: Strategy,
              fetch_data_fn: Callable[[], dict],
              compute_extra_columns_fn: Optional[Callable[[dict], dict]] = None,
              as_of_date: Optional[date_type] = None, force: bool = False) -> dict:
    """
    The idempotent daily runner. Call this once per trading day, after
    market close, for a given strategy already registered in the
    deployment registry (deployment_manager.register_strategy()).

    strategy_key: matches the deployment registry key AND the state
    directory this strategy's virtual portfolio lives under.
    strategy: an instance of the SAME swing_research.base.Strategy
    subclass used in that strategy's research backtests (e.g.
    FiftyTwoWeekHighMomentumStrategy()) -- reused unmodified, never a
    separate/parallel implementation, so paper-trading signals are
    guaranteed consistent with the validated research rules.
    fetch_data_fn: zero-arg callable returning {symbol: DataFrame} for the
    frozen universe, fresh as of today -- caller's responsibility (keeps
    this engine decoupled from any one data source/period choice).
    compute_extra_columns_fn: optional, for strategies needing a
    cross-sectional column (e.g. 52-Week High Momentum's nearness
    percentile) -- same {symbol: DataFrame} -> {symbol: Series} shape as
    swing_research.cross_sectional's functions.
    as_of_date: defaults to today; pass explicitly for a specific date
    (e.g. testing, or catching up after a missed run).

    Returns a summary dict: {"status": "processed"|"skipped_already_processed",
    "as_of_date":..., "new_entries": [...], "new_exits": [...],
    "open_positions": N, "cash": ..., "equity": ...}.
    """
    portfolio = _load_portfolio(strategy_key)
    target_date = as_of_date or date_type.today()

    last_processed = portfolio.get("last_processed_date")
    if not force and last_processed is not None and date_type.fromisoformat(last_processed) >= target_date:
        return {"status": "skipped_already_processed", "as_of_date": target_date.isoformat(),
                "last_processed_date": last_processed}

    data = fetch_data_fn()
    extra_columns = compute_extra_columns_fn(data) if compute_extra_columns_fn else None

    cash = portfolio["cash"]
    positions = portfolio["positions"]   # {symbol: {entry_price, entry_date, quantity, stop_loss}}
    new_entries, new_exits = [], []
    mark_to_market_equity = cash

    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        df = df.sort_index()
        if extra_columns and symbol in extra_columns:
            df = df.join(extra_columns[symbol])
        precomputed = strategy.precompute(df)
        rows_on_date = precomputed[precomputed.index.date == target_date]
        if rows_on_date.empty:
            continue   # no bar for this symbol on target_date (holiday, delisting, etc.)
        row = list(rows_on_date.itertuples(index=False))[0]

        if symbol in positions:
            pos_state = positions[symbol]
            entry_date = date_type.fromisoformat(pos_state["entry_date"])
            open_position = OpenPosition(
                symbol=symbol, direction="BUY",
                units=[PositionUnit(entry_price=pos_state["entry_price"], entry_date=entry_date,
                                     quantity=pos_state["quantity"])],
                stop_loss=pos_state["stop_loss"],
            )
            exit_price = None
            exit_reason = None
            if float(row.Low) <= pos_state["stop_loss"]:
                exit_price = pos_state["stop_loss"]
                exit_reason = "stop_loss"
            else:
                signal_exit = strategy.exit_signal_at(row, open_position)
                if signal_exit is not None:
                    exit_price = float(signal_exit)
                    exit_reason = "signal_exit"

            if exit_price is not None:
                quantity = pos_state["quantity"]
                pnl = (exit_price - pos_state["entry_price"]) * quantity
                cash += exit_price * quantity
                trade = Trade(symbol=symbol, entry_date=entry_date, exit_date=target_date,
                               entry_price=pos_state["entry_price"], exit_price=exit_price,
                               quantity=quantity, pnl=pnl, exit_reason=exit_reason, direction="BUY")
                _append_trade(strategy_key, trade)
                new_exits.append({"symbol": symbol, "exit_price": exit_price, "pnl": round(pnl, 2),
                                   "reason": exit_reason})
                del positions[symbol]
            else:
                mark_to_market_equity += float(row.Close) * pos_state["quantity"]
        else:
            signal = strategy.entry_signal_at(row)
            if signal is not None:
                risk_per_share = signal.entry_price - signal.stop_loss
                if risk_per_share > 0:
                    quantity = math.floor(cash * strategy.risk_pct_per_unit / risk_per_share)
                    cost = signal.entry_price * quantity
                    if quantity >= 1 and cost <= cash:
                        cash -= cost
                        positions[symbol] = {
                            "entry_price": signal.entry_price, "entry_date": target_date.isoformat(),
                            "quantity": quantity, "stop_loss": signal.stop_loss,
                        }
                        new_entries.append({"symbol": symbol, "entry_price": signal.entry_price,
                                             "quantity": quantity, "stop_loss": signal.stop_loss})
                        mark_to_market_equity += cost

    portfolio["cash"] = cash
    portfolio["positions"] = positions
    portfolio["last_processed_date"] = target_date.isoformat()
    _save_portfolio(strategy_key, portfolio)
    _append_daily_equity(strategy_key, target_date, cash, mark_to_market_equity)

    return {
        "status": "processed", "as_of_date": target_date.isoformat(),
        "new_entries": new_entries, "new_exits": new_exits,
        "open_positions": len(positions), "cash": round(cash, 2),
        "mark_to_market_equity": round(mark_to_market_equity, 2),
    }


def load_portfolio(strategy_key: str) -> dict:
    """Public read accessor for a strategy's current virtual portfolio
    state -- for reporting/aggregation modules (strategy_library_view.py,
    drift_report.py) that need it without duplicating the state-file
    format."""
    return _load_portfolio(strategy_key)


def compute_live_metrics(strategy_key: str) -> dict:
    """Metrics computed from the accumulated virtual trade/equity history
    so far, using the SAME compute_metrics() function the research
    backtests use -- for direct comparability against the historical
    research numbers (see deployment/drift_report.py)."""
    trades = _load_trades(strategy_key)
    daily_equity = _load_daily_equity(strategy_key)
    portfolio = _load_portfolio(strategy_key)
    trading_calendar = sorted(daily_equity.keys())
    return compute_metrics(trades, portfolio["starting_capital"], trading_calendar, daily_equity=daily_equity)


def generate_report(strategy_key: str, display_name: str, strategy_id: str = "") -> str:
    """
    Generates a daily/weekly/monthly-summarized markdown report from the
    accumulated live paper-trading history so far, saved under
    deployment/reports/<strategy_key>/. Deterministic, no Claude call --
    a structured numeric summary, not a narrative (kept simple and
    dependency-free for a report meant to be regenerated every single
    trading day). strategy_id: the strategy's permanent id, shown at the
    top so this report is traceable back to the same identity used in its
    Telegram messages.
    """
    metrics = compute_live_metrics(strategy_key)
    portfolio = _load_portfolio(strategy_key)
    trades = _load_trades(strategy_key)
    today = date_type.today()

    lines = [
        f"# Paper Trading Report -- {display_name}" + (f" ({strategy_id})" if strategy_id else ""), "",
        f"Generated: {datetime.now().isoformat()}",
        f"Last processed trading day: {portfolio.get('last_processed_date')}",
        f"Open positions: {len(portfolio['positions'])}",
        f"Cash: {portfolio['cash']:.2f}", "",
        "## Cumulative Metrics (since paper trading began)", "",
        f"- Total trades: {metrics['total_trades']}",
        f"- Win rate: {metrics['win_rate']}",
        f"- Expectancy per trade: {metrics['expectancy']}",
        f"- Profit factor: {metrics['profit_factor']}",
        f"- CAGR: {metrics['cagr']}%",
        f"- Sharpe: {metrics['sharpe_ratio']}",
        f"- Max drawdown: {metrics['max_drawdown_pct']}%",
        f"- Total P&L: {metrics['total_pnl']}", "",
        "## Open Positions", "",
    ]
    for symbol, pos in portfolio["positions"].items():
        lines.append(f"- {symbol}: entry {pos['entry_price']} x {pos['quantity']}, stop {pos['stop_loss']}")

    lines += ["", "## Recent Trades (last 10)", ""]
    for t in trades[-10:]:
        lines.append(f"- {t.symbol}: {t.entry_date} -> {t.exit_date}, pnl {round(t.pnl, 2)} ({t.exit_reason})")

    report_text = "\n".join(lines)

    report_dir = os.path.join(REPORTS_DIR, strategy_key)
    os.makedirs(report_dir, exist_ok=True)
    daily_path = os.path.join(report_dir, f"{today.isoformat()}.md")
    with open(daily_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    latest_path = os.path.join(report_dir, "LATEST.md")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    return daily_path
