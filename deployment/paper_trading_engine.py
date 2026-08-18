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
from dataclasses import dataclass
from datetime import date as date_type, datetime
from typing import Callable, Optional

from reporting.format_utils import format_metric
from swing_research.backtesting_engine import Trade
from swing_research.base import OpenPosition, PositionUnit, Strategy
from swing_research.metrics import compute_metrics

from deployment.settings import (
    PAPER_TRADING_MIN_POSITION_VALUE_RUPEES,
    PAPER_TRADING_STATE_DIR,
    PAPER_TRADING_VIRTUAL_CAPITAL,
    REPORTS_DIR,
)


@dataclass
class ExecutionRealismConfig:
    """
    Optional, per-strategy execution-realism configuration for paper
    trading (added 2026-08-17, per explicit direction) -- OFF by default
    (fill_timing="same_day_close", no cost modeling), preserving
    BYTE-IDENTICAL behavior to every paper-trading run before this
    capability existed. Reuses
    swing_research/execution_realism_engine.py's cost-model formulas
    (compute_single_fill_cost(), itself built on
    compute_trailing_adv()/compute_trailing_illiq()) -- never a
    separate/duplicated cost calculation, so research (Amihud) and paper
    trading can never silently drift onto different cost assumptions for
    the same underlying formula.

    Deliberately NOT enabled for any strategy by default (SW-003, SW-008,
    SW-002, SW-007 all currently run with this at its all-off default) --
    "Execution assumptions should be strategy-appropriate and
    configurable," per explicit direction; turning this on for a specific
    strategy is a separate, deliberate decision, not a side effect of this
    capability existing.
    """
    fill_timing: str = "same_day_close"   # or "next_day_open"
    max_participation_pct_of_adv: Optional[float] = None
    illiq_cost_k: Optional[float] = None
    illiq_cost_cap_pct: float = 0.05
    brokerage_flat_rs: float = 0.0

    @property
    def enabled(self) -> bool:
        return (self.fill_timing != "same_day_close" or self.max_participation_pct_of_adv is not None
                or self.illiq_cost_k is not None or self.brokerage_flat_rs > 0)


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
            # pending_entries/pending_exits (added 2026-08-17): only ever
            # populated when a strategy's ExecutionRealismConfig.fill_timing
            # is "next_day_open" -- a signal detected on day T is recorded
            # here, then FILLED on day T+1 using day T+1's actual Open (see
            # run_daily()'s new resolve-pending step). Always empty for the
            # default "same_day_close" mode, so existing strategies' state
            # files gain these keys but they're always {} -- harmless.
            "pending_entries": {}, "pending_exits": {},
        }
    with open(path, "r", encoding="utf-8") as f:
        portfolio = json.load(f)
    # Backward-compatible for portfolio.json files saved before this field
    # existed (SW-003/SW-008's real, already-running state).
    portfolio.setdefault("pending_entries", {})
    portfolio.setdefault("pending_exits", {})
    return portfolio


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


def _get_open_price(df, target_date: date_type) -> Optional[float]:
    rows = df[df.index.date == target_date]
    if rows.empty:
        return None
    return float(rows.iloc[0]["Open"])


def _previous_mark_to_market_equity(strategy_key: str, before_date: date_type) -> Optional[float]:
    """The mark-to-market `equity` value (NOT the cash-basis figure
    _load_daily_equity() returns for compute_metrics()) most recently
    recorded strictly before `before_date` -- i.e. "yesterday's" equity,
    robust to weekends/holidays/gaps since it just takes the latest prior
    entry rather than literally calendar-yesterday. Returns None if no
    prior day has been recorded (first-ever run), so callers can
    distinguish "no prior day to compare against" from a genuine zero
    change."""
    path = _daily_equity_path(strategy_key)
    if not os.path.exists(path):
        return None
    latest_date, latest_equity = None, None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            d_date = date_type.fromisoformat(d["date"])
            if d_date < before_date and (latest_date is None or d_date > latest_date):
                latest_date, latest_equity = d_date, d["equity"]
    return latest_equity


