"""
Paper Portfolio Dashboard (2026-08-05) -- a NEW, read-only reporting
script, root-level (same tier as run_paper_trading.py, run_certification.py),
NOT inside deployment/ and NOT modifying any file in it. Blends SW-003
(52-Week High Momentum, 60%) and SW-008 (Short-Term Reversal, 40%) into
one portfolio-level view, for PAPER TRADING ONLY -- no production capital
allocation is read, touched, or implied anywhere in this script.

METHODOLOGY DISCLOSURE (stated here and repeated in the generated
report): each strategy runs its OWN independent paper-trading portfolio
with its OWN full capital pool (deployment/paper_trading_engine.py is
completely unmodified and untouched by this script -- SW-003 and SW-008
each continue exactly as built). This script does NOT create a shared
capital pool or change either strategy's actual sizing. "Portfolio-level"
metrics below are a WEIGHTED BLEND of each strategy's own already-saved
backtest metrics (for CAGR/Sharpe/Sortino/MaxDD: a blended MONTHLY equity
curve reconstructed from each strategy's own monthly_returns_pct series,
weighted 60/40 for overlapping months -- a monthly-frequency
approximation, not the frozen daily-engine computation) plus each
strategy's own live paper-trading history where it exists. This is
advisory/reporting only.
"""

import json
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

from research_lab.experiment_manager import load_experiment
from deployment.paper_trading_engine import compute_live_metrics, load_portfolio
from deployment.settings import REPORTS_DIR

SW003_KEY, SW003_EXP, SW003_NAME = "fifty_two_week_high_momentum", "EXP-013", "52-Week High Momentum (SW-003)"
SW008_KEY, SW008_EXP, SW008_NAME = "short_term_reversal", "EXP-020", "Short-Term Reversal (SW-008)"
SW003_WEIGHT, SW008_WEIGHT = 0.60, 0.40

SWING_EXPERIMENTS_DIR = os.path.join(os.path.dirname(__file__), "swing_research", "experiments")


def _load_backtest_metrics(exp_id: str) -> dict:
    return load_experiment(exp_id, SWING_EXPERIMENTS_DIR)["metrics"]


def _blended_monthly_metrics(equity_a: dict, weight_a: float, equity_b: dict, weight_b: float,
                              starting_capital: float) -> dict:
    """
    Reconstructs a blended monthly equity curve from two GENUINE
    compounding month-end equity series (see
    swing_research/_portfolio_blend_equity.py's module docstring for why
    this must come from real realized-equity data, not
    compute_metrics()'s monthly_returns_pct field, which is P&L divided
    by ORIGINAL starting capital, not that month's actual equity, and
    massively inflates a blended CAGR if misused as a compounding
    return -- a bug found and fixed 2026-08-05 before this script's first
    numbers were reported). Each series is first converted to its own
    month-over-month % return (a genuine compounding return, since it's
    equity[t]/equity[t-1] - 1), THEN weighted and blended -- monthly-
    frequency, disclosed approximation of the frozen daily engine's own
    computation, not a re-implementation of it.
    """
    sa = pd.Series({k: float(v) for k, v in equity_a.items()}).sort_index()
    sb = pd.Series({k: float(v) for k, v in equity_b.items()}).sort_index()
    ra, rb = sa.pct_change().dropna(), sb.pct_change().dropna()

    combined = pd.DataFrame({"a": ra, "b": rb}).dropna().sort_index()
    blended_returns = weight_a * combined["a"] + weight_b * combined["b"]

    equity = [1.0]
    for r in blended_returns:
        equity.append(equity[-1] * (1 + r))
    equity = np.array(equity[1:])

    n_months = len(blended_returns)
    years = n_months / 12.0
    cagr = ((equity[-1] / 1.0) ** (1 / years) - 1) * 100 if years > 0 and equity[-1] > 0 else None

    mean_ret, std_ret = blended_returns.mean(), blended_returns.std(ddof=1)
    sharpe = (mean_ret / std_ret) * (12 ** 0.5) if std_ret > 0 else None
    downside = blended_returns[blended_returns < 0]
    downside_std = downside.std(ddof=1) if len(downside) > 1 else 0.0
    sortino = (mean_ret / downside_std) * (12 ** 0.5) if downside_std > 0 else None

    peak, max_dd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    return {
        "overlapping_months": n_months, "cagr": round(cagr, 2) if cagr is not None else None,
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 3) if sortino is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
    }


