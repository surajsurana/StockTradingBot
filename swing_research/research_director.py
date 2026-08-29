"""
Swing Research Program's Research Director -- orchestrates one full
experiment, reusing every genuinely reusable piece of research_lab/ (none
of these are intraday-specific, confirmed by direct inspection during
planning):

    swing_research.published_research_analyst   [structured record, not Claude-generated]
    -> swing_research.backtesting_engine.simulate_portfolio()   [new, multi-day-capable]
    -> research_lab.backtesting_engineer.walk_forward_split()   [imported, pure date-slicer]
    -> swing_research.metrics.compute_metrics()                 [new, compounding equity]
    -> research_lab.statistical_auditor.audit()                 [imported, deterministic, FINAL]
    -> research_lab.performance_analyst.explain()                [imported, Claude narrative only]
    -> research_lab.experiment_manager.save_experiment()         [imported, swing-specific paths]
    -> research_lab.knowledge_base                               [imported, swing-specific paths]

GOVERNANCE (same as research_lab's): the Auditor's PASS/REJECT is computed
BEFORE the narrative, from swing_research.metrics.compute_metrics() output
only -- nothing any LLM call in this pipeline produces can change it. The
Auditor runs on the STRATEGY UNDER TEST ONLY -- MA Crossover, Mean
Reversion, Buy & Hold, and the Nifty 500 index are comparison numbers, not
experiments under audit.

Generalized 2026-08-03 (Minervini implementation) from what was originally
Turtle-specific code, so every future strategy in the roadmap reuses one
pipeline instead of duplicating it.
"""

import os
from datetime import date
from typing import Callable, Optional

from research_lab import backtesting_engineer, experiment_manager, performance_analyst, statistical_auditor
from research_lab.knowledge_base import KNOWLEDGE_BASE_PATH as INTRADAY_KB_PATH  # noqa: F401 (documents the split)

from swing_research import benchmarks
from swing_research.backtesting_engine import simulate_portfolio, simulate_portfolio_single_unit
from swing_research.base import Strategy
from swing_research.evidence_quality import compute_evidence_quality
from swing_research.metrics import compute_holding_period_breakdown, compute_metrics
from swing_research.published_research_analyst import PublishedStrategy
from swing_research.universe import get_universe_metadata


def _sector_map_for_trades(bare_sector_map: dict) -> dict:
    """
    research_lab.performance_analyst.load_sector_map() returns
    {bare_symbol: sector} (e.g. "RELIANCE" -> "Oil Gas & Consumable
    Fuels"), but every Trade.symbol in this program carries the yfinance
    ".NS" suffix (e.g. "RELIANCE.NS") -- compute_sector_breakdown() does a
    direct dict lookup on t.symbol, so every trade was silently landing in
    "Unknown" (found during the 2026-08-03 Research Audit, confirmed via
    EXP-003's sector_breakdown showing 100% of P&L unattributed). Fixed
    here, entirely on the swing_research side -- research_lab's
    load_sector_map()/compute_sector_breakdown() are correct as written
    for research_lab's own (non-.NS-suffixed) symbol convention and are
    NOT modified.
    """
    return {f"{bare_symbol}.NS": sector for bare_symbol, sector in bare_sector_map.items()}


def _narrative_call_with_scope_disclosure(base_call_fn, published: PublishedStrategy):
    """
    Wraps whatever call_fn would otherwise go to
    research_lab.performance_analyst.explain() (real Claude call or a
    test's mock) so the actual narrative prompt -- built by
    build_narrative_prompt() from hypothesis_name + metrics alone, with no
    parameter for the fuller hypothesis/rules/scope-reduction text -- also
    carries this strategy's disclosed scope reductions and implementation
    assumptions. Without this, Claude has no way to know about any
    disclosed simplification and defaults to describing the textbook
    version from its own training knowledge -- confirmed as the actual
    root cause of an early Turtle narrative incorrectly claiming results
    reflected "both long and short breakouts" during the 2026-08-03
    Research Audit. research_lab.performance_analyst.explain() itself is
    not modified -- this only wraps the call_fn parameter it already accepts.
    """
    def wrapped(prompt: str) -> str:
        disclosed_prompt = (
            f"{prompt}\n\nIMPORTANT CONTEXT not otherwise conveyed by the metrics above -- "
            f"disclosed scope reductions: {published.scope_reductions}\n\n"
            f"Estimated impact of implementation assumptions on this result: {published.assumptions_impact}\n\n"
            f"Do not attribute results to anything outside these disclosed rules (e.g. short "
            f"positions, pyramiding, or any variant not explicitly described above)."
        )
        return base_call_fn(disclosed_prompt)

    return wrapped


SWING_EXPERIMENTS_DIR = os.path.join(os.path.dirname(__file__), "experiments")
SWING_KNOWLEDGE_BASE_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.jsonl")
SWING_CONCLUSIONS_PATH = os.path.join(os.path.dirname(__file__), "research_conclusions.jsonl")


