"""
Strategy Catalog -- the single place a strategy declares how it plugs into
the paper-trading factory (run_paper_trading.py) and/or the research CLI
(run_swing_experiment.py), so adding a future strategy means appending one
entry here instead of editing either of those two scripts directly.

Added as part of the "self-registering strategy architecture" (item A of
the continuous strategy pipeline, approved 2026-08-23). Deliberately an
EXPLICIT list, not a filesystem auto-scan of swing_research/strategies/ --
this program's own conventions (see deployment/base.py, capital_winddown.py)
favor explicit, disclosed wiring over implicit/magic discovery, and an
auto-scan would import whatever happens to be dropped into that directory,
including a strategy someone is still mid-edit on.

Named "catalog", not "registry", to avoid confusion with the UNRELATED
deployment/state/strategy_registry.json / deployment_manager.py registry,
which tracks each strategy's ResearchVerdict/DeploymentStatus. This file
has nothing to do with that -- it only wires up CODE (which class, which
column-computation function, which experiment runner), never a verdict or
a deployment status.

Two independent lists, because the two consumers need different things:
  - run_paper_trading.py needs a Strategy factory + (for cross-sectional
    strategies) a compute_extra_columns_fn -- see PAPER_TRADING_STRATEGY_SPECS.
  - run_swing_experiment.py needs a display variant string + the
    research_director.run_*_experiment function to call -- see
    RESEARCH_EXPERIMENT_SPECS.
A strategy that has reached PAPER_TRADING typically appears in both lists
(a promoted strategy) or research-only in just the second (still under
research). PEAD is deliberately ABSENT from PAPER_TRADING_STRATEGY_SPECS --
it is event-driven, not a cross-sectional swing_research.base.Strategy, and
stays special-cased directly in run_paper_trading.py exactly as it was
before this file existed. Forcing it into this shape would be exactly the
kind of invented-rule-to-fit-a-mold this program's conventions warn against.

Every entry below is moved VERBATIM from where it lived before this file
existed (run_paper_trading.py's _STRATEGY_FACTORIES /
run_swing_experiment.py's _STRATEGY_VARIANTS + if/elif) -- no behavior
change, only relocation. runner_getter is a zero-arg lambda that performs
the same local import run_swing_experiment.py did inline, kept lazy so
importing this catalog's paper-trading half never pulls in the heavier
research_director.py module (run_paper_trading.py has no need for it).
"""

from dataclasses import dataclass
from typing import Callable, Optional


def _renamed(extra: dict, column_name: str) -> dict:
    """compute_*_percentile_ranks() returns {symbol: Series} where each
    Series' own .name is the SYMBOL (an artifact of slicing a wide-format
    DataFrame column-wise), not the feature name each strategy's
    precompute() looks for. deployment/paper_trading_engine.py's
    df.join(extra_columns[symbol]) uses the Series' .name as the joined
    column's name, so without this rename it silently joins a column named
    after the symbol instead of e.g. "rs_percentile" -- precompute() then
    never finds its expected column, treats the percentile as always NaN,
    and entry_signal_at() can never signal. Moved here verbatim from
    run_paper_trading.py, 2026-08-23 -- see that module's git history for
    the original 2026-08-17 bug this guards against."""
    return {symbol: series.rename(column_name) for symbol, series in extra.items()}


@dataclass
class PaperTradingStrategySpec:
    strategy_key: str
    display_name: str
    strategy_factory: Callable                      # zero-arg -> a swing_research.base.Strategy instance
    compute_extra_columns_fn: Optional[Callable] = None   # data (dict) -> {symbol: Series}, or None
    execution_config_factory: Optional[Callable] = None   # zero-arg -> a
    # deployment.paper_trading_engine.ExecutionRealismConfig, lazy (same reason
    # strategy_factory/compute_extra_columns_fn are lazy: keeps this module's own import graph free
    # of deployment/ at load time) -- or None to use run_paper_trading.py's own
    # _DEFAULT_EXECUTION_CONFIG. Added 2026-09-06 for Overnight Return Anomaly's promotion: its
    # research verdict (EXP-078) was validated under fill_timing="close_to_next_open", NOT the
    # platform default "next_day_open", so live paper trading must use that SAME config or it would
    # silently re-run under the exact contamination (an extra intraday session on the exit leg)
    # that made the strategy's FIRST research run (EXP-076) REJECT -- "we must not play with
    # strategy rules" applies to this mismatch as much as it did to a missing target_price
    # mechanism. None (every other strategy) is a complete no-op -- byte-identical to before this
    # field existed.


