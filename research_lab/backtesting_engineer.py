"""
Backtesting Engineer -- generalizes the ad hoc intraday backtest built and
run many times earlier this session (same mandatory-EOD-square-off
mechanics, already proven against real Kite intraday data) into a reusable
component with a full metrics suite, instead of just win rate/R:R/P&L.

Position sizing/leverage assumptions live in risk_manager_research.py, not
here -- this module takes a risk_per_trade_pct as a plain input and
doesn't itself decide what a sensible value is.

Data sources (both already proven this session): data/fetch_kite_intraday.py
(intraday bars, via the paid Kite Connect market-data app) and
data/fetch_historical.py (daily bars, yfinance -- used only if a
hypothesis needs a daily-bar filter, same pattern as the earlier ORB work's
trend filter).

Simplification stated explicitly rather than hidden: CAGR/Sharpe/Sortino
below use a FIXED starting-capital denominator for daily returns (not a
fully compounding equity curve) -- the same simplification this project's
existing swing backtests already make (BacktestResult tracks
ending_capital = starting_capital + total_pnl). Good enough to compare
hypotheses against each other; not a claim of precise real-world
compounding.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from research_lab.base import Strategy
from research_lab.risk_manager_research import RiskParameters, should_block_new_trade


@dataclass
class Trade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    exit_reason: str
    entry_hour: float = None   # 24h float (e.g. 9.25 = 9:15am) -- for Performance Analyst's
                               # time-of-day breakdown; None if the caller didn't provide one
    direction: str = "BUY"     # "BUY" (long) or "SELL" (short) -- see _is_long() etc. below


def _is_long(direction: str) -> bool:
    return direction == "BUY"


def _risk_per_share(entry_price: float, stop_loss: float, direction: str) -> float:
    """Always positive for a valid stop, regardless of direction -- a long's
    stop sits below entry (risk = entry - stop); a short's stop sits above
    entry (risk = stop - entry). Zero or negative means an invalid stop for
    that direction (e.g. a "BUY" stop placed above entry)."""
    return (entry_price - stop_loss) if _is_long(direction) else (stop_loss - entry_price)


def _check_exit(direction: str, bar_low: float, bar_high: float, stop_loss: float, target: float) -> tuple:
    """Returns (hit_stop, hit_target) for one bar. Long: stop is below
    (breached by a low), target is above (reached by a high). Short: stop
    is above (breached by a high), target is below (reached by a low) --
    the exact mirror image."""
    if _is_long(direction):
        return bar_low <= stop_loss, bar_high >= target
    return bar_high >= stop_loss, bar_low <= target


def _trade_pnl(direction: str, entry_price: float, exit_price: float, quantity: int) -> float:
    """Long profits as price rises; short profits as price falls -- same
    magnitude of move, opposite sign of exposure."""
    if _is_long(direction):
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def _compute_day_context(df: pd.DataFrame, trade_date: date, lookback_days: int = 20) -> dict:
    """
    Multi-day context for the day about to be simulated, computed ONLY
    from data strictly BEFORE trade_date -- no lookahead. df here is the
    symbol's full multi-day intraday history (not one day's slice).

    prior_close: previous trading day's last Close, or None if there's no
    earlier data (e.g. the very first day in the fetched window).
    avg_first_15min_volume_20d: average of the first-15-minutes' total
    Volume (first 3 bars at 5-min candles) over up to the last
    `lookback_days` trading days before trade_date, or None if fewer than
    5 prior days are available (too small a sample to call "average").
    """
    prior_bars = df[df.index.date < trade_date]
    if prior_bars.empty:
        return {"prior_close": None, "avg_first_15min_volume_20d": None}

    prior_close = float(prior_bars.iloc[-1]["Close"])

    prior_bars = prior_bars.copy()
    prior_bars["trade_date"] = prior_bars.index.date
    prior_days = sorted(prior_bars["trade_date"].unique())[-lookback_days:]
    if len(prior_days) < 5:
        return {"prior_close": prior_close, "avg_first_15min_volume_20d": None}

    first_15min_volumes = []
    for d in prior_days:
        day_bars = prior_bars[prior_bars["trade_date"] == d]
        if len(day_bars) >= 3:
            first_15min_volumes.append(float(day_bars.iloc[:3]["Volume"].sum()))
    avg_volume = sum(first_15min_volumes) / len(first_15min_volumes) if first_15min_volumes else None

    return {"prior_close": prior_close, "avg_first_15min_volume_20d": avg_volume}


def simulate_symbol(df: pd.DataFrame, strategy: Strategy, capital: float,
                     risk_per_trade_pct: float, risk_params: Optional[RiskParameters] = None,
                     symbol: str = "UNKNOWN") -> list:
    """
    Bar-by-bar, day-by-day simulation of one symbol's intraday bars.
    Mandatory same-day square-off: any position still open at the last
    bar of a trading day is closed at that bar's Close, regardless of
    stop/target -- mirrors real MIS mechanics. Stop/target checks assume
    the worst case when a single bar's High/Low straddle both (stop
    checked first) -- a standard, conservative convention.

    symbol: recorded on every Trade regardless of what (if anything) the
    strategy itself set on Signal.symbol -- this function's caller always
    knows the real symbol (it's the dict key in run_backtest()'s `data`),
    so trusting the caller here is more reliable than depending on every
    strategy remembering to fill in signal.symbol correctly. Real bug
    found while building this: strategies following this project's
    existing convention (leave Signal.symbol="", let the caller fill it
    in) meant every trade was recorded as "UNKNOWN", silently breaking
    Performance Analyst's per-symbol/sector breakdowns.

    risk_params=None (default): capped at ONE trade per symbol per day --
    fixes a real discrepancy found while building this: the earlier ad hoc
    ORB backtest's code had a comment claiming this same one-per-day cap
    but never actually enforced it (a closed position immediately became
    eligible for a fresh signal on the very next bar). Pass a
    RiskParameters to instead allow up to max_trades_per_day and enforce
    daily_loss_limit_pct -- this is what makes those research parameters
    real inputs the Backtesting Engineer varies, not just a config object
    sitting unused.
    """
    trades = []
    df = df.copy()
    df["trade_date"] = df.index.date

    for trade_date, day_df in df.groupby("trade_date"):
        day_df = day_df.drop(columns=["trade_date"])
        if len(day_df) < 4:
            continue  # partial/holiday-truncated day, not enough bars to matter

        day_context = _compute_day_context(df, trade_date)
        position = None
        trades_today = 0
        realized_pnl_today = 0.0
        for i in range(len(day_df)):
            todays_bars_so_far = day_df.iloc[: i + 1]
            bar = day_df.iloc[i]
            is_last_bar = i == len(day_df) - 1

            if position is not None:
                hit_stop, hit_target = _check_exit(
                    position["direction"], float(bar["Low"]), float(bar["High"]),
                    position["stop_loss"], position["target"],
                )
                if hit_stop or hit_target:
                    exit_price = position["stop_loss"] if hit_stop else position["target"]
                    exit_reason = "stop_loss" if hit_stop else "target"
                elif is_last_bar:
                    exit_price = float(bar["Close"])
                    exit_reason = "eod_square_off"
                else:
                    continue
                pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price,
                                  position["quantity"])
                trades.append(Trade(
                    symbol=symbol, entry_date=trade_date, exit_date=trade_date,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    quantity=position["quantity"], pnl=pnl, exit_reason=exit_reason,
                    entry_hour=position["entry_hour"], direction=position["direction"],
                ))
                trades_today += 1
                realized_pnl_today += pnl
                position = None
                continue

            if is_last_bar:
                continue
            if risk_params is None:
                if trades_today >= 1:
                    continue  # one trade per symbol per day, the documented default behavior
            elif should_block_new_trade(realized_pnl_today, trades_today, capital, risk_params):
                continue

            signal = strategy.generate_signal(todays_bars_so_far, day_context)
            if signal is None:
                continue
            risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
            if risk_per_share <= 0:
                continue  # invalid stop for this direction (e.g. a BUY stop placed above entry)
            quantity = int((capital * risk_per_trade_pct) / risk_per_share)
            if quantity <= 0:
                continue
            entry_timestamp = todays_bars_so_far.index[-1]
            position = {
                "entry_price": signal.entry_price, "stop_loss": signal.stop_loss,
                "target": signal.target, "quantity": quantity, "direction": signal.direction,
                "entry_hour": entry_timestamp.hour + entry_timestamp.minute / 60,
            }

    return trades


def run_backtest(strategy: Strategy, data: dict, capital_per_symbol: float,
                  risk_per_trade_pct: float, risk_params: Optional[RiskParameters] = None) -> dict:
    """
    data: {symbol: DataFrame of intraday bars}. Each symbol gets its own
    independent capital_per_symbol (same first-pass methodology already
    used and validated this session -- a shared-capital-pool version can
    follow later if a hypothesis passes and is worth the extra build).

    Returns {"trades": list[Trade], "trading_calendar": list[date],
    "symbols": list[str]} -- the raw inputs compute_metrics() needs. Kept
    separate from compute_metrics() so walk-forward slicing can re-run
    metrics on trade subsets without re-simulating.
    """
    all_trades = []
    calendar = set()
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        trades = simulate_symbol(df, strategy, capital_per_symbol, risk_per_trade_pct, risk_params, symbol=symbol)
        all_trades.extend(trades)
        calendar.update(df.index.date)

    return {
        "trades": all_trades,
        "trading_calendar": sorted(calendar),
        "symbols": list(data.keys()),
        "capital_per_symbol": capital_per_symbol,
    }


def _daily_pnl_series(trades: list, trading_calendar: list) -> list:
    """One P&L number per trading day in the calendar (0.0 on days with no
    trades) -- needed so Sharpe/Sortino correctly treat a quiet day as a
    0% return day, not an excluded one. This is exactly what makes "no
    trade today is fine" (PART 5) show up honestly in the risk-adjusted
    metrics instead of only being visible in the trade count."""
    pnl_by_date = {}
    for t in trades:
        pnl_by_date[t.exit_date] = pnl_by_date.get(t.exit_date, 0.0) + t.pnl
    return [pnl_by_date.get(d, 0.0) for d in trading_calendar]


def compute_metrics(trades: list, starting_capital: float, trading_calendar: list) -> dict:
    """The full PART 7 metrics suite. Returns a plain dict (JSON-serializable
    as-is) so experiment_manager.save_experiment() can write it straight
    to metrics.json."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": None, "expectancy": 0.0,
            "cagr": 0.0, "sharpe_ratio": None, "sortino_ratio": None, "max_drawdown_pct": 0.0,
            "recovery_factor": None, "monthly_returns_pct": {}, "annual_returns_pct": {},
            "total_pnl": 0.0, "return_on_capital_pct": 0.0,
        }

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(t.pnl for t in losses)  # negative or zero
    total_pnl = sum(t.pnl for t in trades)

    win_rate = len(wins) / len(trades)
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else None
    expectancy = total_pnl / len(trades)

    days_in_period = max(1, (trading_calendar[-1] - trading_calendar[0]).days) if trading_calendar else 1
    years = days_in_period / 365.0
    ending_capital = starting_capital + total_pnl
    cagr = ((ending_capital / starting_capital) ** (1 / years) - 1) * 100 if years > 0 and ending_capital > 0 else None

    daily_pnl = _daily_pnl_series(trades, trading_calendar)
    daily_returns_pct = [p / starting_capital for p in daily_pnl]
    sharpe_ratio = _sharpe(daily_returns_pct)
    sortino_ratio = _sortino(daily_returns_pct)

    max_dd_pct, recovery_factor = _drawdown_and_recovery(daily_pnl, starting_capital, total_pnl)

    monthly_returns_pct = _bucket_returns_pct(trades, starting_capital, key=lambda d: d.strftime("%Y-%m"))
    annual_returns_pct = _bucket_returns_pct(trades, starting_capital, key=lambda d: d.strftime("%Y"))

    return {
        "total_trades": len(trades), "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "expectancy": round(expectancy, 2),
        "cagr": round(cagr, 2) if cagr is not None else None,
        "sharpe_ratio": round(sharpe_ratio, 3) if sharpe_ratio is not None else None,
        "sortino_ratio": round(sortino_ratio, 3) if sortino_ratio is not None else None,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "recovery_factor": round(recovery_factor, 3) if recovery_factor is not None else None,
        "monthly_returns_pct": monthly_returns_pct,
        "annual_returns_pct": annual_returns_pct,
        "total_pnl": round(total_pnl, 2),
        "return_on_capital_pct": round(total_pnl / starting_capital * 100, 2),
    }