def record_strategy_conclusion(conclusion_text: str, based_on_exp_ids: list,
                                path: str = SWING_CONCLUSIONS_PATH) -> None:
    """
    Records a synthesized, cross-experiment conclusion about a whole
    strategy (e.g. "Turtle showed historical strength but failed temporal
    robustness") -- distinct from the per-experiment verdicts
    save_experiment() already records automatically. Reuses
    research_lab.knowledge_base.record_conclusion() (imported, unmodified)
    pointed at this program's own conclusions file, exactly the same
    "generic function + swing-specific path" reuse pattern as every other
    research_lab import in this module.
    """
    from research_lab.knowledge_base import record_conclusion
    record_conclusion(conclusion_text, based_on_exp_ids, path=path)


def run_walk_forward_generic(strategy: Strategy, data: dict, starting_capital: float,
                              sector_map: dict, start_date: date, end_date: date,
                              n_walk_forward_windows: int = 3,
                              max_units_per_sector: int = 6, max_units_total: int = 10,
                              extra_columns_by_symbol: Optional[dict] = None,
                              min_trades_total: int = 15, min_out_of_sample_trades: int = 3,
                              min_consistent_window_fraction: float = 0.5) -> dict:
    """
    Splits [start_date, end_date] into n_walk_forward_windows sequential
    windows via research_lab.backtesting_engineer.walk_forward_split()
    (imported, unmodified -- a pure date-range slicer with no intraday
    assumption). The LAST window is the true out-of-sample holdout.

    Default trade-count thresholds are lower than research_lab's intraday
    defaults (30/5) -- swing breakout/screen signals fire far less often
    than intraday setups, so each window needs enough calendar time to
    produce a meaningful trade count. Same deterministic Auditor function,
    different thresholds for a genuinely different trade-frequency regime.

    extra_columns_by_symbol: passed straight through to simulate_portfolio()
    for strategies needing a cross-sectional precomputed column (e.g.
    Minervini's RS percentile) -- sliced per-window by the caller if the
    underlying data itself is windowed (the columns must be recomputed or
    resliced consistently with `data`'s own windowing; see
    run_generic_swing_experiment()'s handling below).
    """
    windows = backtesting_engineer.walk_forward_split(start_date, end_date, n_walk_forward_windows)
    walk_forward_metrics = []
    all_trades_by_window = []

    for w_start, w_end in windows:
        windowed_data = {
            sym: df[(df.index.date >= w_start) & (df.index.date <= w_end)]
            for sym, df in data.items()
        }
        windowed_extra = None
        if extra_columns_by_symbol:
            windowed_extra = {
                sym: series[(series.index.date >= w_start) & (series.index.date <= w_end)]
                for sym, series in extra_columns_by_symbol.items()
            }
        result = simulate_portfolio(
            windowed_data, strategy, starting_capital, sector_map=sector_map,
            max_units_per_sector=max_units_per_sector, max_units_total=max_units_total,
            extra_columns_by_symbol=windowed_extra,
        )
        metrics = compute_metrics(result["trades"], starting_capital, result["trading_calendar"],
                                   daily_equity=result["daily_equity"])
        walk_forward_metrics.append(metrics)
        all_trades_by_window.append(result["trades"])

    out_of_sample_metrics = walk_forward_metrics[-1] if walk_forward_metrics else {}
    consistency_metrics = walk_forward_metrics[:-1]
    out_of_sample_trades = all_trades_by_window[-1] if all_trades_by_window else []
    all_trades = [t for trades in all_trades_by_window for t in trades]

    verdict = statistical_auditor.audit(
        consistency_metrics, out_of_sample_metrics,
        min_trades_total=min_trades_total, min_out_of_sample_trades=min_out_of_sample_trades,
        min_consistent_window_fraction=min_consistent_window_fraction,
    )

    return {
        "verdict": verdict, "walk_forward_metrics": consistency_metrics,
        "out_of_sample_metrics": out_of_sample_metrics, "out_of_sample_trades": out_of_sample_trades,
        "all_trades": all_trades, "windows": windows,
    }


