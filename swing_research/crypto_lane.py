"""
The crypto research lane (Pool E, 2026-09-13): the SAME pipeline as every
swing strategy -- simulate_portfolio, walk-forward windows, the frozen
Statistical Auditor, evidence quality, Performance Analyst narrative,
experiment record -- with three differences, all here and nowhere else:

  1. Every window's trades (and the full-period run) pass through
     swing_research/crypto_costs.py before the Auditor sees them: fees +
     spread, then India's per-trade 31.2% tax. The verdict is POST-TAX.
  2. Pre-tax metrics (fees only) are recorded alongside, per window and
     for the full period, so every report shows both.
  3. Calendar days are bars (365/yr), and the benchmark is BTC buy & hold
     instead of the Nifty 500.

Reuses research_director.run_generic_swing_experiment() through its
walk_forward_fn / full_period_trade_adjuster hooks -- the same hooks the
execution-realism engine uses -- so nothing in the shared pipeline is
modified for crypto.
"""

import json
import os
from datetime import date
from typing import Callable, Optional

from research_lab import backtesting_engineer, statistical_auditor
from swing_research import benchmarks
from swing_research.backtesting_engine import simulate_portfolio
from swing_research.base import Strategy
from swing_research.crypto_costs import (
    INDIA_VDA_TAX_RATE, CryptoCostModel, apply_crypto_costs, apply_india_tax, tax_summary,
)
from swing_research.execution_realism_engine import build_approximate_daily_equity
from swing_research.metrics import compute_metrics
from swing_research.research_director import (
    SWING_EXPERIMENTS_DIR, SWING_KNOWLEDGE_BASE_PATH, run_generic_swing_experiment,
)

CRYPTO_ANNUALIZATION_DAYS = 365
CRYPTO_STARTING_CAPITAL_USDT = 1_000.0


def _metrics_for(trades: list, starting_capital: float, calendar: list) -> dict:
    """compute_metrics() on a post-processed trade list with the same
    dense proxy equity curve the execution-realism engine uses, so CAGR /
    Sharpe / drawdown exist for both the pre-tax and post-tax series."""
    equity = build_approximate_daily_equity(trades, starting_capital, calendar)
    return compute_metrics(trades, starting_capital, calendar, daily_equity=equity,
                           annualization_days=CRYPTO_ANNUALIZATION_DAYS)


def run_walk_forward_crypto(strategy: Strategy, data: dict, starting_capital: float, sector_map: dict,
                            start_date: date, end_date: date, n_walk_forward_windows: int = 3,
                            max_units_per_sector: int = 6, max_units_total: int = 10,
                            extra_columns_by_symbol: Optional[dict] = None,
                            min_trades_total: int = 15, min_out_of_sample_trades: int = 3,
                            min_consistent_window_fraction: float = 0.5,
                            cost_model: CryptoCostModel = CryptoCostModel(),
                            tax_rate: float = INDIA_VDA_TAX_RATE) -> dict:
    """research_director.run_walk_forward_generic() with costs + Indian
    tax applied to each window's trades before its metrics -- the
    Auditor's inputs are post-tax. Pre-tax (fees-only) window metrics are
    returned too under "pre_tax_walk_forward_metrics"/"pre_tax_out_of_sample_metrics"."""
    windows = backtesting_engineer.walk_forward_split(start_date, end_date, n_walk_forward_windows)
    post_tax_metrics, pre_tax_metrics, post_tax_trades = [], [], []
    for w_start, w_end in windows:
        windowed_data = {sym: df[(df.index.date >= w_start) & (df.index.date <= w_end)] for sym, df in data.items()}
        windowed_extra = None
        if extra_columns_by_symbol:
            windowed_extra = {sym: s[(s.index.date >= w_start) & (s.index.date <= w_end)]
                              for sym, s in extra_columns_by_symbol.items()}
        result = simulate_portfolio(windowed_data, strategy, starting_capital, sector_map=sector_map,
                                    max_units_per_sector=max_units_per_sector, max_units_total=max_units_total,
                                    extra_columns_by_symbol=windowed_extra)
        raw = result["trades"]
        pre = apply_crypto_costs(raw, cost_model)
        post = apply_india_tax(raw, cost_model, tax_rate)
        post_tax_metrics.append(_metrics_for(post, starting_capital, result["trading_calendar"]))
        pre_tax_metrics.append(_metrics_for(pre, starting_capital, result["trading_calendar"]))
        post_tax_trades.append(post)

    out_of_sample = post_tax_metrics[-1] if post_tax_metrics else {}
    consistency = post_tax_metrics[:-1]
    verdict = statistical_auditor.audit(
        consistency, out_of_sample, min_trades_total=min_trades_total,
        min_out_of_sample_trades=min_out_of_sample_trades,
        min_consistent_window_fraction=min_consistent_window_fraction,
    )
    return {
        "verdict": verdict, "walk_forward_metrics": consistency, "out_of_sample_metrics": out_of_sample,
        "out_of_sample_trades": post_tax_trades[-1] if post_tax_trades else [],
        "all_trades": [t for trades in post_tax_trades for t in trades], "windows": windows,
        "pre_tax_walk_forward_metrics": pre_tax_metrics[:-1],
        "pre_tax_out_of_sample_metrics": pre_tax_metrics[-1] if pre_tax_metrics else {},
    }