def run_daily(strategy_key: str, strategy: Strategy,
              fetch_data_fn: Callable[[], dict],
              compute_extra_columns_fn: Optional[Callable[[dict], dict]] = None,
              as_of_date: Optional[date_type] = None, force: bool = False,
              execution_config: Optional[ExecutionRealismConfig] = None,
              min_position_value_rupees: float = PAPER_TRADING_MIN_POSITION_VALUE_RUPEES) -> dict:
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
    execution_config: OFF by default (ExecutionRealismConfig()'s own
    all-off defaults) -- see that dataclass's docstring. When
    fill_timing="next_day_open", a signal detected TODAY is queued in
    portfolio["pending_entries"/"pending_exits"] and actually filled
    TOMORROW at tomorrow's real Open (resolved at the TOP of the next
    call to run_daily(), before today's own signals are evaluated) --
    never same-day, so no lookahead is introduced. When cost/cap options
    are set, the fill price/quantity is adjusted via
    swing_research.execution_realism_engine.compute_single_fill_cost()
    (the SAME formula research's batch adjustment uses) at the moment of
    the ACTUAL fill (same-day close, or the resolved next-day open).
    min_position_value_rupees: 0 (disabled) by default -- see
    deployment/settings.py's PAPER_TRADING_MIN_POSITION_VALUE_RUPEES
    docstring. When > 0, a signal whose computed position value (entry
    price x quantity) falls below this is skipped, same soft-fail
    convention as an unaffordable/zero-quantity signal (recorded nowhere,
    simply not taken -- not an error).

    Returns a summary dict: {"status": "processed"|"skipped_already_processed",
    "as_of_date":..., "new_entries": [...], "new_exits": [...],
    "new_pending_entries": [...], "new_pending_exits": [...] (signals
    detected TODAY but not yet filled -- only ever populated under
    fill_timing="next_day_open"), "open_positions": N,
    "open_positions_detail": [...] (per-position current price/value/
    unrealized P&L, marked at today's close), "cash": ...,
    "mark_to_market_equity": ..., "daily_pnl": ... (today's equity minus
    the most recently recorded prior day's, or None on the first-ever
    recorded day)}.
    """
    execution_config = execution_config or ExecutionRealismConfig()
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
    pending_entries = portfolio.get("pending_entries", {})
    pending_exits = portfolio.get("pending_exits", {})
    new_entries, new_exits = [], []
    # Signals detected TODAY but not yet filled (only ever populated when
    # fill_timing=="next_day_open") -- distinct from new_entries/new_exits,
    # which are ACTUAL fills. Lets callers show "detected today, queued for
    # tomorrow's open" instead of the misleading "no signal today" a purely
    # fill-based view would otherwise show on the detection day.
    new_pending_entries, new_pending_exits = [], []
    # Per-open-position current mark-to-market snapshot (current price,
    # value, unrealized P&L) -- for reporting/Telegram, which should show
    # what a position is worth NOW, not just its entry price/stop-loss.
    open_positions_detail = []

    def _cost_adjusted(symbol: str, side: str, raw_price: float, quantity: int) -> tuple:
        """Applies execution_config's cap/cost (both no-ops at defaults) --
        returns (adjusted_price, capped_quantity)."""
        if execution_config.max_participation_pct_of_adv is None and execution_config.illiq_cost_k is None:
            return raw_price, quantity
        from swing_research.execution_realism_engine import compute_single_fill_cost
        result = compute_single_fill_cost(
            symbol, target_date, side, raw_price, quantity, data,
            max_participation_pct_of_adv=execution_config.max_participation_pct_of_adv,
            illiq_cost_k=execution_config.illiq_cost_k,
            illiq_cost_cap_pct=execution_config.illiq_cost_cap_pct,
        )
        return result["adjusted_price"], result["capped_quantity"]

    # --- STEP 0: resolve any fills queued YESTERDAY at TODAY's real Open
    # (only ever non-empty when fill_timing=="next_day_open" was active
    # when they were queued) -- must run BEFORE today's own signals are
    # evaluated, so a symbol's pending exit/entry is fully resolved
    # (removed from/added to `positions`) before the main loop below
    # re-examines it, and so no lookahead occurs (today's Open is now
    # actually known).
    for symbol in list(pending_exits.keys()):
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        open_price = _get_open_price(df.sort_index(), target_date)
        if open_price is None:
            continue   # no bar today (holiday) -- stays pending, resolved on the next real trading day
        pending = pending_exits.pop(symbol)
        pos_state = positions.pop(symbol, None)
        if pos_state is None:
            continue
        quantity = pos_state["quantity"]
        fill_price, _ = _cost_adjusted(symbol, "SELL", open_price, quantity)
        pnl = (fill_price - pos_state["entry_price"]) * quantity
        cash += fill_price * quantity - execution_config.brokerage_flat_rs
        entry_date = date_type.fromisoformat(pos_state["entry_date"])
        trade = Trade(symbol=symbol, entry_date=entry_date, exit_date=target_date,
                       entry_price=pos_state["entry_price"], exit_price=fill_price,
                       quantity=quantity, pnl=pnl, exit_reason=pending["exit_reason"], direction="BUY")
        _append_trade(strategy_key, trade)
        new_exits.append({"symbol": symbol, "exit_price": fill_price, "pnl": round(pnl, 2),
                           "reason": pending["exit_reason"], "fill_timing": "next_day_open",
                           "signal_date": pending["signal_date"]})

    for symbol in list(pending_entries.keys()):
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        open_price = _get_open_price(df.sort_index(), target_date)
        if open_price is None:
            continue
        pending = pending_entries.pop(symbol)
        stop_loss = pending["stop_loss"]
        risk_per_share = open_price - stop_loss
        if risk_per_share <= 0:
            continue   # the overnight gap moved price through its own planned stop -- signal abandoned, not taken
        quantity = math.floor(cash * strategy.risk_pct_per_unit / risk_per_share)
        fill_price, quantity = _cost_adjusted(symbol, "BUY", open_price, quantity)
        cost = fill_price * quantity
        if quantity < 1 or cost > cash or cost < min_position_value_rupees:
            continue
        cash -= cost + execution_config.brokerage_flat_rs
        positions[symbol] = {"entry_price": fill_price, "entry_date": target_date.isoformat(),
                              "quantity": quantity, "stop_loss": stop_loss}
        new_entries.append({"symbol": symbol, "entry_price": fill_price, "quantity": quantity,
                             "stop_loss": stop_loss, "fill_timing": "next_day_open",
                             "signal_date": pending["signal_date"]})
        # NOTE: no equity bookkeeping needed here -- this position now sits
        # in `positions`, and the MAIN LOOP below will visit this same
        # symbol (it's in `data`), find it `in positions`, and -- if it
        # doesn't ALSO exit same-day -- add it to open_positions_detail
        # itself. mark_to_market_equity is computed ONCE at the very end
        # from final cash + open_positions_detail, not accumulated
        # in-line (a prior incremental version of this double-counted
        # entries and silently dropped same-day exit proceeds -- fixed
        # 2026-08-18).

    # --- MAIN LOOP: evaluate today's own signals ---
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
                if execution_config.fill_timing == "next_day_open":
                    # Queue for tomorrow's Open -- NOT filled today, per
                    # explicit direction (no lookahead: today's decision,
                    # tomorrow's real, then-unknown price).
                    pending_exits[symbol] = {"exit_reason": exit_reason, "signal_date": target_date.isoformat()}
                    new_pending_exits.append({"symbol": symbol, "exit_reason": exit_reason,
                                               "signal_date": target_date.isoformat()})
                    current_price = float(row.Close)   # still marked at today's close until the exit actually fills
                    open_positions_detail.append({
                        "symbol": symbol, "quantity": quantity, "entry_price": pos_state["entry_price"],
                        "current_price": current_price, "current_value": round(current_price * quantity, 2),
                        "unrealized_pnl": round((current_price - pos_state["entry_price"]) * quantity, 2),
                        "unrealized_pnl_pct": round((current_price / pos_state["entry_price"] - 1) * 100, 2)
                        if pos_state["entry_price"] else None,
                    })
                else:
                    fill_price, _ = _cost_adjusted(symbol, "SELL", exit_price, quantity)
                    pnl = (fill_price - pos_state["entry_price"]) * quantity
                    cash += fill_price * quantity - execution_config.brokerage_flat_rs
                    trade = Trade(symbol=symbol, entry_date=entry_date, exit_date=target_date,
                                   entry_price=pos_state["entry_price"], exit_price=fill_price,
                                   quantity=quantity, pnl=pnl, exit_reason=exit_reason, direction="BUY")
                    _append_trade(strategy_key, trade)
                    new_exits.append({"symbol": symbol, "exit_price": fill_price, "pnl": round(pnl, 2),
                                       "reason": exit_reason})
                    del positions[symbol]
            else:
                current_price = float(row.Close)
                quantity = pos_state["quantity"]
                open_positions_detail.append({
                    "symbol": symbol, "quantity": quantity, "entry_price": pos_state["entry_price"],
                    "current_price": current_price, "current_value": round(current_price * quantity, 2),
                    "unrealized_pnl": round((current_price - pos_state["entry_price"]) * quantity, 2),
                    "unrealized_pnl_pct": round((current_price / pos_state["entry_price"] - 1) * 100, 2)
                    if pos_state["entry_price"] else None,
                })
        elif symbol not in pending_entries and symbol not in pending_exits:
            signal = strategy.entry_signal_at(row)
            if signal is not None:
                risk_per_share = signal.entry_price - signal.stop_loss
                if risk_per_share > 0:
                    if execution_config.fill_timing == "next_day_open":
                        pending_entries[symbol] = {"stop_loss": signal.stop_loss,
                                                    "signal_date": target_date.isoformat()}
                        new_pending_entries.append({"symbol": symbol, "stop_loss": signal.stop_loss,
                                                     "signal_date": target_date.isoformat()})
                    else:
                        quantity = math.floor(cash * strategy.risk_pct_per_unit / risk_per_share)
                        fill_price, quantity = _cost_adjusted(symbol, "BUY", signal.entry_price, quantity)
                        cost = fill_price * quantity
                        if quantity >= 1 and cost <= cash and cost >= min_position_value_rupees:
                            cash -= cost + execution_config.brokerage_flat_rs
                            positions[symbol] = {
                                "entry_price": fill_price, "entry_date": target_date.isoformat(),
                                "quantity": quantity, "stop_loss": signal.stop_loss,
                            }
                            new_entries.append({"symbol": symbol, "entry_price": fill_price,
                                                 "quantity": quantity, "stop_loss": signal.stop_loss})
                            # Filled at today's close, so fill_price IS
                            # today's mark -- this symbol won't be
                            # revisited again this call (each symbol is
                            # only ever iterated once), so it must be
                            # added to open_positions_detail explicitly
                            # here rather than relying on a later
                            # "held, no exit" pass.
                            open_positions_detail.append({
                                "symbol": symbol, "quantity": quantity, "entry_price": fill_price,
                                "current_price": fill_price, "current_value": round(cost, 2),
                                "unrealized_pnl": 0.0, "unrealized_pnl_pct": 0.0,
                            })

    portfolio["cash"] = cash
    portfolio["positions"] = positions
    portfolio["pending_entries"] = pending_entries
    portfolio["pending_exits"] = pending_exits
    portfolio["last_processed_date"] = target_date.isoformat()

    # Computed ONCE, here, from final cash + every currently-open
    # position's current value (open_positions_detail already reflects
    # every symbol still held at day-end, built incrementally above as
    # each was visited). Deliberately NOT accumulated incrementally
    # during the loop -- a prior incremental version double-counted the
    # cost of same-day/resolved entries (added on top of cash that
    # already included that money) and silently dropped same-day exit
    # proceeds entirely (never added anywhere), a real bug found
    # 2026-08-18 while adding daily_pnl below, which exposed it.
    mark_to_market_equity = round(cash + sum(d["current_value"] for d in open_positions_detail), 2)

    # Daily P&L = today's mark-to-market equity minus the most recently
    # recorded prior day's -- captures BOTH realized P&L (from any exits
    # that filled today) AND unrealized mark-to-market movement of
    # positions held open, which "sum of today's booked exits alone"
    # (the previous computation, done by the caller) silently missed,
    # showing 0 on every day with no exit even though open positions may
    # have moved. None on the very first recorded day (nothing prior to
    # compare against).
    previous_equity = _previous_mark_to_market_equity(strategy_key, target_date)
    daily_pnl = round(mark_to_market_equity - previous_equity, 2) if previous_equity is not None else None

    _save_portfolio(strategy_key, portfolio)
    _append_daily_equity(strategy_key, target_date, cash, mark_to_market_equity)

    return {
        "status": "processed", "as_of_date": target_date.isoformat(),
        "new_entries": new_entries, "new_exits": new_exits,
        "new_pending_entries": new_pending_entries, "new_pending_exits": new_pending_exits,
        "open_positions": len(positions), "open_positions_detail": open_positions_detail,
        "cash": round(cash, 2), "mark_to_market_equity": round(mark_to_market_equity, 2),
        "daily_pnl": daily_pnl,
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
        f"Cash: {format_metric(portfolio['cash'])}", "",
        "## Cumulative Metrics (since paper trading began)", "",
        f"- Total trades: {metrics['total_trades']}",
        f"- Win rate: {format_metric(metrics['win_rate'])}",
        f"- Expectancy per trade: {format_metric(metrics['expectancy'])}",
        f"- Profit factor: {format_metric(metrics['profit_factor'])}",
        f"- CAGR: {format_metric(metrics['cagr'])}%",
        f"- Sharpe: {format_metric(metrics['sharpe_ratio'])}",
        f"- Max drawdown: {format_metric(metrics['max_drawdown_pct'])}%",
        f"- Total P&L: {format_metric(metrics['total_pnl'])}", "",
        "## Open Positions", "",
    ]
    for symbol, pos in portfolio["positions"].items():
        lines.append(f"- {symbol}: entry {format_metric(pos['entry_price'])} x {pos['quantity']}, "
                     f"stop {format_metric(pos['stop_loss'])}")

    lines += ["", "## Recent Trades (last 10)", ""]
    for t in trades[-10:]:
        lines.append(f"- {t.symbol}: {t.entry_date} -> {t.exit_date}, pnl {format_metric(round(t.pnl, 2))} "
                     f"({t.exit_reason})")

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