def run_generic_swing_experiment(strategy: Strategy, published: PublishedStrategy, data: dict,
                                  start_date: date, end_date: date, starting_capital: float = 1_000_000,
                                  n_walk_forward_windows: int = 3,
                                  extra_columns_by_symbol: Optional[dict] = None,
                                  narrative_api_key: str = "",
                                  narrative_call_fn: Optional[Callable[[str], str]] = None,
                                  experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                  knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                  skip_regime_breakdown: bool = False,
                                  extra_parameters: Optional[dict] = None,
                                  walk_forward_fn: Callable = None,
                                  full_period_trade_adjuster: Optional[Callable[[list, dict], list]] = None) -> str:
    """
    Full pipeline for one experiment run, for ANY swing_research.base.Strategy:
    walk-forward + audit on the strategy under test, then MA Crossover /
    Mean Reversion / Buy & Hold / Nifty 500 index computed on the SAME
    data/period/universe for the comparison table, all saved into one
    permanent experiment record.

    data: {symbol: DataFrame of daily OHLCV bars} for the full universe
    (see swing_research/universe.py), already fetched by the caller via
    data/fetch_historical.fetch_all() (read-only reuse).
    extra_columns_by_symbol: for strategies needing a cross-sectional
    precomputed column (e.g. Minervini's RS percentile,
    swing_research/cross_sectional.py) -- None for strategies that don't.
    extra_parameters: strategy-specific values to fold into the saved
    experiment's parameters.json (e.g. Turtle's unit-cap values, Minervini's
    RS threshold) -- kept generic here so this function doesn't need to
    know about any one strategy's specific knobs.

    walk_forward_fn: defaults to run_walk_forward_generic (unchanged
    behavior for every strategy that doesn't pass this) -- ADDED 2026-08-16
    so Amihud (SW-010) can swap in
    swing_research.execution_realism_engine.run_walk_forward_execution_realistic
    (pre-bound via functools.partial with its own volume-cap/illiquidity-
    cost/fill-timing parameters) instead, making the ACCEPTANCE VERDICT
    itself reflect execution-realism adjustments rather than a zero-cost,
    same-day-close backtest. Purely additive -- every prior strategy's
    call site omits this parameter and gets IDENTICAL behavior to before
    this change (verified by re-running an existing strategy's smoke test
    and confirming byte-identical output).
    full_period_trade_adjuster: if provided, applied to the continuous
    full-period run's trades (via ADDED 2026-08-16 for the same reason as
    walk_forward_fn above) BEFORE computing the comparison-table's
    headline strategy_metrics -- the RAW (unadjusted) full-period metrics
    are then saved separately under a "diagnostic_zero_cost_baseline" key
    in the experiment's metrics, never used for the audit, purely for
    transparency (see execution_realism_framework_proposal.md).
    """
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.market_regime import build_regime_series
    from data.fetch_historical import fetch_nifty

    if walk_forward_fn is None:
        walk_forward_fn = run_walk_forward_generic

    sector_map = _sector_map_for_trades(performance_analyst.load_sector_map())

    # --- Strategy under test: walk-forward + audit ---
    result = walk_forward_fn(
        strategy, data, starting_capital, sector_map, start_date, end_date, n_walk_forward_windows,
        extra_columns_by_symbol=extra_columns_by_symbol,
    )
    verdict = result["verdict"]
    calendar_all = sorted({d for df in data.values() for d in df.index.date})

    # A SEPARATE, continuous (non-windowed) full-period run, used ONLY for
    # the comparison-table metrics and breakdowns -- NOT for the audit
    # (that stays strictly on the windowed walk-forward/OOS trades above,
    # unchanged). Genuinely necessary, not redundant: each walk-forward
    # window resets to starting_capital independently (by design, so the
    # Auditor can judge each window on its own footing), so concatenating
    # windowed trades can never produce one coherent equity curve. This
    # engine deliberately tracks REAL compounding equity (see
    # backtesting_engine.py's module docstring) rather than research_lab's
    # fixed-starting-capital simplification, so getting a meaningful
    # CAGR/Sharpe/Sortino/max-drawdown requires actually re-running the
    # strategy continuously across the full period once.
    full_result = simulate_portfolio(data, strategy, starting_capital, sector_map=sector_map,
                                      extra_columns_by_symbol=extra_columns_by_symbol)

    diagnostic_zero_cost_baseline = None
    full_trades = full_result["trades"]
    full_daily_equity = full_result["daily_equity"]
    if full_period_trade_adjuster is not None:
        diagnostic_zero_cost_baseline = compute_metrics(
            full_trades, starting_capital, full_result["trading_calendar"], daily_equity=full_daily_equity,
        )
        from swing_research.execution_realism_engine import build_approximate_daily_equity
        full_trades = full_period_trade_adjuster(full_trades, data)
        full_daily_equity = build_approximate_daily_equity(full_trades, starting_capital, full_result["trading_calendar"])

    strategy_metrics = compute_metrics(full_trades, starting_capital,
                                        full_result["trading_calendar"], daily_equity=full_daily_equity)
    strategy_sector_breakdown = performance_analyst.compute_sector_breakdown(full_trades, sector_map)
    strategy_holding_breakdown = compute_holding_period_breakdown(full_trades)

    # One Nifty fetch, reused for both the regime breakdown and the MA
    # Crossover benchmark's own regime gate -- avoids fetching the same
    # index history twice.
    nifty_regime = None
    if not skip_regime_breakdown:
        try:
            nifty = fetch_nifty(period="max")
            nifty_regime = build_regime_series(nifty)
        except Exception as e:
            print(f"WARNING: could not fetch Nifty regime series: {e}")

    regime_breakdown = {}
    if nifty_regime is not None:
        regime_breakdown = performance_analyst.compute_regime_breakdown(full_trades, nifty_regime)

    # --- Benchmarks: same data, same period, SAME STARTING CAPITAL POOL,
    # each under its own real, disclosed capital/portfolio discipline ---
    #
    # Research Audit fix (2026-08-03): MA Crossover and Mean Reversion each
    # get ONE shared starting_capital pool via simulate_portfolio_single_unit(),
    # sized under production's REAL RISK_PER_TRADE_PCT / MAX_OPEN_POSITIONS /
    # MAX_DEPLOYED_CAPITAL_PCT / MAX_CAPITAL_PER_TRADE_PCT /
    # DAILY_LOSS_CIRCUIT_BREAKER_PCT (config/settings.py values,
    # reimplemented read-only) -- not each independently given unlimited
    # aggregate capital, which was the original comparison-fairness bug.
    # The strategy under test keeps ITS OWN documented portfolio
    # discipline (that IS its published methodology) -- the fairness
    # principle is comparable rigor, not identical numeric caps.
    #
    # Buy & Hold stays capital-DIVIDED across the universe on purpose: a
    # real passive investor does split one fixed pool across many stocks
    # simultaneously, unlike the two active single-position-at-a-time
    # strategies above.
    buy_hold_capital_per_symbol = starting_capital / max(len(data), 1)

    ma_result = simulate_portfolio_single_unit(
        data, MACrossoverStrategy, starting_capital, regime_series=nifty_regime,
    )
    mr_result = simulate_portfolio_single_unit(
        data, MeanReversionStrategy, starting_capital, regime_series=None,
    )
    ma_metrics = compute_metrics(ma_result["trades"], starting_capital, ma_result["trading_calendar"],
                                  daily_equity=ma_result["daily_equity"])
    mr_metrics = compute_metrics(mr_result["trades"], starting_capital, mr_result["trading_calendar"],
                                  daily_equity=mr_result["daily_equity"])

    buy_hold_result = benchmarks.simulate_buy_and_hold(data, buy_hold_capital_per_symbol)
    buy_hold_metrics = compute_metrics(buy_hold_result["trades"], buy_hold_result["starting_capital"],
                                        buy_hold_result["trading_calendar"], daily_equity=buy_hold_result["daily_equity"])

    if skip_regime_breakdown:
        # Same "avoid a real network call" convention as skip_regime_breakdown
        # above -- reused for the index fetch rather than a second flag,
        # since both are "skip the real Nifty network dependency" for tests.
        index_ticker, index_metrics = "skipped", {"cagr": None, "sharpe_ratio": None,
                                                    "sortino_ratio": None, "max_drawdown_pct": None}
    else:
        index_ticker, index_df = benchmarks.get_index_benchmark_series(period="max")
        index_metrics = benchmarks.compute_index_benchmark_metrics(index_df)

    comparison = {
        published.name.lower().replace(" ", "_").replace("-", ""): strategy_metrics,
        "ma_crossover_production": ma_metrics,
        "mean_reversion_production": mr_metrics,
        "buy_and_hold": buy_hold_metrics,
        f"index_benchmark_{index_ticker}": index_metrics,
    }

    if narrative_call_fn is not None:
        base_call_fn = narrative_call_fn
    else:
        from news.news_agent import call_claude
        base_call_fn = lambda p: call_claude(p, narrative_api_key, max_tokens=2048)

    narrative = performance_analyst.explain(
        published.name, {"decision": verdict.decision, "reasoning": verdict.reasoning},
        strategy_metrics, strategy_sector_breakdown, {}, regime_breakdown,
        api_key=narrative_api_key,
        call_fn=_narrative_call_with_scope_disclosure(base_call_fn, published),
    )

    universe_meta = get_universe_metadata()

    # Evidence-quality (confidence) score -- added 2026-08-04, per explicit
    # direction immediately after Minervini's INCONCLUSIVE verdict.
    # Deliberately computed from the SAME trade counts the Statistical
    # Auditor's own decision used (verdict.checks), plus the actual
    # walk-forward window count and data coverage -- see
    # swing_research/evidence_quality.py's module docstring for why this
    # is kept strictly outcome-blind (never a function of the verdict
    # itself or the metrics' sign/magnitude). Supplements, does not
    # replace, the PASS/REJECT/INCONCLUSIVE decision below.
    evidence_quality = compute_evidence_quality(
        total_trades=verdict.checks.get("total_trades", 0),
        out_of_sample_trades=verdict.checks.get("out_of_sample_trades", 0),
        windows_used=n_walk_forward_windows,
        available_trading_days=len(calendar_all),
        min_lookback_days=getattr(strategy, "min_lookback_days", 0),
    )

    exp_id = experiment_manager.next_experiment_id(experiments_dir)
    experiment_manager.save_experiment(
        exp_id=exp_id,
        hypothesis={
            "name": published.name, "mechanism": published.mechanism,
            "rationale": f"{published.variant_chosen}\n\n{published.scope_reductions}",
            "rules": published.rules,
            "selection_reasoning": "User-selected from the published-swing-research candidate report.",
        },
        parameters={
            "starting_capital": starting_capital, "universe_version": universe_meta["version"],
            "universe_symbol_count": universe_meta["symbol_count"], "n_walk_forward_windows": n_walk_forward_windows,
            "benchmark_capital_model": (
                "MA Crossover / Mean Reversion: ONE shared starting_capital pool, sized under "
                "production's real RISK_PER_TRADE_PCT=0.01 / MAX_OPEN_POSITIONS=10 / "
                "MAX_DEPLOYED_CAPITAL_PCT=0.60 / MAX_CAPITAL_PER_TRADE_PCT=0.12 / "
                "DAILY_LOSS_CIRCUIT_BREAKER_PCT=0.03 (config/settings.py values, reimplemented "
                "read-only in simulate_portfolio_single_unit()). Buy & Hold: starting_capital "
                "divided equally across the universe (a real passive-investor allocation model)."
            ),
            "transaction_costs_modeled": False, "slippage_modeled": False,
            "assumptions_impact": published.assumptions_impact,
            **(extra_parameters or {}),
        },
        data_period=f"{start_date} to {end_date}",
        metrics={
            **strategy_metrics, "walk_forward_metrics": result["walk_forward_metrics"],
            "out_of_sample_metrics": result["out_of_sample_metrics"], "audit_checks": verdict.checks,
            "sector_breakdown": strategy_sector_breakdown, "holding_period_breakdown": strategy_holding_breakdown,
            "regime_breakdown": regime_breakdown, "comparison_vs_benchmarks": comparison,
            "evidence_quality": evidence_quality,
            # Only present when full_period_trade_adjuster was used (added
            # 2026-08-16 for Amihud/SW-010) -- the RAW, zero-cost,
            # same-day-close comparison, reported for transparency ONLY.
            # The verdict/audit above was never computed from this.
            **({"diagnostic_zero_cost_baseline": diagnostic_zero_cost_baseline}
               if diagnostic_zero_cost_baseline is not None else {}),
            **({"walk_forward_diagnostic_zero_cost_metrics": result["diagnostic_walk_forward_metrics_zero_cost"]}
               if "diagnostic_walk_forward_metrics_zero_cost" in result else {}),
        },
        observations=narrative,
        verdict={"decision": verdict.decision, "reasoning": verdict.reasoning},
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
    )
    return exp_id