def _augment_experiment_with_pre_tax(exp_id: str, experiments_dir: str, data: dict, starting_capital: float,
                                     cost_model: CryptoCostModel, tax_rate: float, holder: dict) -> None:
    """Adds pre-tax full-period metrics, the tax ledger and a BTC buy &
    hold benchmark to the saved metrics.json -- the generic runner only
    knows about the (post-tax) adjusted trades it was handed. `holder`
    carries the raw full-period trades captured by the trade adjuster and
    the pre-tax window metrics from run_walk_forward_crypto()."""
    raw = holder.get("full_raw_trades", [])
    calendar = sorted({d for df in data.values() for d in df.index.date})
    pre_metrics = _metrics_for(apply_crypto_costs(raw, cost_model), starting_capital, calendar)
    ledger = tax_summary(raw, cost_model, tax_rate)
    btc = {}
    if "BTC" in data:
        btc_result = benchmarks.simulate_buy_and_hold({"BTC": data["BTC"]}, starting_capital, fractional_quantities=True)
        btc = compute_metrics(btc_result["trades"], btc_result["starting_capital"], btc_result["trading_calendar"],
                              daily_equity=btc_result["daily_equity"], annualization_days=CRYPTO_ANNUALIZATION_DAYS)

    path = os.path.join(experiments_dir, exp_id, "metrics.json")
    with open(path, encoding="utf-8") as f:
        metrics = json.load(f)
    metrics["pre_tax"] = {
        "full_period": pre_metrics,
        "walk_forward_metrics": holder.get("pre_tax_walk_forward_metrics", []),
        "out_of_sample_metrics": holder.get("pre_tax_out_of_sample_metrics", {}),
    }
    metrics["tax_ledger_full_period"] = ledger
    metrics["book_currency"] = "USDT"
    if btc:
        metrics.setdefault("comparison_vs_benchmarks", {})["btc_buy_and_hold_pre_tax"] = btc
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)


