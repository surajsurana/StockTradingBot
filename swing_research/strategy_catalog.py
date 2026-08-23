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
]