def run_turtle_experiment(data: dict, start_date: date, end_date: date,
                          starting_capital: float = 1_000_000, n_walk_forward_windows: int = 3,
                          narrative_api_key: str = "", narrative_call_fn: Optional[Callable[[str], str]] = None,
                          experiments_dir: str = SWING_EXPERIMENTS_DIR,
                          knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                          skip_regime_breakdown: bool = False) -> str:
    """Thin wrapper over run_generic_swing_experiment() for Turtle System 2."""
    from swing_research.strategies.turtle_system2 import TurtleSystem2Strategy
    from swing_research.published_research_analyst import TURTLE_SYSTEM_2

    turtle = TurtleSystem2Strategy()
    return run_generic_swing_experiment(
        turtle, TURTLE_SYSTEM_2, data, start_date, end_date, starting_capital, n_walk_forward_windows,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "turtle_max_units_per_symbol": turtle.max_units, "turtle_risk_pct_per_unit": turtle.risk_pct_per_unit,
            "turtle_max_units_per_sector": 6, "turtle_max_units_total": 10,
        },
    )


def run_minervini_experiment(data: dict, start_date: date, end_date: date,
                              starting_capital: float = 1_000_000, n_walk_forward_windows: int = 3,
                              narrative_api_key: str = "", narrative_call_fn: Optional[Callable[[str], str]] = None,
                              experiments_dir: str = SWING_EXPERIMENTS_DIR,
                              knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                              skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for the Minervini
    Trend Template Filter -- the extra step vs. Turtle is computing the
    cross-sectional RS percentile ranks ONCE up front (they don't depend on
    the walk-forward windowing itself, only on the data available up to
    each date, so a single computation over the full `data` is correct and
    reused across every window by run_walk_forward_generic()'s own
    per-window slicing of extra_columns_by_symbol).
    """
    from swing_research.strategies.minervini_trend_template_filter import MinerviniTrendTemplateFilterStrategy
    from swing_research.published_research_analyst import MINERVINI_TREND_TEMPLATE_FILTER
    from swing_research.cross_sectional import compute_rs_percentile_ranks

    rs_percentiles = compute_rs_percentile_ranks(data)
    extra_columns = {symbol: series.rename("rs_percentile") for symbol, series in rs_percentiles.items()}

    minervini = MinerviniTrendTemplateFilterStrategy()
    return run_generic_swing_experiment(
        minervini, MINERVINI_TREND_TEMPLATE_FILTER, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "minervini_risk_pct_per_unit": minervini.risk_pct_per_unit,
            "minervini_stop_loss_pct": 0.08, "minervini_rs_percentile_threshold": 70.0,
            "minervini_pyramiding": False,
        },
    )


def run_52_week_high_momentum_experiment(data: dict, start_date: date, end_date: date,
                                          starting_capital: float = 1_000_000, n_walk_forward_windows: int = 3,
                                          narrative_api_key: str = "",
                                          narrative_call_fn: Optional[Callable[[str], str]] = None,
                                          experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                          knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                          skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for 52-Week High
    Momentum -- computes the 52-week-high nearness cross-sectional
    percentile ONCE up front (same pattern as Minervini's RS percentile),
    reused across every walk-forward window by run_walk_forward_generic()'s
    own per-window slicing of extra_columns_by_symbol.
    """
    from swing_research.strategies.fifty_two_week_high_momentum import FiftyTwoWeekHighMomentumStrategy
    from swing_research.published_research_analyst import FIFTY_TWO_WEEK_HIGH_MOMENTUM
    from swing_research.cross_sectional import compute_52w_high_nearness_percentile_ranks

    nearness_percentiles = compute_52w_high_nearness_percentile_ranks(data)
    extra_columns = {symbol: series.rename("nearness_percentile") for symbol, series in nearness_percentiles.items()}

    strategy = FiftyTwoWeekHighMomentumStrategy()
    return run_generic_swing_experiment(
        strategy, FIFTY_TWO_WEEK_HIGH_MOMENTUM, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "fifty_two_week_high_momentum_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "fifty_two_week_high_momentum_stop_loss_pct": 0.08,
            "fifty_two_week_high_momentum_nearness_percentile_threshold": 90.0,
            "fifty_two_week_high_momentum_holding_period_trading_days": 126,
            "fifty_two_week_high_momentum_single_vintage": True,
            "fifty_two_week_high_momentum_percentile_based_early_exit": False,
        },
    )


