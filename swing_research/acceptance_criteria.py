"""
Standing acceptance criteria for the Swing Research Program -- set
2026-08-03, after Turtle Trading's robustness analysis showed a strategy
can look genuinely profitable over a long backtest while having already
stopped working in the years that matter most for a forward-looking
decision. This is not Turtle-specific: it applies to every future
strategy in the roadmap (Minervini Trend Template, 52-Week High Momentum,
Cross-Sectional Momentum, PEAD, ...).

No strategy is eligible for paper trading unless BOTH hold:
  1. Positive out-of-sample performance (the existing Statistical Auditor
     gate: research_lab.statistical_auditor.audit()'s out-of-sample
     expectancy check).
  2. Robustness in the MOST RECENT market period specifically -- tested by
     running the walk-forward/OOS pipeline on a recent-period-only slice
     (not just as part of a longer full-history window, where an early
     strong era can carry an overall PASS while the strategy has already
     stopped working recently -- exactly what happened to Turtle: full
     10-year PASS, but REJECT when the 2021-2026 slice was tested alone).

This second condition is deliberately NOT satisfied merely by the last
walk-forward window of a long backtest "happening" to be recent -- it
requires an EXPLICIT, DEDICATED recent-period-only run (see
run_recent_period_check() below), so it can never be silently skipped or
diluted by a long lookback window's early years.

STRATEGY-AWARE WINDOWING (added 2026-08-03, after Minervini's first
recent-period check): naively splitting RECENT_PERIOD_YEARS into
n_walk_forward_windows windows can starve a long-lookback strategy of any
tradeable days per window -- Minervini needs 252 bars (its
min_lookback_days, see swing_research/base.py) before its first possible
signal, so 3 windows of a 3-year (~756-trading-day) period left each
window with ~252 bars of pure warm-up and almost nothing left to trade,
producing a 0-trade REJECT that reflected the windowing arithmetic, not
the strategy's actual recent performance (confirmed by a separate,
non-windowed continuous run over the identical 3-year slice, which DID
trade normally). Fixed generically below: window count is now DERIVED
from each strategy's own declared `min_lookback_days` and the actual
available trading days in the recent-period slice, via
_feasible_window_count() -- not a hardcoded per-strategy override. This
applies identically to every future strategy in the roadmap (52-Week High
Momentum, Cross-Sectional Momentum, ...), each of which declares its own
min_lookback_days on its Strategy subclass.

THIRD VERDICT -- INCONCLUSIVE (added 2026-08-04, after Minervini): the
official recent-period check and an independent robustness study can
themselves disagree -- Minervini's official 3-year/2-window recent-period
check REJECTed on a single negative consistency window, while a richer
5-year second-half robustness sub-period (3 windows, materially
overlapping the same years) PASSed strongly, beating even the earlier
era. Unlike Turtle -- where the second-half robustness result CORROBORATED
the recent-period REJECT (both genuinely pointed the same direction) --
here the two independent, methodologically valid tests point in opposite
directions. The framework has no principled basis to pick a winner between
them, and forcing a PASS or REJECT in that situation would silently
substitute one arbitrary choice for a real, disclosed conflict in the
evidence. determine_acceptance_verdict() below returns "INCONCLUSIVE" for
exactly this situation: base run PASS, recent-period REJECT, and the
caller discloses that an independent robustness check materially
disagrees with that REJECT. INCONCLUSIVE means neither approved for paper
trading nor permanently rejected -- eligible for reconsideration if future
evidence (more market history, alternate universes, further robustness
studies) resolves the conflict either way.
"""

from datetime import date, timedelta
from typing import Optional

from swing_research.base import Strategy

RECENT_PERIOD_YEARS = 3   # the "most recent market period" window checked by every future strategy

# Generic (not strategy-specific) minimum number of trading days a walk-
# forward window needs AFTER a strategy's own warm-up/lookback period for
# its trade count to be even potentially meaningful -- the same order of
# magnitude as a trading quarter, deliberately conservative since the
# Statistical Auditor itself already applies the real statistical
# significance thresholds (min_trades_total, min_out_of_sample_trades) on
# top of whatever a window actually produces.
MIN_TRADEABLE_DAYS_PER_WINDOW = 60


