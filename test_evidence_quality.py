"""
Unit tests for swing_research/evidence_quality.py -- a deterministic,
outcome-blind confidence score computed purely from sample-size/coverage
characteristics. Run with:

    python test_evidence_quality.py
"""

import unittest

from swing_research.evidence_quality import (
    COVERAGE_LOOKBACK_MULTIPLE, OOS_TRADE_COUNT_SATURATION, TRADE_COUNT_SATURATION,
    WINDOW_COUNT_SATURATION, compute_evidence_quality,
)


class TestComputeEvidenceQuality(unittest.TestCase):
    def test_all_dimensions_at_or_above_saturation_scores_100_high(self):
        result = compute_evidence_quality(
            total_trades=TRADE_COUNT_SATURATION, out_of_sample_trades=OOS_TRADE_COUNT_SATURATION,
            windows_used=WINDOW_COUNT_SATURATION,
            available_trading_days=1000 * COVERAGE_LOOKBACK_MULTIPLE, min_lookback_days=1000,
        )
        self.assertAlmostEqual(result["score"], 100.0)
        self.assertEqual(result["label"], "HIGH")

    def test_all_zero_scores_zero_very_low(self):
        result = compute_evidence_quality(
            total_trades=0, out_of_sample_trades=0, windows_used=0,
            available_trading_days=0, min_lookback_days=252,
        )
        self.assertAlmostEqual(result["score"], 0.0)
        self.assertEqual(result["label"], "VERY_LOW")

    def test_score_never_exceeds_100_even_when_inputs_far_exceed_saturation(self):
        result = compute_evidence_quality(
            total_trades=TRADE_COUNT_SATURATION * 10, out_of_sample_trades=OOS_TRADE_COUNT_SATURATION * 10,
            windows_used=WINDOW_COUNT_SATURATION * 10,
            available_trading_days=10_000_000, min_lookback_days=1,
        )
        self.assertAlmostEqual(result["score"], 100.0)

    def test_zero_min_lookback_gives_full_data_coverage_score(self):
        # A strategy with no meaningful warm-up requirement can't be
        # "starved" of coverage -- full marks on that one dimension.
        result = compute_evidence_quality(
            total_trades=0, out_of_sample_trades=0, windows_used=0,
            available_trading_days=10, min_lookback_days=0,
        )
        self.assertAlmostEqual(result["components"]["data_coverage_score"], 25.0)

    def test_turtles_actual_base_run_numbers_score_high(self):
        # EXP-002: 244 total trades, out-of-sample expectancy computed
        # from a real holdout, 3 windows, ~10 years of daily data (~2500
        # trading days), min_lookback_days=55.
        result = compute_evidence_quality(
            total_trades=244, out_of_sample_trades=60, windows_used=3,
            available_trading_days=2500, min_lookback_days=55,
        )
        self.assertGreaterEqual(result["score"], 75.0)
        self.assertEqual(result["label"], "HIGH")

    def test_minervinis_thin_recent_period_check_scores_lower_than_its_base_run(self):
        # EXP-009 (thin, 2-window recent-period check) vs EXP-008 (rich,
        # 3-window, 10-year base run) -- the score should reflect the
        # sample-size difference regardless of either result's verdict.
        thin = compute_evidence_quality(
            total_trades=117, out_of_sample_trades=47, windows_used=2,
            available_trading_days=741, min_lookback_days=252,
        )
        rich = compute_evidence_quality(
            total_trades=574, out_of_sample_trades=200, windows_used=3,
            available_trading_days=2500, min_lookback_days=252,
        )
        self.assertLess(thin["score"], rich["score"])

    def test_score_is_outcome_blind_same_sample_size_different_signs(self):
        # Two calls with IDENTICAL sample-size inputs must produce an
        # IDENTICAL score -- this function has no parameter through which
        # a result's sign, magnitude, or verdict could enter the
        # computation at all (structural guarantee, not just an assertion
        # about behavior).
        a = compute_evidence_quality(100, 20, 3, 1000, 100)
        b = compute_evidence_quality(100, 20, 3, 1000, 100)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["label"], b["label"])

    def test_components_sum_to_total_score(self):
        # Each component is independently rounded for display, so the sum
        # of rounded components can differ from the (unrounded) total by
        # at most a fraction of a point of rounding slop.
        result = compute_evidence_quality(50, 10, 2, 500, 100)
        component_sum = sum(result["components"].values())
        self.assertAlmostEqual(result["score"], component_sum, delta=0.5)

    def test_inputs_are_preserved_verbatim_for_auditability(self):
        result = compute_evidence_quality(50, 10, 2, 500, 100)
        self.assertEqual(result["inputs"], {
            "total_trades": 50, "out_of_sample_trades": 10, "windows_used": 2,
            "available_trading_days": 500, "min_lookback_days": 100,
        })


if __name__ == "__main__":
    unittest.main()