def run_cross_sectional_momentum_experiment(data: dict, start_date: date, end_date: date,
                                             starting_capital: float = 1_000_000,
                                             n_walk_forward_windows: int = 3,
                                             narrative_api_key: str = "",
                                             narrative_call_fn: Optional[Callable[[str], str]] = None,
                                             experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                             knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                             skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for Cross-Sectional
    Momentum -- computes the J=6-month formation-return cross-sectional
    percentile ONCE up front (same pattern as Minervini's RS percentile
    and 52-Week High Momentum's nearness percentile), reused across every
    walk-forward window by run_walk_forward_generic()'s own per-window
    slicing of extra_columns_by_symbol.
    """
    from swing_research.strategies.cross_sectional_momentum import CrossSectionalMomentumStrategy
    from swing_research.published_research_analyst import CROSS_SECTIONAL_MOMENTUM
    from swing_research.cross_sectional import compute_momentum_percentile_ranks

    momentum_percentiles = compute_momentum_percentile_ranks(data)
    extra_columns = {symbol: series.rename("momentum_percentile") for symbol, series in momentum_percentiles.items()}

    strategy = CrossSectionalMomentumStrategy()
    return run_generic_swing_experiment(
        strategy, CROSS_SECTIONAL_MOMENTUM, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "cross_sectional_momentum_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "cross_sectional_momentum_stop_loss_pct": 0.08,
            "cross_sectional_momentum_percentile_threshold": 90.0,
            "cross_sectional_momentum_formation_days": 126,
            "cross_sectional_momentum_holding_period_trading_days": 126,
            "cross_sectional_momentum_single_vintage": True,
            "cross_sectional_momentum_skip_period": False,
            "cross_sectional_momentum_percentile_based_early_exit": False,
        },
    )


def run_amihud_experiment(data: dict, start_date: date, end_date: date,
                          starting_capital: float = 1_000_000,
                          n_walk_forward_windows: int = 3,
                          narrative_api_key: str = "",
                          narrative_call_fn: Optional[Callable[[str], str]] = None,
                          experiments_dir: str = SWING_EXPERIMENTS_DIR,
                          knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                          skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for the Amihud
    Illiquidity Premium -- computes the 252-day ILLIQ cross-sectional
    percentile ONCE up front (same pattern as every prior cross-sectional
    strategy), reused across every walk-forward window.

    UNLIKE every prior strategy: passes walk_forward_fn=
    execution_realism_engine.run_walk_forward_execution_realistic (instead
    of the default run_walk_forward_generic) and a
    full_period_trade_adjuster, both pre-bound with the SAME fixed,
    pre-declared configuration (5% trailing-20-day-ADV position cap,
    ILLIQ-derived cost calibrated ONCE from this run's own data via
    execution_realism_engine.calibrate_illiq_cost_k()'s default anchor,
    next-day-open fills) -- so the ACCEPTANCE VERDICT and the headline
    comparison-table metrics both reflect execution-realism, not a
    zero-cost, same-day-close backtest. The raw zero-cost numbers are
    still computed and saved (see run_generic_swing_experiment()'s
    "diagnostic_zero_cost_baseline" / "walk_forward_diagnostic_zero_cost_metrics"
    keys) but are explicitly NOT used for the verdict. illiq_cost_k is
    calibrated once here and reused identically for every window and the
    full-period run -- never re-tuned based on any result.
    """
    from functools import partial

    from swing_research.strategies.amihud_illiquidity import AmihudIlliquidityStrategy
    from swing_research.published_research_analyst import AMIHUD_ILLIQUIDITY_PREMIUM
    from swing_research.cross_sectional import compute_amihud_illiq_percentile_ranks
    from swing_research.execution_realism_engine import (
        apply_execution_realism, calibrate_illiq_cost_k, run_walk_forward_execution_realistic,
    )

    illiq_percentiles = compute_amihud_illiq_percentile_ranks(data)
    extra_columns = {symbol: series.rename("illiq_percentile") for symbol, series in illiq_percentiles.items()}

    illiq_cost_k = calibrate_illiq_cost_k(data)
    execution_realism_kwargs = dict(max_participation_pct_of_adv=0.05, illiq_cost_k=illiq_cost_k,
                                     fill_timing="next_day_open")

    walk_forward_fn = partial(run_walk_forward_execution_realistic, **execution_realism_kwargs)

    def full_period_trade_adjuster(trades: list, full_data: dict) -> list:
        return apply_execution_realism(trades, full_data, **execution_realism_kwargs)["trades"]

    strategy = AmihudIlliquidityStrategy()
    return run_generic_swing_experiment(
        strategy, AMIHUD_ILLIQUIDITY_PREMIUM, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        walk_forward_fn=walk_forward_fn, full_period_trade_adjuster=full_period_trade_adjuster,
        extra_parameters={
            "amihud_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "amihud_stop_loss_pct": 0.08,
            "amihud_illiq_percentile_threshold": 90.0,
            "amihud_formation_days": 252,
            "amihud_holding_period_trading_days": 21,
            "amihud_single_vintage": True,
            "amihud_percentile_based_early_exit": False,
            "transaction_costs_modeled": True, "slippage_modeled": True,
            "execution_realism_config": {
                "max_participation_pct_of_adv": 0.05, "illiq_cost_k": illiq_cost_k,
                "illiq_cost_calibration_anchor": "10bps one-way at Rs.100,000 for a median-ILLIQ universe stock",
                "fill_timing": "next_day_open",
                "methodology_note": (
                    "See execution_realism_framework_proposal.md and execution_realism_engine.py. "
                    "Verdict and headline comparison-table metrics both reflect this configuration; "
                    "the zero-cost baseline is saved separately for transparency only, under "
                    "diagnostic_zero_cost_baseline / walk_forward_diagnostic_zero_cost_metrics."
                ),
            },
        },
    )


def run_betting_against_beta_experiment(data: dict, start_date: date, end_date: date,
                                         starting_capital: float = 1_000_000,
                                         n_walk_forward_windows: int = 3,
                                         narrative_api_key: str = "",
                                         narrative_call_fn: Optional[Callable[[str], str]] = None,
                                         experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                         knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                         skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for Betting Against
    Beta -- computes the shrunk-beta cross-sectional percentile ONCE up
    front (same pattern as every prior cross-sectional strategy), reused
    across every walk-forward window by run_walk_forward_generic()'s own
    per-window slicing of extra_columns_by_symbol.

    First strategy in this program needing an EXTERNAL market-index series
    (Nifty 50) as an input to its own cross-sectional signal, not just each
    symbol's own price history -- fetched here via data.fetch_historical.fetch_nifty()
    (period="max", read-only reuse, same source already used for the regime
    gate/breakdown elsewhere in this function), independent of whatever
    period `data` itself covers (compute_shrunk_beta_percentile_ranks()
    reindexes the market series to each symbol's own dates, so a shorter
    `data` window -- e.g. the recent-period check's 3-year slice -- is
    handled correctly without a second, differently-windowed Nifty fetch).
    """
    from swing_research.strategies.betting_against_beta import BettingAgainstBetaStrategy
    from swing_research.published_research_analyst import BETTING_AGAINST_BETA
    from swing_research.cross_sectional import compute_shrunk_beta_percentile_ranks
    from data.fetch_historical import fetch_nifty

    nifty = fetch_nifty(period="max")
    beta_percentiles = compute_shrunk_beta_percentile_ranks(data, nifty["Close"])
    extra_columns = {symbol: series.rename("beta_percentile") for symbol, series in beta_percentiles.items()}

    strategy = BettingAgainstBetaStrategy()
    return run_generic_swing_experiment(
        strategy, BETTING_AGAINST_BETA, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "betting_against_beta_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "betting_against_beta_stop_loss_pct": 0.08,
            "betting_against_beta_percentile_threshold": 10.0,
            "betting_against_beta_lookback_days": 252,
            "betting_against_beta_correlation_return_lag_days": 3,
            "betting_against_beta_shrinkage_weight": 0.6,
            "betting_against_beta_holding_period_trading_days": 21,
            "betting_against_beta_single_vintage": True,
            "betting_against_beta_percentile_based_early_exit": False,
        },
    )


def run_short_term_reversal_experiment(data: dict, start_date: date, end_date: date,
                                        starting_capital: float = 1_000_000,
                                        n_walk_forward_windows: int = 3,
                                        narrative_api_key: str = "",
                                        narrative_call_fn: Optional[Callable[[str], str]] = None,
                                        experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                        knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                        skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for Short-Term
    Reversal -- computes the 1-month formation-return cross-sectional
    percentile ONCE up front (same pattern as every prior cross-sectional
    strategy), reused across every walk-forward window by
    run_walk_forward_generic()'s own per-window slicing of
    extra_columns_by_symbol.
    """
    from swing_research.strategies.short_term_reversal import ShortTermReversalStrategy
    from swing_research.published_research_analyst import SHORT_TERM_REVERSAL
    from swing_research.cross_sectional import compute_short_term_reversal_percentile_ranks

    reversal_percentiles = compute_short_term_reversal_percentile_ranks(data)
    extra_columns = {symbol: series.rename("reversal_percentile") for symbol, series in reversal_percentiles.items()}

    strategy = ShortTermReversalStrategy()
    return run_generic_swing_experiment(
        strategy, SHORT_TERM_REVERSAL, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "short_term_reversal_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "short_term_reversal_stop_loss_pct": 0.08,
            "short_term_reversal_percentile_threshold": 10.0,
            "short_term_reversal_formation_days": 21,
            "short_term_reversal_holding_period_trading_days": 21,
            "short_term_reversal_single_vintage": True,
            "short_term_reversal_percentile_based_early_exit": False,
        },
    )


def run_max_effect_experiment(data: dict, start_date: date, end_date: date,
                               starting_capital: float = 1_000_000,
                               n_walk_forward_windows: int = 3,
                               narrative_api_key: str = "",
                               narrative_call_fn: Optional[Callable[[str], str]] = None,
                               experiments_dir: str = SWING_EXPERIMENTS_DIR,
                               knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                               skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for MAX Effect
    (Lottery-Demand Anomaly) -- computes the 1-month trailing MAX(1)
    cross-sectional percentile ONCE up front (same pattern as every prior
    cross-sectional strategy), reused across every walk-forward window by
    run_walk_forward_generic()'s own per-window slicing of
    extra_columns_by_symbol.
    """
    from swing_research.strategies.max_effect import MaxEffectStrategy
    from swing_research.published_research_analyst import MAX_EFFECT
    from swing_research.cross_sectional import compute_max_effect_percentile_ranks

    max_effect_percentiles = compute_max_effect_percentile_ranks(data)
    extra_columns = {symbol: series.rename("max_effect_percentile") for symbol, series in max_effect_percentiles.items()}

    strategy = MaxEffectStrategy()
    return run_generic_swing_experiment(
        strategy, MAX_EFFECT, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "max_effect_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "max_effect_stop_loss_pct": 0.08,
            "max_effect_percentile_threshold": 10.0,
            "max_effect_formation_days": 21,
            "max_effect_holding_period_trading_days": 21,
            "max_effect_single_vintage": True,
            "max_effect_percentile_based_early_exit": False,
            "max_effect_variant": "MAX(1) -- single highest daily return in trailing month",
        },
    )


def run_idiosyncratic_volatility_experiment(data: dict, start_date: date, end_date: date,
                                             starting_capital: float = 1_000_000,
                                             n_walk_forward_windows: int = 3,
                                             narrative_api_key: str = "",
                                             narrative_call_fn: Optional[Callable[[str], str]] = None,
                                             experiments_dir: str = SWING_EXPERIMENTS_DIR,
                                             knowledge_base_path: str = SWING_KNOWLEDGE_BASE_PATH,
                                             skip_regime_breakdown: bool = False) -> str:
    """
    Thin wrapper over run_generic_swing_experiment() for the Idiosyncratic
    Volatility Anomaly -- computes the 1-month trailing single-factor
    residual-volatility cross-sectional percentile ONCE up front (same
    pattern as every prior cross-sectional strategy), reused across every
    walk-forward window by run_walk_forward_generic()'s own per-window
    slicing of extra_columns_by_symbol.

    Second strategy in this program (after Betting Against Beta) needing an
    EXTERNAL market-index series (Nifty 50) as an input to its own
    cross-sectional signal -- fetched here via
    data.fetch_historical.fetch_nifty() (period="max", read-only reuse,
    same source already used for the regime gate/breakdown and for Betting
    Against Beta elsewhere in this module), independent of whatever period
    `data` itself covers (compute_idiosyncratic_volatility_percentile_ranks()
    reindexes the market series to each symbol's own dates, so a shorter
    `data` window -- e.g. the recent-period check's 3-year slice -- is
    handled correctly without a second, differently-windowed Nifty fetch).
    """
    from swing_research.strategies.idiosyncratic_volatility import IdiosyncraticVolatilityStrategy
    from swing_research.published_research_analyst import IDIOSYNCRATIC_VOLATILITY_ANOMALY
    from swing_research.cross_sectional import compute_idiosyncratic_volatility_percentile_ranks
    from data.fetch_historical import fetch_nifty

    nifty = fetch_nifty(period="max")
    idio_vol_percentiles = compute_idiosyncratic_volatility_percentile_ranks(data, nifty["Close"])
    extra_columns = {symbol: series.rename("idio_vol_percentile") for symbol, series in idio_vol_percentiles.items()}

    strategy = IdiosyncraticVolatilityStrategy()
    return run_generic_swing_experiment(
        strategy, IDIOSYNCRATIC_VOLATILITY_ANOMALY, data, start_date, end_date, starting_capital,
        n_walk_forward_windows, extra_columns_by_symbol=extra_columns,
        narrative_api_key=narrative_api_key, narrative_call_fn=narrative_call_fn,
        experiments_dir=experiments_dir, knowledge_base_path=knowledge_base_path,
        skip_regime_breakdown=skip_regime_breakdown,
        extra_parameters={
            "idiosyncratic_volatility_risk_pct_per_unit": strategy.risk_pct_per_unit,
            "idiosyncratic_volatility_stop_loss_pct": 0.08,
            "idiosyncratic_volatility_percentile_threshold": 10.0,
            "idiosyncratic_volatility_formation_days": 21,
            "idiosyncratic_volatility_holding_period_trading_days": 21,
            "idiosyncratic_volatility_single_vintage": True,
            "idiosyncratic_volatility_percentile_based_early_exit": False,
            "idiosyncratic_volatility_factor_model": "single-factor (CAPM/market-model) residual, "
                                                       "not the paper's primary 3-factor construction",
        },
    )