def run_crypto_experiment_generic(strategy: Strategy, published, data: dict, start_date: date, end_date: date,
                                  starting_capital: float = CRYPTO_STARTING_CAPITAL_USDT,
                                  n_walk_forward_windows: int = 3, extra_columns_by_symbol: Optional[dict] = None,
                                  extra_parameters: Optional[dict] = None, narrative_api_key: str = "",
                                  narrative_call_fn: Optional[Callable[[str], str]] = None,
                                  experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                  knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                  skip_regime_breakdown: bool = True,
                                  cost_model: CryptoCostModel = CryptoCostModel(),
                                  tax_rate: float = INDIA_VDA_TAX_RATE) -> str:
    """One crypto experiment for ANY Strategy: the generic swing pipeline
    with the post-tax walk-forward, the post-tax full-period adjuster,
    365-day annualisation, and the pre-tax/tax-ledger/BTC augmentation."""
    holder = {}

    def walk_forward_fn(*args, **kwargs):
        result = run_walk_forward_crypto(*args, cost_model=cost_model, tax_rate=tax_rate, **kwargs)
        holder.update(result)
        return result

    def full_period_trade_adjuster(trades: list, _data: dict) -> list:
        holder["full_raw_trades"] = trades
        return apply_india_tax(trades, cost_model, tax_rate)

    exp_id = run_generic_swing_experiment(
        strategy, published, data, start_date, end_date, starting_capital, n_walk_forward_windows,
        extra_columns_by_symbol=extra_columns_by_symbol, narrative_api_key=narrative_api_key,
        narrative_call_fn=narrative_call_fn, experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown, annualization_days=CRYPTO_ANNUALIZATION_DAYS,
        walk_forward_fn=walk_forward_fn, full_period_trade_adjuster=full_period_trade_adjuster,
        extra_parameters={
            "universe_version": "crypto-usdt-2026-09-13", "universe_symbol_count": len(data),
            "universe_symbols": sorted(data), "book_currency": "USDT",
            "crypto_risk_pct_per_unit": strategy.risk_pct_per_unit, "crypto_long_only": True,
            "crypto_cost_model": cost_model.describe(),
            "india_vda_tax_rate_per_profitable_trade": tax_rate, "india_vda_loss_offset": False,
            "india_vda_fees_deductible": False, "india_vda_tds_rate_on_sales": 0.01,
            "verdict_basis": "POST-TAX trades (fees + spread + 31.2% per profitable trade)",
            **(extra_parameters or {}),
        },
    )
    _augment_experiment_with_pre_tax(exp_id, experiments_dir, data, starting_capital, cost_model, tax_rate, holder)
    return exp_id


def run_crypto_xs_momentum_experiment(data: dict, start_date: date, end_date: date, **kwargs) -> str:
    from swing_research.strategies.crypto_xs_momentum import (
        CryptoCrossSectionalMomentumStrategy, HOLDING_DAYS, STOP_LOSS_PCT, TOP_QUINTILE_PERCENTILE,
    )
    from swing_research.published_research_analyst import CRYPTO_XS_MOMENTUM
    from swing_research.cross_sectional import CRYPTO_MOMENTUM_LOOKBACK_DAYS, compute_crypto_momentum_percentile_ranks

    ranks = compute_crypto_momentum_percentile_ranks(data)
    extra_columns = {symbol: series.rename("crypto_momentum_percentile") for symbol, series in ranks.items()}
    return run_crypto_experiment_generic(
        CryptoCrossSectionalMomentumStrategy(), CRYPTO_XS_MOMENTUM, data, start_date, end_date,
        extra_columns_by_symbol=extra_columns,
        extra_parameters={"crypto_momentum_lookback_days": CRYPTO_MOMENTUM_LOOKBACK_DAYS,
                          "crypto_top_quintile_percentile": TOP_QUINTILE_PERCENTILE, "crypto_holding_days": HOLDING_DAYS,
                          "crypto_rebalance_weekday": "Monday (UTC)", "crypto_stop_loss_pct": STOP_LOSS_PCT},
        **kwargs,
    )


def run_crypto_trend_timing_experiment(data: dict, start_date: date, end_date: date, **kwargs) -> str:
    """Faber's 10-month SMA rule on the majors (EXP-083 candidate, 2026-09-13)."""
    from swing_research.strategies.crypto_trend_timing import (
        CryptoTrendTimingStrategy, SMA_MONTHS, STOP_LOSS_PCT, compute_month_end_sma,
    )
    from swing_research.published_research_analyst import CRYPTO_TREND_TIMING

    # Warm-up: the 10-month SMA from FULL history, windowed by date later,
    # so no walk-forward window starts ten months blind (post-EXP-083).
    extra_columns = {symbol: compute_month_end_sma(df)[["sma_month_end"]] for symbol, df in data.items()}
    return run_crypto_experiment_generic(
        CryptoTrendTimingStrategy(), CRYPTO_TREND_TIMING, data, start_date, end_date,
        extra_columns_by_symbol=extra_columns,
        extra_parameters={"crypto_sma_months": SMA_MONTHS, "crypto_rebalance": "last UTC calendar day of each month",
                          "crypto_stop_loss_pct": STOP_LOSS_PCT, "crypto_sma_warm_up_from_full_history": True},
        **kwargs,
    )