PAPER_TRADING_STRATEGY_SPECS = [
    PaperTradingStrategySpec(
        strategy_key="fifty_two_week_high_momentum",
        display_name="52-Week High Momentum",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.fifty_two_week_high_momentum", fromlist=["FiftyTwoWeekHighMomentumStrategy"]
        ).FiftyTwoWeekHighMomentumStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_52w_high_nearness_percentile_ranks"]
        ).compute_52w_high_nearness_percentile_ranks(data), "nearness_percentile"),
    ),
    PaperTradingStrategySpec(
        strategy_key="short_term_reversal",
        display_name="Short-Term Reversal",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.short_term_reversal", fromlist=["ShortTermReversalStrategy"]
        ).ShortTermReversalStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_short_term_reversal_percentile_ranks"]
        ).compute_short_term_reversal_percentile_ranks(data), "reversal_percentile"),
    ),
    PaperTradingStrategySpec(
        strategy_key="minervini_trend_template_filter",
        display_name="Minervini Trend Template Filter",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.minervini_trend_template_filter", fromlist=["MinerviniTrendTemplateFilterStrategy"]
        ).MinerviniTrendTemplateFilterStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_rs_percentile_ranks"]
        ).compute_rs_percentile_ranks(data), "rs_percentile"),
    ),
    PaperTradingStrategySpec(
        strategy_key="cross_sectional_momentum",
        display_name="Cross-Sectional Momentum",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.cross_sectional_momentum", fromlist=["CrossSectionalMomentumStrategy"]
        ).CrossSectionalMomentumStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_momentum_percentile_ranks"]
        ).compute_momentum_percentile_ranks(data), "momentum_percentile"),
    ),
    PaperTradingStrategySpec(
        strategy_key="max_effect",
        display_name="MAX Effect (Lottery-Demand Anomaly)",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.max_effect", fromlist=["MaxEffectStrategy"]
        ).MaxEffectStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_max_effect_percentile_ranks"]
        ).compute_max_effect_percentile_ranks(data), "max_effect_percentile"),
    ),
    PaperTradingStrategySpec(
        strategy_key="turn_of_month",
        display_name="Turn-of-the-Month Effect",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.turn_of_month", fromlist=["TurnOfMonthStrategy"]
        ).TurnOfMonthStrategy(),
        # No compute_extra_columns_fn -- no natural cross-sectional ranking signal exists for
        # this strategy (date-driven, not percentile-driven); candidate_ranking.py's
        # date-seeded tie-break is the sole ordering mechanism, same as Turtle System 2.
    ),
    PaperTradingStrategySpec(
        strategy_key="ma_pullback",
        display_name="Moving Average Pullback",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.ma_pullback", fromlist=["MaPullbackStrategy"]
        ).MaPullbackStrategy(),
        # No compute_extra_columns_fn -- a per-symbol pattern signal (uptrend + pullback +
        # bullish reaction), not a cross-sectional decile sort; same "no natural ranking
        # measure" situation as Turtle System 2/Turn-of-the-Month.
    ),
    PaperTradingStrategySpec(
        strategy_key="volume_backed_breakout",
        display_name="Volume-Backed Breakout",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.volume_backed_breakout_pool_a", fromlist=["VolumeBackedBreakoutPoolAStrategy"]
        ).VolumeBackedBreakoutPoolAStrategy(),
        # No compute_extra_columns_fn -- a per-symbol pattern signal (new N-day high + volume
        # confirmation), not a cross-sectional decile sort; same "no natural ranking measure"
        # situation as Turtle System 2/Turn-of-the-Month.
    ),
    PaperTradingStrategySpec(
        strategy_key="overnight_return_anomaly",
        display_name="Overnight Return Anomaly",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.overnight_return_anomaly", fromlist=["OvernightReturnAnomalyStrategy"]
        ).OvernightReturnAnomalyStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_overnight_return_percentile_ranks"]
        ).compute_overnight_return_percentile_ranks(data), "overnight_percentile"),
        execution_config_factory=lambda: __import__(
            "deployment.paper_trading_engine", fromlist=["ExecutionRealismConfig"]
        ).ExecutionRealismConfig(fill_timing="close_to_next_open"),
        # See PaperTradingStrategySpec.execution_config_factory's own docstring above -- this MUST
        # match the fill timing EXP-078 (PASS) was actually validated under, or live paper trading
        # would silently re-run under EXP-076's REJECTed, contaminated mechanics.
    ),
    PaperTradingStrategySpec(
        strategy_key="high_volume_return_premium",
        display_name="High-Volume Return Premium",
        strategy_factory=lambda: __import__(
            "swing_research.strategies.high_volume_return_premium", fromlist=["HighVolumeReturnPremiumStrategy"]
        ).HighVolumeReturnPremiumStrategy(),
        compute_extra_columns_fn=lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_volume_shock_percentile_ranks"]
        ).compute_volume_shock_percentile_ranks(data), "volume_shock_percentile"),
        # No execution_config_factory -- no fill-timing sensitivity was flagged for this strategy
        # (unlike Overnight Return Anomaly), so the platform default is the correct, validated basis
        # (EXP-080 was run with no execution-realism configuration, same as most other strategies).
    ),
    PaperTradingStrategySpec(
        strategy_key="earnings_announcement_premium",
        display_name="Earnings Announcement Premium",
        # Live construction differs from research in exactly one injected dependency: the NSE
        # holiday calendar, so "today is the month's last trading day" can be answered for the
        # final bar (see the strategy's IMPLEMENTATION ASSUMPTIONS item 5).
        strategy_factory=lambda: __import__(
            "swing_research.strategies.earnings_announcement_premium",
            fromlist=["EarningsAnnouncementPremiumStrategy"],
        ).EarningsAnnouncementPremiumStrategy(
            is_last_trading_day_fn=__import__(
                "deployment.nse_trading_calendar", fromlist=["is_last_trading_day_of_month"]
            ).is_last_trading_day_of_month,
        ),
        # Three feature columns per symbol (a DataFrame, joined like any Series) from the
        # announcement-date cache, refreshed in full once it is more than 30 days old so the
        # trailing-12-month announcement count keeps tracking newly-reported quarters.
        compute_extra_columns_fn=lambda data: __import__(
            "swing_research.announcement_features", fromlist=["compute_announcement_features_by_symbol"]
        ).compute_announcement_features_by_symbol(
            data,
            __import__("data.fetch_earnings_calendar", fromlist=["get_announcement_date_history"])
            .get_announcement_date_history(list(data.keys()), max_age_days=30),
        ),
        # No execution_config_factory: EXP-081 ran on the default same-day-close research engine,
        # so the platform default (next_day_open) applies live, same as High-Volume Return Premium.
    ),
]


