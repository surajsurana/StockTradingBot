"""
Evidence-quality (confidence) scoring for Swing Research Program
experiments -- added 2026-08-04, immediately after Minervini's
INCONCLUSIVE verdict, per explicit direction: "add an evidence-quality or
confidence score to every experiment and verdict based on objective
characteristics such as trade count, effective sample size, number of
walk-forward windows, and data coverage. This score should supplement --
not replace -- the PASS/REJECT/INCONCLUSIVE verdict."

DELIBERATELY OUTCOME-BLIND: every input to compute_evidence_quality() is a
SAMPLE-SIZE/COVERAGE characteristic (how much data went into the verdict),
never the metrics' sign, magnitude, CAGR, Sharpe, or the Statistical
Auditor's own PASS/REJECT/INCONCLUSIVE decision. This is intentional and
load-bearing: a score that could move because a result "looked good" would
be exactly the outcome-driven distortion the acceptance framework itself
was built to resist (see acceptance_criteria.py's own INCONCLUSIVE
verdict, added for the same reason -- refusing to let one favorable
result silently override an unfavorable one). A HIGH evidence-quality
score on a REJECT is just as valid a combination as a HIGH score on a
PASS -- it says "we can trust this REJECT," not "this looks promising."

Does NOT touch acceptance_criteria.py's PASS/REJECT/INCONCLUSIVE decision
logic at all (kept unchanged per explicit direction) -- this module is
purely additive, reported alongside a verdict, never consulted by
determine_acceptance_verdict() or meets_acceptance_criteria().
"""

# Each component saturates independently at a value considered "clearly
# enough" for that dimension -- past the saturation point, MORE of that
# one dimension stops adding confidence (avoids one huge trade count
# alone producing a misleadingly high score while every other dimension
# is thin). Saturation values are round numbers chosen to be well above
# what a solid result looks like (see this module's tests for Turtle's
# and Minervini's actual real numbers as reference points), not tuned to
# any specific experiment's result.
TRADE_COUNT_SATURATION = 200       # total trades across the walk-forward run
OOS_TRADE_COUNT_SATURATION = 30    # out-of-sample holdout trades specifically
WINDOW_COUNT_SATURATION = 5        # walk-forward windows actually used
COVERAGE_LOOKBACK_MULTIPLE = 4     # available trading days / strategy's own
                                    # min_lookback_days -- 4x the minimum
                                    # warm-up requirement counts as "ample
                                    # room to trade beyond just qualifying"

_MAX_COMPONENT_POINTS = 25.0  # 4 components x 25 = 100 total


def _bounded_ratio_score(value: float, saturation_value: float,
                          max_points: float = _MAX_COMPONENT_POINTS) -> float:
    if saturation_value <= 0:
        return max_points
    return max(0.0, min(max_points, max_points * value / saturation_value))


def _label_for_score(score: float) -> str:
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MODERATE"
    if score >= 25:
        return "LOW"
    return "VERY_LOW"


def compute_evidence_quality(total_trades: int, out_of_sample_trades: int, windows_used: int,
                              available_trading_days: int, min_lookback_days: int = 0) -> dict:
    """
    Returns a dict with a 0-100 `score`, a qualitative `label`
    (HIGH/MODERATE/LOW/VERY_LOW), a `components` breakdown (one 0-25
    sub-score per dimension, for transparency into what drove the total),
    and the raw `inputs` this was computed from (for auditability -- this
    score should always be reproducible from numbers already in the
    experiment record, never a black box).

    total_trades / out_of_sample_trades: the SAME counts the Statistical
    Auditor itself used for its PASS/REJECT/INCONCLUSIVE decision (see
    research_lab.statistical_auditor.audit()'s checks dict) -- not
    independently recomputed, so this score is always describing the
    confidence behind the actual verdict, not some other sample.
    windows_used: the actual walk-forward window count used for this run
    (after any strategy-aware reduction, e.g.
    acceptance_criteria._feasible_window_count()).
    available_trading_days: trading days in the data actually backtested.
    min_lookback_days: the strategy's own declared warm-up requirement
    (swing_research.base.Strategy.min_lookback_days) -- 0 for a strategy
    with no meaningful lookback, in which case data-coverage scores full
    marks (nothing to be "starved" of).
    """
    trade_count_score = _bounded_ratio_score(total_trades, TRADE_COUNT_SATURATION)
    oos_trade_count_score = _bounded_ratio_score(out_of_sample_trades, OOS_TRADE_COUNT_SATURATION)
    window_count_score = _bounded_ratio_score(windows_used, WINDOW_COUNT_SATURATION)

    if min_lookback_days > 0:
        coverage_ratio = available_trading_days / (min_lookback_days * COVERAGE_LOOKBACK_MULTIPLE)
        data_coverage_score = _bounded_ratio_score(coverage_ratio, 1.0)
    else:
        data_coverage_score = _MAX_COMPONENT_POINTS

    score = trade_count_score + oos_trade_count_score + window_count_score + data_coverage_score

    return {
        "score": round(score, 1),
        "label": _label_for_score(score),
        "components": {
            "trade_count_score": round(trade_count_score, 1),
            "out_of_sample_trade_count_score": round(oos_trade_count_score, 1),
            "walk_forward_window_count_score": round(window_count_score, 1),
            "data_coverage_score": round(data_coverage_score, 1),
        },
        "inputs": {
            "total_trades": total_trades, "out_of_sample_trades": out_of_sample_trades,
            "windows_used": windows_used, "available_trading_days": available_trading_days,
            "min_lookback_days": min_lookback_days,
        },
    }
