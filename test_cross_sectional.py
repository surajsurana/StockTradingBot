"""
Unit tests for swing_research/cross_sectional.py -- hand-verifiable RS
score and percentile ranking against a small synthetic multi-symbol
universe. Run with:

    python test_cross_sectional.py
"""

import unittest

import pandas as pd

from swing_research.cross_sectional import (
    compute_52w_high_nearness_percentile_ranks, compute_52w_high_nearness_score,
    compute_max_effect_percentile_ranks, compute_max_effect_score,
    compute_momentum_percentile_ranks, compute_momentum_score,
    compute_rs_percentile_ranks, compute_rs_score,
    compute_short_term_reversal_percentile_ranks, compute_short_term_reversal_score,
)


def _flat_then_return_bars(total_return_over_63_days, n=300, start_price=100.0):
    """
    n days flat at start_price, then a straight-line ramp over the final
    63 trading days (3 months) to a known total return -- makes r3 (the
    dominant 40%-weighted term) hand-calculable. n must be > 252 (12
    months x 21 trading days) so the r12 term is non-NaN by the last row.
    """
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price] * (n - 63)
    end_price = start_price * (1 + total_return_over_63_days)
    step = (end_price - start_price) / 63
    closes += [start_price + step * i for i in range(1, 64)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestComputeRsScore(unittest.TestCase):
    def test_flat_series_has_zero_score(self):
        df = _flat_then_return_bars(0.0)
        score = compute_rs_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=6)

    def test_positive_return_gives_positive_score(self):
        df = _flat_then_return_bars(0.50)  # +50% over the last quarter
        score = compute_rs_score(df)
        self.assertGreater(score.iloc[-1], 0.0)

    def test_no_lookahead_early_rows_are_nan(self):
        df = _flat_then_return_bars(0.20, n=100)
        score = compute_rs_score(df)
        self.assertTrue(pd.isna(score.iloc[0]))  # far short of even the 3-month lookback


class TestComputeRsPercentileRanks(unittest.TestCase):
    def test_higher_momentum_symbol_ranks_higher(self):
        data = {
            "STRONG": _flat_then_return_bars(1.00),   # +100% last quarter
            "MEDIUM": _flat_then_return_bars(0.20),   # +20%
            "WEAK": _flat_then_return_bars(-0.30),    # -30%
        }
        ranks = compute_rs_percentile_ranks(data)
        last_date = data["STRONG"].index[-1]
        self.assertGreater(ranks["STRONG"].loc[last_date], ranks["MEDIUM"].loc[last_date])
        self.assertGreater(ranks["MEDIUM"].loc[last_date], ranks["WEAK"].loc[last_date])

    def test_percentiles_are_between_0_and_100(self):
        data = {
            "A": _flat_then_return_bars(0.10), "B": _flat_then_return_bars(0.40),
            "C": _flat_then_return_bars(-0.10), "D": _flat_then_return_bars(0.05),
        }
        ranks = compute_rs_percentile_ranks(data)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_rs_percentile_ranks({}), {})

    def test_top_symbol_of_four_lands_at_100th_percentile(self):
        data = {
            "A": _flat_then_return_bars(0.10), "B": _flat_then_return_bars(0.40),
            "C": _flat_then_return_bars(-0.10), "D": _flat_then_return_bars(0.05),
        }
        ranks = compute_rs_percentile_ranks(data)
        last_date = data["B"].index[-1]
        # B has the highest quarterly return of the four -> rank 4/4 -> 100th pct
        self.assertAlmostEqual(ranks["B"].loc[last_date], 100.0)


def _uptrend_at_own_high(n=300, start_price=100.0, daily_pct=0.002):
    """Monotonic uptrend -- every close is its own running 52-week high, so
    the nearness ratio is exactly 1.0 by construction once the 252-day
    window is full."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price * (1 + daily_pct) ** i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


def _pulled_back_from_high(n=300, high_price=200.0, current_price=100.0):
    """Flat at high_price long enough to seed a 252-day high, then a final
    sharp drop to current_price -- nearness ratio on the last day is
    exactly current_price / high_price, hand-verifiable."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [high_price] * (n - 1) + [current_price]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestCompute52wHighNearnessScore(unittest.TestCase):
    def test_at_own_high_ratio_is_1(self):
        df = _uptrend_at_own_high()
        score = compute_52w_high_nearness_score(df)
        self.assertAlmostEqual(score.iloc[-1], 1.0, places=6)

    def test_pulled_back_ratio_matches_hand_calculation(self):
        df = _pulled_back_from_high(high_price=200.0, current_price=100.0)
        score = compute_52w_high_nearness_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.5, places=6)

    def test_no_lookahead_early_rows_are_nan(self):
        df = _uptrend_at_own_high(n=100)  # far short of the 252-day window
        score = compute_52w_high_nearness_score(df)
        self.assertTrue(pd.isna(score.iloc[0]))