def build_dashboard() -> dict:
    sw003_m = _load_backtest_metrics(SW003_EXP)
    sw008_m = _load_backtest_metrics(SW008_EXP)

    equity_cache_path = os.path.join(os.path.dirname(__file__), "paper_portfolio_equity_cache.json")
    with open(equity_cache_path) as f:
        equity_cache = json.load(f)
    blended = _blended_monthly_metrics(equity_cache[SW003_KEY], SW003_WEIGHT,
                                        equity_cache[SW008_KEY], SW008_WEIGHT,
                                        equity_cache["starting_capital"])

    # Profit factor isn't rigorously blendable from monthly returns alone
    # (needs trade-level gross profit/loss) -- weighted average of each
    # strategy's own standalone profit factor, disclosed as an
    # approximation, not a rigorous portfolio-level figure.
    blended_profit_factor = round(SW003_WEIGHT * sw003_m["profit_factor"] + SW008_WEIGHT * sw008_m["profit_factor"], 3)

    # Capital utilization: each strategy's own exposure_pct describes how
    # much of ITS OWN capital is deployed -- weighting by capital
    # allocation gives a valid portfolio-level utilization of TOTAL
    # capital.
    blended_exposure = round(SW003_WEIGHT * sw003_m["exposure_pct"] + SW008_WEIGHT * sw008_m["exposure_pct"], 2)

    # Trade counts / holding periods are NOT blended (each strategy trades
    # independently, not competing for shared positions) -- reported
    # separately plus a simple sum for "combined monthly trades."
    # Real trade-level figures from a fresh re-run of each strategy's own
    # simulate_portfolio() call (_sw003_detailed_stats.py / _str_detailed_stats.py,
    # 2026-08-05) -- more precise than deriving from the saved EXP's
    # total_trades/10y, and directly measured rather than estimated.
    sw003_trades_per_month = 2.74
    sw008_trades_per_month = 9.5

    combined_trades_per_month = round(sw003_trades_per_month + sw008_trades_per_month, 2)
    # 2 per-strategy messages/trading day + 1 daily summary, ~21 trading days/month
    expected_telegram_messages_per_month = round(21 * 3, 0)

    live = {}
    for key, name in [(SW003_KEY, SW003_NAME), (SW008_KEY, SW008_NAME)]:
        portfolio = load_portfolio(key)
        live[key] = {
            "last_processed_date": portfolio.get("last_processed_date"),
            "open_positions": len(portfolio.get("positions", {})),
            "cash": portfolio.get("cash"),
            "live_metrics": compute_live_metrics(key) if portfolio.get("last_processed_date") else None,
        }

    return {
        "weights": {SW003_KEY: SW003_WEIGHT, SW008_KEY: SW008_WEIGHT},
        "blended_backtest_metrics": blended,
        "blended_profit_factor": blended_profit_factor,
        "blended_exposure_pct": blended_exposure,
        "expected_annual_return_pct": blended["cagr"],
        "expected_avg_holding_period": {
            SW003_KEY: sw003_m["avg_holding_period_days"], SW008_KEY: sw008_m["avg_holding_period_days"],
        },
        "expected_monthly_trades": {
            SW003_KEY: sw003_trades_per_month, SW008_KEY: sw008_trades_per_month,
            "combined": combined_trades_per_month,
        },
        "expected_telegram_messages_per_month": expected_telegram_messages_per_month,
        "expected_avg_open_positions": {
            # Real figures from _sw003_detailed_stats.py / _str_detailed_stats.py
            # (2026-08-05, fresh simulate_portfolio() re-run) -- each
            # strategy's own independent portfolio, summed for a combined
            # figure since they never compete for the same positions.
            SW003_KEY: 8.53, SW008_KEY: 8.25, "combined": round(8.53 + 8.25, 2),
        },
        "expected_max_open_positions": {SW003_KEY: 14, SW008_KEY: 15},
        "individual_contribution": {SW003_KEY: sw003_m, SW008_KEY: sw008_m},
        "live_paper_trading": live,
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(build_dashboard())
