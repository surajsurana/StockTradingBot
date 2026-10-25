"""
Two passive benchmarks required by the approved implementation plan, both
built independently of swing_research/backtesting_engine.py's trade-based
model (buy-and-hold has no signal-driven exits; the index has no trades at
all):

- simulate_buy_and_hold(): one position per symbol, entered at the first
  available bar and held to the last -- no stop, no target, no
  signal-based exit. Returns the same shape as
  backtesting_engine.simulate_portfolio() so it can go through
  swing_research.metrics.compute_metrics() unchanged for a fair, apples-
  to-apples comparison table.
- compute_index_benchmark_metrics(): the Nifty 500 index's OWN passive
  return series (no trades, no position sizing) -- CAGR/Sharpe/Sortino/
  max-drawdown computed directly from its daily Close.

Both read data via data/fetch_historical.py's fetch_daily_candles()
pattern -- READ-ONLY reuse, this module never modifies that file or its
data source.
"""

from datetime import date
from typing import Optional

import numpy as np

from data.fetch_historical import fetch_daily_candles
from swing_research.backtesting_engine import (Trade, _day_keys, _positions_on_calendar, _sorted_by_date,
                                               _to_date)

# Nifty 500's yfinance ticker. Falls back to Nifty 50 (^NSEI, already used
# elsewhere in this codebase for the production regime filter) if this one
# proves unreliable -- see get_index_benchmark_series()'s docstring.
NIFTY_500_TICKER = "^CRSLDX"
NIFTY_50_FALLBACK_TICKER = "^NSEI"


def simulate_buy_and_hold(data: dict, capital_per_symbol: float, fractional_quantities: bool = False) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}. Each symbol gets an
    equal capital_per_symbol allocation, entered at its first available
    Close in the window and held to its last -- no stop, no target,
    matching what a genuinely passive investor would do, not a strategy.

    daily_equity here is a REAL mark-to-market curve (current Close x
    quantity, summed across all held symbols, each day) -- unlike
    simulate_portfolio()'s realized-only convention, which only updates
    equity when a trade closes. Buy-and-hold never closes until the very
    end, so a realized-only curve would be flat and meaningless for
    Sharpe/drawdown; mark-to-market is the correct (and only sensible)
    choice specifically for this benchmark.
    """
    # Memory-lean since 2026-10-25: each symbol keeps only its Close values plus one shared
    # calendar and a compact position array, instead of a {date: float} dict per symbol (~100 MB
    # on the 503-symbol US universe) and its own frame. Same closes (float() of the same values,
    # later bar winning on a duplicated date, as the dict did), same calendar, same summation
    # order -- so the same daily equity and trades to the last digit.
    trades = []
    key_intern = {}      # union calendar of every held symbol's days -- see _day_keys
    positions = {}       # symbol -> (quantity, entry_price, exit_price, entry_date, exit_date)
    day_keys = {}        # symbol -> its bars' calendar days, in row order
    close_at = {}        # symbol -> row position -> that bar's Close (as zip(df.index, df["Close"]) yielded it)

    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        df = _sorted_by_date(df)
        entry_price = float(df.iloc[0]["Close"])
        if entry_price <= 0:
            continue
        quantity = (round(capital_per_symbol / entry_price, 6) if fractional_quantities
                    else int(capital_per_symbol / entry_price))
        if quantity <= 0:
            continue
        positions[symbol] = (quantity, entry_price, float(df.iloc[-1]["Close"]),
                             _to_date(df.index[0]), _to_date(df.index[-1]))
        close = df["Close"]
        if isinstance(close.dtype, np.dtype) and close.dtype.kind not in "mM":
            close_at[symbol] = np.array(close.to_numpy(), copy=True).item   # what Series iteration yields
        else:
            close_at[symbol] = list(close).__getitem__
        day_keys[symbol] = _day_keys(df.index, key_intern)

    all_dates = sorted(key_intern)
    slot = {d: i for i, d in enumerate(all_dates)}
    position_on_day = {symbol: _positions_on_calendar(day_keys.pop(symbol), slot, len(all_dates))
                       for symbol in positions}

    daily_equity = {}
    last_known_close = {symbol: entry_price for symbol, (_, entry_price, _, _, _) in positions.items()}
    for i, d in enumerate(all_dates):
        equity_today = 0.0
        for symbol, (quantity, _, _, _, _) in positions.items():
            p = position_on_day[symbol][i]
            if p >= 0:
                last_known_close[symbol] = float(close_at[symbol](p))
            equity_today += quantity * last_known_close[symbol]
        daily_equity[d] = equity_today

    for symbol, (quantity, entry_price, exit_price, entry_date, exit_date) in positions.items():
        pnl = (exit_price - entry_price) * quantity
        trades.append(Trade(
            symbol=symbol, entry_date=entry_date, exit_date=exit_date,
            entry_price=entry_price, exit_price=exit_price, quantity=quantity,
            pnl=pnl, exit_reason="end_of_backtest", direction="BUY",
        ))

    starting_capital = capital_per_symbol * len(positions)
    return {
        "trades": trades, "trading_calendar": all_dates,
        "symbols": list(positions.keys()), "daily_equity": daily_equity,
        "starting_capital": starting_capital,
    }


def get_index_benchmark_series(period: str = "10y"):
    """
    Returns (ticker_used, DataFrame) for the Nifty 500 index, falling back
    to Nifty 50 if the 500 ticker fails to fetch real data (empty/missing
    result) -- read-only reuse of fetch_daily_candles(), same yfinance
    source the rest of this codebase already depends on.
    """
    try:
        df = fetch_daily_candles(NIFTY_500_TICKER, period=period)
        if df is not None and not df.empty:
            return NIFTY_500_TICKER, df
    except Exception:
        pass
    df = fetch_daily_candles(NIFTY_50_FALLBACK_TICKER, period=period)
    return NIFTY_50_FALLBACK_TICKER, df


def compute_index_benchmark_metrics(index_df, annualization_days: int = 252) -> dict:
    """Passive CAGR/Sharpe/Sortino/max-drawdown for the index itself --
    no trades, no position sizing, just its own daily Close series."""
    import numpy as np

    if index_df is None or index_df.empty:
        return {"cagr": None, "sharpe_ratio": None, "sortino_ratio": None, "max_drawdown_pct": None}

    closes = index_df["Close"].dropna()
    dates = [_to_date(ts) for ts in closes.index]
    values = closes.tolist()

    years = (dates[-1] - dates[0]).days / 365.25 if len(dates) > 1 else 0
    cagr = ((values[-1] / values[0]) ** (1 / years) - 1) * 100 if years > 0 and values[0] > 0 else None

    daily_rets = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values)) if values[i - 1] > 0]
    sharpe_ratio = sortino_ratio = None
    if daily_rets:
        mean_ret = np.mean(daily_rets)
        std_ret = np.std(daily_rets, ddof=1) if len(daily_rets) > 1 else 0.0
        sharpe_ratio = (mean_ret / std_ret) * (annualization_days ** 0.5) if std_ret > 0 else None
        downside = [r for r in daily_rets if r < 0]
        downside_std = np.std(downside, ddof=1) if len(downside) > 1 else 0.0
        sortino_ratio = (mean_ret / downside_std) * (annualization_days ** 0.5) if downside_std > 0 else None

    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    return {
        "cagr": round(cagr, 2) if cagr is not None else None,
        "sharpe_ratio": round(sharpe_ratio, 3) if sharpe_ratio is not None else None,
        "sortino_ratio": round(sortino_ratio, 3) if sortino_ratio is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
    }