def _feasible_window_count(available_trading_days: int, min_lookback_days: int,
                            requested_windows: int,
                            min_tradeable_days_per_window: int = MIN_TRADEABLE_DAYS_PER_WINDOW) -> int:
    """
    Derives how many walk-forward windows the available_trading_days can
    actually support for a strategy needing min_lookback_days of warm-up
    per window, capped at whatever was requested (never invents MORE
    windows than asked for, only ever reduces to what's feasible).
    """
    if min_lookback_days <= 0:
        return max(1, requested_windows)
    days_needed_per_window = min_lookback_days + min_tradeable_days_per_window
    max_feasible_windows = max(1, available_trading_days // days_needed_per_window)
    return max(1, min(requested_windows, max_feasible_windows))


def run_recent_period_check(data: dict, run_turtle_style_experiment_fn, strategy: Strategy,
                             min_tradeable_days_per_window: int = MIN_TRADEABLE_DAYS_PER_WINDOW,
                             **experiment_kwargs) -> dict:
    """
    Generic helper: re-runs a strategy's full experiment pipeline restricted
    to the last RECENT_PERIOD_YEARS of whatever data was already fetched
    (no re-fetch -- slices the same data the base run used), and returns
    the resulting experiment's verdict/metrics for the acceptance-criteria
    decision. run_turtle_style_experiment_fn: any of this program's
    run_*_experiment functions, sharing the same
    (data, start_date, end_date, n_walk_forward_windows, ...) call shape.

    strategy: an INSTANCE of the swing_research.base.Strategy subclass
    being tested (e.g. MinerviniTrendTemplateFilterStrategy()) -- read only
    for its declared min_lookback_days, to derive a feasible walk-forward
    window count for this recent-period slice (see module docstring). If
    the caller passes n_walk_forward_windows in experiment_kwargs, that
    value is treated as the REQUESTED count and may be reduced (never
    increased) to whatever the available data can actually support.

    Returns {"exp_id": ..., "requested_n_walk_forward_windows": ...,
    "n_walk_forward_windows_used": ...} rather than a bare exp_id, so the
    caller can see (and record) whether/how much the windowing was reduced.
    """
    full_end = max(df.index.date.max() for df in data.values() if df is not None and not df.empty)
    recent_start = full_end - timedelta(days=int(RECENT_PERIOD_YEARS * 365.25))

    recent_data = {
        sym: df[(df.index.date >= recent_start) & (df.index.date <= full_end)]
        for sym, df in data.items() if df is not None
    }
    trading_calendar = sorted({
        d for df in recent_data.values() if df is not None and not df.empty for d in df.index.date
    })
    available_trading_days = len(trading_calendar)

    requested_windows = experiment_kwargs.pop("n_walk_forward_windows", 3)
    n_windows = _feasible_window_count(
        available_trading_days, getattr(strategy, "min_lookback_days", 0),
        requested_windows, min_tradeable_days_per_window,
    )

    exp_id = run_turtle_style_experiment_fn(
        data=recent_data, start_date=recent_start, end_date=full_end,
        n_walk_forward_windows=n_windows, **experiment_kwargs
    )
    return {
        "exp_id": exp_id, "requested_n_walk_forward_windows": requested_windows,
        "n_walk_forward_windows_used": n_windows, "available_trading_days": available_trading_days,
    }


ACCEPTANCE_VERDICTS = ("PASS", "REJECT", "INCONCLUSIVE")


def determine_acceptance_verdict(base_run_verdict_decision: str, recent_period_verdict_decision: str,
                                  conflicting_robustness_evidence: bool = False) -> str:
    """
    The full three-way gate (see module docstring for INCONCLUSIVE's
    rationale):
      - base run REJECT -> "REJECT" (recent-period result doesn't matter --
        if it doesn't even hold up over the full history, it's rejected).
      - base run PASS, recent-period PASS -> "PASS".
      - base run PASS, recent-period REJECT, no disclosed conflicting
        robustness evidence -> "REJECT" (Turtle's case: the second-half
        robustness sub-period corroborated the recent-period REJECT, so
        there was nothing to disclose as conflicting).
      - base run PASS, recent-period REJECT, WITH disclosed conflicting
        robustness evidence -> "INCONCLUSIVE" (Minervini's case).

    conflicting_robustness_evidence: the CALLER's explicit disclosure that
    an independent robustness study (e.g. a second-half sub-period run)
    covering a materially overlapping period reached the OPPOSITE decision
    from recent_period_verdict_decision. This function does not compute
    that disagreement itself -- it has no access to robustness results --
    it only encodes what to DO once a human/caller has identified one,
    so the decision rule itself is generic and reusable rather than
    re-derived ad hoc for each strategy.
    """
    if base_run_verdict_decision != "PASS":
        return "REJECT"
    if recent_period_verdict_decision == "PASS":
        return "PASS"
    if conflicting_robustness_evidence:
        return "INCONCLUSIVE"
    return "REJECT"


def meets_acceptance_criteria(base_run_verdict_decision: str, recent_period_verdict_decision: str) -> bool:
    """
    Backward-compatible two-way gate (no robustness-conflict awareness):
    both the full-history run AND the dedicated recent-period-only run
    must PASS. Equivalent to determine_acceptance_verdict(...) == "PASS"
    with conflicting_robustness_evidence=False. Strategies whose
    recent-period result conflicts with an independent robustness study
    should use determine_acceptance_verdict() directly instead, so an
    unresolved conflict is recorded as INCONCLUSIVE rather than silently
    collapsed into REJECT.
    """
    return determine_acceptance_verdict(base_run_verdict_decision, recent_period_verdict_decision) == "PASS"