def run_crypto_tsmom_experiment(data: dict, start_date: date, end_date: date, **kwargs) -> str:
    """Moskowitz-Ooi-Pedersen 12-month time-series momentum on the majors (2026-09-14)."""
    from swing_research.strategies.crypto_tsmom import (
        CryptoTimeSeriesMomentumStrategy, STOP_LOSS_PCT, TSMOM_LOOKBACK_DAYS, compute_tsmom_signal,
    )
    from swing_research.published_research_analyst import CRYPTO_TSMOM

    extra_columns = {symbol: compute_tsmom_signal(df)[["tsmom_return"]] for symbol, df in data.items()}
    return run_crypto_experiment_generic(
        CryptoTimeSeriesMomentumStrategy(), CRYPTO_TSMOM, data, start_date, end_date,
        extra_columns_by_symbol=extra_columns,
        extra_parameters={"crypto_tsmom_lookback_calendar_days": TSMOM_LOOKBACK_DAYS,
                          "crypto_rebalance": "last UTC calendar day of each month",
                          "crypto_stop_loss_pct": STOP_LOSS_PCT, "crypto_volatility_scaling": False,
                          "crypto_signal_warm_up_from_full_history": True},
        **kwargs,
    )


def run_crypto_vol_managed_experiment(data: dict, start_date: date, end_date: date, **kwargs) -> str:
    """Moreira & Muir (2017) volatility-managed exposure on the majors (2026-09-14)."""
    from swing_research.strategies.crypto_vol_managed import (
        CryptoVolManagedStrategy, MIN_WEIGHT, REBALANCE_BAND, STOP_LOSS_PCT, TARGET_VOL, VOL_LOOKBACK_DAYS,
        compute_vol_weight,
    )
    from swing_research.published_research_analyst import CRYPTO_VOL_MANAGED

    extra_columns = {symbol: compute_vol_weight(df)[["vol_weight"]] for symbol, df in data.items()}
    return run_crypto_experiment_generic(
        CryptoVolManagedStrategy(), CRYPTO_VOL_MANAGED, data, start_date, end_date,
        extra_columns_by_symbol=extra_columns,
        extra_parameters={"crypto_target_vol_annual": TARGET_VOL, "crypto_vol_lookback_days": VOL_LOOKBACK_DAYS,
                          "crypto_min_weight": MIN_WEIGHT, "crypto_rebalance_band": REBALANCE_BAND,
                          "crypto_rebalance": "last UTC calendar day of each month (exit + re-enter at the new weight)",
                          "crypto_stop_loss_pct": STOP_LOSS_PCT, "crypto_leverage": False,
                          "crypto_signal_warm_up_from_full_history": True},
        **kwargs,
    )


def run_crypto_trend_timing_weekly_experiment(data: dict, start_date: date, end_date: date, **kwargs) -> str:
    """Faber's rule at weekly cadence (2026-09-17) -- see crypto_trend_timing_weekly.py."""
    from swing_research.strategies.crypto_trend_timing_weekly import (
        CryptoWeeklyTrendTimingStrategy, SMA_MONTHS_EQUIVALENT_DAYS, STOP_LOSS_PCT, compute_week_end_sma,
    )
    from swing_research.published_research_analyst import CRYPTO_TREND_TIMING_WEEKLY

    extra_columns = {symbol: compute_week_end_sma(df)[["sma_week_end"]] for symbol, df in data.items()}
    return run_crypto_experiment_generic(
        CryptoWeeklyTrendTimingStrategy(), CRYPTO_TREND_TIMING_WEEKLY, data, start_date, end_date,
        extra_columns_by_symbol=extra_columns,
        extra_parameters={"crypto_sma_lookback_days": SMA_MONTHS_EQUIVALENT_DAYS,
                          "crypto_rebalance": "last day of every ISO week (Sunday, UTC)",
                          "crypto_stop_loss_pct": STOP_LOSS_PCT, "crypto_signal_warm_up_from_full_history": True},
        **kwargs,
    )
