"""
Temporal Regime Analysis -- an ADDITIONAL DIAGNOSTIC layer (added
2026-08-16, per explicit direction) for strategies that show strong
long-term (base) performance but fail the recent-period check: splits the
"recent, underperforming" window into finer eras to help distinguish
STRUCTURAL DECAY (the edge is genuinely gone) from TEMPORARY REGIME
DEPENDENCE (the edge was suppressed by a specific, identifiable market
period -- e.g. COVID/transition volatility -- and may have recovered
since).

GOVERNANCE (same discipline as every other diagnostic layer in this
program, e.g. execution_realism_engine.py, execution_realism_study.md):
- NEVER re-runs or overwrites a strategy's existing base/recent-period/
  robustness experiments -- those remain the permanent, official record.
  This module only ADDS new, additional experiment records.
- Reuses the strategy's OWN existing run_*_experiment() function
  (research_director.py) COMPLETELY UNCHANGED -- same Strategy class,
  same rules, same execution assumptions (zero-cost for a strategy that
  was originally evaluated zero-cost, execution-realistic for one that
  was originally evaluated that way, since this just calls whatever
  function the caller passes in). No parameter tuning.
- Reuses the frozen acceptance framework's OWN strategy-aware windowing
  logic (swing_research.acceptance_criteria._feasible_window_count(),
  imported read-only, never modified) -- the exact same function
  run_recent_period_check() itself already uses, so a temporal-regime
  sub-period is windowed with identical discipline to the official
  recent-period check.
- NEVER changes any strategy's Research Verdict or Deployment Status --
  purely diagnostic. Interpreting the results (structural decay vs.
  temporary regime dependence vs. recovery vs. no clear conclusion) is a
  human/caller judgment call informed by this module's output, not
  something this module decides or automates.
"""

from datetime import date
from typing import Callable, Optional

from swing_research.acceptance_criteria import _feasible_window_count, MIN_TRADEABLE_DAYS_PER_WINDOW


def run_temporal_regime_split(experiment_fn: Callable, data: dict, strategy,
                               periods: dict, starting_capital: float = 1_000_000,
                               requested_windows: int = 3,
                               narrative_api_key: str = "",
                               min_tradeable_days_per_window: int = MIN_TRADEABLE_DAYS_PER_WINDOW,
                               **experiment_kwargs) -> dict:
    """
    experiment_fn: the strategy's own existing run_*_experiment function
    (e.g. research_director.run_betting_against_beta_experiment) --
    called with EXACTLY the same keyword shape every other caller of that
    function already uses (data, start_date, end_date, starting_capital,
    n_walk_forward_windows, narrative_api_key, **experiment_kwargs) --
    nothing about the function itself is touched or wrapped.

    data: the ALREADY-FETCHED full-history {symbol: DataFrame} the
    strategy's own base run used (or a fresh, identically-sourced fetch --
    this module never re-fetches on its own, the caller controls that).

    periods: {label: (start_date, end_date)} -- e.g.
    {"covid_transition_2020_2022": (date(2020,1,1), date(2022,12,31)),
     "current_regime_2023_latest": (date(2023,1,1), latest_available_date)}.

    For each period, slices `data` to that date range -- the SAME
    convention run_recent_period_check() already uses (the period's own
    data only, NOT extended backward for warm-up, so the strategy's own
    min_lookback_days naturally consumes the front of each slice as
    warm-up, exactly like the existing recent-period check already
    behaves) -- then derives a feasible window count via the SAME frozen
    _feasible_window_count() the official recent-period check itself uses.

    Returns {label: {"exp_id": ..., "windows_used": ..., "requested_windows": ...,
    "available_trading_days": ...}} -- one entry per period, in `periods`'
    own iteration order.
    """
    results = {}
    for label, (period_start, period_end) in periods.items():
        period_data = {
            sym: df[(df.index.date >= period_start) & (df.index.date <= period_end)]
            for sym, df in data.items() if df is not None
        }
        trading_calendar = sorted({
            d for df in period_data.values() if df is not None and not df.empty for d in df.index.date
        })
        available_trading_days = len(trading_calendar)
        n_windows = _feasible_window_count(
            available_trading_days, getattr(strategy, "min_lookback_days", 0),
            requested_windows, min_tradeable_days_per_window,
        )

        exp_id = experiment_fn(
            data=period_data, start_date=period_start, end_date=period_end,
            starting_capital=starting_capital, n_walk_forward_windows=n_windows,
            narrative_api_key=narrative_api_key, **experiment_kwargs,
        )
        results[label] = {
            "exp_id": exp_id, "windows_used": n_windows, "requested_windows": requested_windows,
            "available_trading_days": available_trading_days,
        }
    return results