def _sharpe(daily_returns_pct: list, annualization_days: int = 252) -> float:
    if len(daily_returns_pct) < 2:
        return None
    mean = sum(daily_returns_pct) / len(daily_returns_pct)
    variance = sum((r - mean) ** 2 for r in daily_returns_pct) / (len(daily_returns_pct) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(annualization_days)


def _sortino(daily_returns_pct: list, annualization_days: int = 252) -> float:
    if len(daily_returns_pct) < 2:
        return None
    mean = sum(daily_returns_pct) / len(daily_returns_pct)
    downside = [r for r in daily_returns_pct if r < 0]
    if not downside:
        return None
    downside_deviation = math.sqrt(sum(r ** 2 for r in downside) / len(daily_returns_pct))
    if downside_deviation == 0:
        return None
    return (mean / downside_deviation) * math.sqrt(annualization_days)


def _drawdown_and_recovery(daily_pnl: list, starting_capital: float, total_pnl: float) -> tuple:
    equity = starting_capital
    peak = starting_capital
    max_dd = 0.0
    for pnl in daily_pnl:
        equity += pnl
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, drawdown)
    max_dd_pct = max_dd * 100
    max_dd_abs = max_dd * starting_capital
    recovery_factor = (total_pnl / max_dd_abs) if max_dd_abs > 0 else None
    return max_dd_pct, recovery_factor


def _bucket_returns_pct(trades: list, starting_capital: float, key) -> dict:
    buckets = {}
    for t in trades:
        bucket_key = key(t.exit_date)
        buckets[bucket_key] = buckets.get(bucket_key, 0.0) + t.pnl
    return {k: round(v / starting_capital * 100, 2) for k, v in sorted(buckets.items())}


def walk_forward_split(start_date: date, end_date: date, n_windows: int) -> list:
    """Splits [start_date, end_date] into n_windows sequential,
    non-overlapping windows. The Statistical Auditor treats the LAST
    window as the true out-of-sample holdout and the rest as walk-forward
    consistency checks -- this function just does the date-range slicing,
    it has no opinion on which window means what."""
    total_days = (end_date - start_date).days
    if total_days <= 0 or n_windows <= 0:
        return []
    window_len = total_days / n_windows
    windows = []
    for i in range(n_windows):
        w_start = start_date + timedelta(days=int(i * window_len))
        w_end = start_date + timedelta(days=int((i + 1) * window_len)) if i < n_windows - 1 else end_date
        windows.append((w_start, w_end))
    return windows
