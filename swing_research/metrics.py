"""
Metrics for the Swing Research Program's backtesting engine. Conceptually
carries over the metric *definitions* research_lab/backtesting_engineer.py
already uses (profit factor, expectancy, Sharpe, Sortino, max drawdown,
recovery factor) -- not imported, since the underlying trade/equity model
genuinely differs here (a real compounding equity curve from
swing_research/backtesting_engine.py's realized-equity tracking, vs.
research_lab's fixed-starting-capital simplification) -- plus new metrics
this program's benchmarking requirement needs that research_lab never
computes: avg_holding_period_days, exposure_pct, rolling_annual_returns.
"""

from collections import defaultdict
from datetime import date
from typing import Optional

import numpy as np


def _daily_returns(daily_equity: dict) -> list:
    dates = sorted(daily_equity.keys())
    values = [daily_equity[d] for d in dates]
    returns = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev > 0:
            returns.append((values[i] - prev) / prev)
    return returns


def compute_metrics(trades: list, starting_capital: float, trading_calendar: list,
                     daily_equity: Optional[dict] = None, annualization_days: int = 252) -> dict:
    """
    trades: list[swing_research.backtesting_engine.Trade].
    daily_equity: {date: realized_equity}, as returned by simulate_portfolio()
    -- required for CAGR/Sharpe/Sortino/max_drawdown/recovery_factor/
    rolling_annual_returns (all equity-curve-based); if omitted, those keys
    are returned as None rather than guessed.
    """
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": None, "expectancy": 0.0,
            "cagr": None, "sharpe_ratio": None, "sortino_ratio": None, "max_drawdown_pct": None,
            "recovery_factor": None, "avg_holding_period_days": None, "exposure_pct": None,
            "monthly_returns_pct": {}, "annual_returns_pct": {}, "rolling_annual_returns_pct": {},
            "total_pnl": 0.0, "return_on_capital_pct": 0.0,
        }

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(t.pnl for t in losses)
    total_pnl = sum(t.pnl for t in trades)

    win_rate = len(wins) / total_trades
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss < 0 else (0.0 if gross_profit == 0 else None)
    expectancy = total_pnl / total_trades
    return_on_capital_pct = (total_pnl / starting_capital * 100) if starting_capital > 0 else 0.0

    holding_days = [(t.exit_date - t.entry_date).days for t in trades]
    avg_holding_period_days = sum(holding_days) / len(holding_days) if holding_days else None

    monthly_pnl = defaultdict(float)
    annual_pnl = defaultdict(float)
    for t in trades:
        monthly_pnl[t.exit_date.strftime("%Y-%m")] += t.pnl
        annual_pnl[t.exit_date.strftime("%Y")] += t.pnl
    monthly_returns_pct = {k: round(v / starting_capital * 100, 4) for k, v in sorted(monthly_pnl.items())}
    annual_returns_pct = {k: round(v / starting_capital * 100, 4) for k, v in sorted(annual_pnl.items())}

    cagr = sharpe_ratio = sortino_ratio = max_drawdown_pct = recovery_factor = None
    rolling_annual_returns_pct = {}
    exposure_pct = None

    if daily_equity:
        dates = sorted(daily_equity.keys())
        values = [daily_equity[d] for d in dates]

        years = (dates[-1] - dates[0]).days / 365.25 if len(dates) > 1 else 0
        if years > 0 and values[0] > 0:
            cagr = ((values[-1] / values[0]) ** (1 / years) - 1) * 100

        daily_rets = _daily_returns(daily_equity)
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
                dd = (peak - v) / peak
                max_dd = max(max_dd, dd)
        max_drawdown_pct = max_dd * 100
        max_drawdown_amount = max_dd * peak
        recovery_factor = (total_pnl / max_drawdown_amount) if max_drawdown_amount > 0 else None

        # Rolling 12-month (approx 252 trading day) return, sampled at each
        # month-end present in the equity curve -- distinct from
        # annual_returns_pct above, which buckets by CALENDAR year of exit,
        # not a trailing 12-month window.
        window = annualization_days
        last_seen_month = None
        for i, d in enumerate(dates):
            month_key = d.strftime("%Y-%m")
            if month_key == last_seen_month:
                continue
            last_seen_month = month_key
            if i >= window and values[i - window] > 0:
                rolling_annual_returns_pct[month_key] = round(
                    (values[i] / values[i - window] - 1) * 100, 4
                )

        # Exposure: symbol-days-held / symbol-days-available, approximated
        # at the portfolio level as total holding-days across all trades
        # divided by (number of distinct symbols traded x trading calendar
        # length) -- a portfolio-wide utilization measure, not a per-symbol
        # exact figure (a symbol's actual available history may be shorter
        # than the full calendar; this is a disclosed approximation).
        symbols_traded = {t.symbol for t in trades}
        if symbols_traded and trading_calendar:
            exposure_pct = round(
                sum(holding_days) / (len(symbols_traded) * len(trading_calendar)) * 100, 2
            )

    return {
        "total_trades": total_trades, "win_rate": round(win_rate, 4), "profit_factor": profit_factor,
        "expectancy": round(expectancy, 2), "cagr": round(cagr, 2) if cagr is not None else None,
        "sharpe_ratio": round(sharpe_ratio, 3) if sharpe_ratio is not None else None,
        "sortino_ratio": round(sortino_ratio, 3) if sortino_ratio is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct, 2) if max_drawdown_pct is not None else None,
        "recovery_factor": round(recovery_factor, 3) if recovery_factor is not None else None,
        "avg_holding_period_days": round(avg_holding_period_days, 1) if avg_holding_period_days is not None else None,
        "exposure_pct": exposure_pct,
        "monthly_returns_pct": monthly_returns_pct, "annual_returns_pct": annual_returns_pct,
        "rolling_annual_returns_pct": rolling_annual_returns_pct,
        "total_pnl": round(total_pnl, 2), "return_on_capital_pct": round(return_on_capital_pct, 2),
    }