class TestCompute52wHighNearnessPercentileRanks(unittest.TestCase):
    def test_symbol_at_its_high_ranks_above_a_pulled_back_symbol(self):
        data = {
            "AT_HIGH": _uptrend_at_own_high(),
            "PULLED_BACK": _pulled_back_from_high(high_price=200.0, current_price=100.0),
        }
        ranks = compute_52w_high_nearness_percentile_ranks(data)
        last_date = data["AT_HIGH"].index[-1]
        self.assertGreater(ranks["AT_HIGH"].loc[last_date], ranks["PULLED_BACK"].loc[last_date])

    def test_percentiles_are_between_0_and_100(self):
        data = {
            "A": _uptrend_at_own_high(),
            "B": _pulled_back_from_high(high_price=150.0, current_price=120.0),
            "C": _pulled_back_from_high(high_price=200.0, current_price=50.0),
        }
        ranks = compute_52w_high_nearness_percentile_ranks(data)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_52w_high_nearness_percentile_ranks({}), {})

    def test_top_symbol_lands_at_100th_percentile(self):
        data = {
            "AT_HIGH": _uptrend_at_own_high(),
            "SLIGHT_PULLBACK": _pulled_back_from_high(high_price=110.0, current_price=100.0),
            "DEEP_PULLBACK": _pulled_back_from_high(high_price=200.0, current_price=50.0),
        }
        ranks = compute_52w_high_nearness_percentile_ranks(data)
        last_date = data["AT_HIGH"].index[-1]
        self.assertAlmostEqual(ranks["AT_HIGH"].loc[last_date], 100.0)