@dataclass
class ResearchExperimentSpec:
    strategy_key: str
    variant_description: str
    runner_getter: Callable   # zero-arg -> the research_director.run_*_experiment function (not called here)


RESEARCH_EXPERIMENT_SPECS = [
    ResearchExperimentSpec(
        strategy_key="turtle_system2",
        variant_description="System 2 (55-day entry / 20-day exit, long-only)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_turtle_experiment"]
        ).run_turtle_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="minervini_trend_template_filter",
        variant_description="Trend Template Filter (8-criterion screen, disclosed mechanical entry trigger, no pyramiding)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_minervini_experiment"]
        ).run_minervini_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="52_week_high_momentum",
        variant_description="52-Week High Momentum (top-decile nearness percentile, K=6mo single-vintage, no percentile-based early exit)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_52_week_high_momentum_experiment"]
        ).run_52_week_high_momentum_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="cross_sectional_momentum",
        variant_description="Cross-Sectional Momentum (J=6mo formation, top-decile percentile, K=6mo single-vintage, no percentile-based early exit)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_cross_sectional_momentum_experiment"]
        ).run_cross_sectional_momentum_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="short_term_reversal",
        variant_description="Short-Term Reversal (1mo formation, bottom-decile percentile, 1mo single-vintage, no percentile-based early exit)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_short_term_reversal_experiment"]
        ).run_short_term_reversal_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="betting_against_beta",
        variant_description="Betting Against Beta (Frazzini-Pedersen shrunk beta, 1yr lookback, bottom-decile percentile, 1mo single-vintage, long-only unlevered)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_betting_against_beta_experiment"]
        ).run_betting_against_beta_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="amihud_illiquidity",
        variant_description="Amihud Illiquidity Premium (252d ILLIQ formation, top-decile percentile, 1mo single-vintage, EXECUTION-REALISTIC verdict: 5% ADV cap + ILLIQ-derived cost + next-day-open fills)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_amihud_experiment"]
        ).run_amihud_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="max_effect",
        variant_description="MAX Effect / Lottery-Demand Anomaly (MAX(1) trailing 1mo formation, bottom-decile percentile, 1mo single-vintage, no percentile-based early exit)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_max_effect_experiment"]
        ).run_max_effect_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="idiosyncratic_volatility",
        variant_description="Idiosyncratic Volatility Anomaly (single-factor market-model residual vol, 1mo formation, bottom-decile percentile, 1mo single-vintage, long-only)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_idiosyncratic_volatility_experiment"]
        ).run_idiosyncratic_volatility_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="turn_of_month",
        variant_description="Turn-of-the-Month Effect (last trading day of month entry, 3-trading-day hold, applied per-symbol universe-wide, no cross-sectional selection)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_turn_of_month_experiment"]
        ).run_turn_of_month_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="overnight_return_anomaly",
        variant_description="Overnight Return Anomaly (21-day cumulative Close-to-Open return, top-decile percentile, 1-trading-day single-vintage hold, close-to-next-open fills)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_overnight_return_experiment"]
        ).run_overnight_return_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="high_volume_return_premium",
        variant_description="High-Volume Return Premium (5-day recent / 252-day baseline volume shock, top-decile percentile, 1-month single-vintage hold)",
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_high_volume_return_premium_experiment"]
        ).run_high_volume_return_premium_experiment,
    ),
    ResearchExperimentSpec(
        strategy_key="earnings_announcement_premium",
        variant_description=("Earnings Announcement Premium (Frazzini-Lamont: buy expected announcers at the prior "
                             "month-end close, hold to month-end; ranked by 4-year volume concentration ratio, "
                             "lagged 3 months; long only)"),
        runner_getter=lambda: __import__(
            "swing_research.research_director", fromlist=["run_earnings_announcement_premium_experiment"]
        ).run_earnings_announcement_premium_experiment,
    ),
]