def build_daily_equity_from_trades(trades: list, starting_capital: float, trading_calendar: list) -> dict:
    """
    Reconstructs a realized-equity daily series from a plain trades list --
    generic, works for ANY engine's output regardless of whether that
    engine tracks its own equity curve natively. Used for the two
    production-strategy benchmarks (simulate_symbol_single_unit() has no
    native equity tracking, unlike simulate_portfolio()) so they still get
    CAGR/Sharpe/Sortino/max-drawdown in the comparison table, not just
    win-rate/profit-factor. Equity only changes on days a trade actually
    closed (realized, cash-basis) -- same convention as
    backtesting_engine.simulate_portfolio()'s own equity tracking.
    """
    pnl_by_date = defaultdict(float)
    for t in trades:
        pnl_by_date[t.exit_date] += t.pnl

    equity = starting_capital
    daily_equity = {}
    for d in sorted(trading_calendar):
        equity += pnl_by_date.get(d, 0.0)
        daily_equity[d] = equity
    return daily_equity


def compute_holding_period_breakdown(trades: list, bucket_days: tuple = (5, 10, 20, 40, 80)) -> dict:
    """P&L bucketed by holding-period length -- the swing-specific analogue
    of research_lab/performance_analyst.py's compute_time_of_day_breakdown()
    (intraday-only, meaningless here). Buckets are cumulative upper bounds
    in trading days-ish (calendar days, for simplicity): "<=5d", "<=10d",
    ..., ">last_bound d"."""
    breakdown = defaultdict(float)
    for t in trades:
        days = (t.exit_date - t.entry_date).days
        label = None
        for bound in bucket_days:
            if days <= bound:
                label = f"<={bound}d"
                break
        if label is None:
            label = f">{bucket_days[-1]}d"
        breakdown[label] += t.pnl
    return {k: round(v, 2) for k, v in breakdown.items()}