def _flat_then_final_jump(final_return, n=200, start_price=100.0):
    """n-1 days flat at start_price, then a single final-day jump to
    start_price*(1+final_return) -- makes the 126-day formation return at
    the LAST row exactly hand-calculable (close.shift(126) at the last row
    still falls in the flat region as long as n >= 128)."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price] * (n - 1) + [start_price * (1 + final_return)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestComputeMomentumScore(unittest.TestCase):
    def test_flat_series_has_zero_score(self):
        df = _flat_then_final_jump(0.0)
        score = compute_momentum_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=6)

    def test_positive_jump_gives_positive_score_matching_hand_calculation(self):
        df = _flat_then_final_jump(0.30)  # +30% on the final day
        score = compute_momentum_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.30, places=6)

    def test_negative_jump_gives_negative_score(self):
        df = _flat_then_final_jump(-0.20)
        score = compute_momentum_score(df)
        self.assertAlmostEqual(score.iloc[-1], -0.20, places=6)

    def test_no_lookahead_early_rows_are_nan(self):
        df = _flat_then_final_jump(0.10, n=100)  # short of the 126-day formation window
        score = compute_momentum_score(df)
        self.assertTrue(pd.isna(score.iloc[-1]))


class TestComputeMomentumPercentileRanks(unittest.TestCase):
    def test_higher_formation_return_symbol_ranks_higher(self):
        data = {
            "STRONG": _flat_then_final_jump(1.00),
            "MEDIUM": _flat_then_final_jump(0.20),
            "WEAK": _flat_then_final_jump(-0.30),
        }
        ranks = compute_momentum_percentile_ranks(data)
        last_date = data["STRONG"].index[-1]
        self.assertGreater(ranks["STRONG"].loc[last_date], ranks["MEDIUM"].loc[last_date])
        self.assertGreater(ranks["MEDIUM"].loc[last_date], ranks["WEAK"].loc[last_date])

    def test_percentiles_are_between_0_and_100(self):
        data = {
            "A": _flat_then_final_jump(0.10), "B": _flat_then_final_jump(0.40),
            "C": _flat_then_final_jump(-0.10), "D": _flat_then_final_jump(0.05),
        }
        ranks = compute_momentum_percentile_ranks(data)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_momentum_percentile_ranks({}), {})

    def test_top_symbol_lands_at_100th_percentile(self):
        data = {
            "A": _flat_then_final_jump(0.10), "B": _flat_then_final_jump(0.40),
            "C": _flat_then_final_jump(-0.10), "D": _flat_then_final_jump(0.05),
        }
        ranks = compute_momentum_percentile_ranks(data)
        last_date = data["B"].index[-1]
        self.assertAlmostEqual(ranks["B"].loc[last_date], 100.0)


class TestComputeShortTermReversalScore(unittest.TestCase):
    def test_flat_series_has_zero_score(self):
        df = _flat_then_final_jump(0.0)
        score = compute_short_term_reversal_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=6)

    def test_negative_jump_gives_negative_score_matching_hand_calculation(self):
        df = _flat_then_final_jump(-0.15)  # -15% on the final day
        score = compute_short_term_reversal_score(df)
        self.assertAlmostEqual(score.iloc[-1], -0.15, places=6)

    def test_positive_jump_gives_positive_score(self):
        df = _flat_then_final_jump(0.10)
        score = compute_short_term_reversal_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.10, places=6)

    def test_no_lookahead_early_rows_are_nan(self):
        df = _flat_then_final_jump(0.10, n=15)  # short of the 21-day formation window
        score = compute_short_term_reversal_score(df)
        self.assertTrue(pd.isna(score.iloc[-1]))


class TestComputeShortTermReversalPercentileRanks(unittest.TestCase):
    def test_worse_return_symbol_ranks_lower(self):
        # This is the INVERSE relationship of momentum -- a strategy that
        # buys the bottom decile cares about LOW percentile = worst return.
        data = {
            "WORST": _flat_then_final_jump(-0.30),
            "MIDDLE": _flat_then_final_jump(0.0),
            "BEST": _flat_then_final_jump(0.30),
        }
        ranks = compute_short_term_reversal_percentile_ranks(data)
        last_date = data["WORST"].index[-1]
        self.assertLess(ranks["WORST"].loc[last_date], ranks["MIDDLE"].loc[last_date])
        self.assertLess(ranks["MIDDLE"].loc[last_date], ranks["BEST"].loc[last_date])

    def test_percentiles_are_between_0_and_100(self):
        data = {
            "A": _flat_then_final_jump(0.10), "B": _flat_then_final_jump(-0.40),
            "C": _flat_then_final_jump(-0.10), "D": _flat_then_final_jump(0.05),
        }
        ranks = compute_short_term_reversal_percentile_ranks(data)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_short_term_reversal_percentile_ranks({}), {})

    def test_worst_symbol_lands_at_lowest_percentile(self):
        data = {
            "A": _flat_then_final_jump(0.10), "B": _flat_then_final_jump(-0.40),
            "C": _flat_then_final_jump(-0.10), "D": _flat_then_final_jump(0.05),
        }
        ranks = compute_short_term_reversal_percentile_ranks(data)
        last_date = data["B"].index[-1]
        # B has the lowest (most negative) return of the four -> rank 1/4 -> 25th pct
        self.assertAlmostEqual(ranks["B"].loc[last_date], 25.0)


class TestComputeMaxEffectScore(unittest.TestCase):
    def test_flat_series_has_zero_score(self):
        # A flat series has a zero daily return every day -- the rolling
        # MAX of an all-zero window is itself 0.0.
        df = _flat_then_final_jump(0.0)
        score = compute_max_effect_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=6)

    def test_positive_jump_gives_a_score_matching_hand_calculation(self):
        # Every day in the trailing 21-day window is 0% except the final
        # day (+20%) -- the rolling MAX must equal that single spike.
        df = _flat_then_final_jump(0.20)
        score = compute_max_effect_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.20, places=6)

    def test_negative_final_day_does_not_push_the_score_below_zero(self):
        # Unlike a cumulative-return score, MAX is a rolling MAXIMUM of
        # daily returns -- a single negative day surrounded by flat (0%)
        # days must NOT make the score negative, since 0% is still the
        # best day in that window. This is the behavior that distinguishes
        # MAX from short_term_reversal_score on the same fixture shape.
        df = _flat_then_final_jump(-0.30)
        score = compute_max_effect_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=6)

    def test_no_lookahead_early_rows_are_nan(self):
        df = _flat_then_final_jump(0.10, n=15)  # short of the 21-day formation window
        score = compute_max_effect_score(df)
        self.assertTrue(pd.isna(score.iloc[-1]))


class TestComputeMaxEffectPercentileRanks(unittest.TestCase):
    def test_calmer_symbol_ranks_lower(self):
        # LOW percentile = LOW recent max daily return = the "calm" stock
        # this strategy wants to buy (bottom decile).
        data = {
            "CALM": _flat_then_final_jump(0.02),
            "MODERATE": _flat_then_final_jump(0.15),
            "EXTREME": _flat_then_final_jump(0.50),
        }
        ranks = compute_max_effect_percentile_ranks(data)
        last_date = data["CALM"].index[-1]
        self.assertLess(ranks["CALM"].loc[last_date], ranks["MODERATE"].loc[last_date])
        self.assertLess(ranks["MODERATE"].loc[last_date], ranks["EXTREME"].loc[last_date])

    def test_percentiles_are_between_0_and_100(self):
        data = {
            "A": _flat_then_final_jump(0.10), "B": _flat_then_final_jump(0.40),
            "C": _flat_then_final_jump(0.01), "D": _flat_then_final_jump(0.25),
        }
        ranks = compute_max_effect_percentile_ranks(data)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_max_effect_percentile_ranks({}), {})

    def test_calmest_symbol_lands_at_lowest_percentile(self):
        data = {
            "A": _flat_then_final_jump(0.10), "B": _flat_then_final_jump(0.01),
            "C": _flat_then_final_jump(0.40), "D": _flat_then_final_jump(0.25),
        }
        ranks = compute_max_effect_percentile_ranks(data)
        last_date = data["B"].index[-1]
        # B has the lowest MAX (calmest) of the four -> rank 1/4 -> 25th pct
        self.assertAlmostEqual(ranks["B"].loc[last_date], 25.0)


if __name__ == "__main__":
    unittest.main()
