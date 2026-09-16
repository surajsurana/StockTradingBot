"""
Unit tests for the turnover functions in swing_research/cross_sectional.py
(compute_turnover_score, compute_turnover_percentile_ranks) -- hand-
verifiable turnover ratios and percentile ranking against a small
synthetic multi-symbol universe, plus the missing/zero-shares-outstanding
edge cases specific to this candidate. Run with:

    python test_turnover_cross_sectional.py
"""

import unittest

import pandas as pd

from swing_research.cross_sectional import (
    TURNOVER_FORMATION_DAYS, compute_turnover_percentile_ranks, compute_turnover_score,
)


def _constant_volume_series(volume, n=40, start_price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price] * n
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [volume] * n}, index=idx)


class TestComputeTurnoverScore(unittest.TestCase):
    def test_matches_hand_calculation(self):
        # Constant Volume 50,000 / 1,000,000 shares outstanding = 5% turnover every day,
        # so the trailing rolling average is exactly 0.05 once the window is full.
        df = _constant_volume_series(50_000)
        score = compute_turnover_score(df, shares_outstanding=1_000_000)
        self.assertAlmostEqual(score.iloc[-1], 0.05, places=6)

    def test_lower_volume_gives_lower_turnover(self):
        low = compute_turnover_score(_constant_volume_series(1_000), shares_outstanding=1_000_000)
        high = compute_turnover_score(_constant_volume_series(100_000), shares_outstanding=1_000_000)
        self.assertLess(low.iloc[-1], high.iloc[-1])

    def test_missing_shares_outstanding_returns_all_nan(self):
        df = _constant_volume_series(50_000)
        score = compute_turnover_score(df, shares_outstanding=None)
        self.assertTrue(score.isna().all())

    def test_zero_shares_outstanding_returns_all_nan_not_a_crash(self):
        df = _constant_volume_series(50_000)
        score = compute_turnover_score(df, shares_outstanding=0)
        self.assertTrue(score.isna().all())

    def test_no_lookahead_early_rows_are_nan(self):
        df = _constant_volume_series(50_000, n=10)  # short of the formation window
        score = compute_turnover_score(df, shares_outstanding=1_000_000)
        self.assertTrue(pd.isna(score.iloc[-1]))


class TestComputeTurnoverPercentileRanks(unittest.TestCase):
    def test_lower_turnover_symbol_ranks_lower(self):
        # LOW percentile = LOW turnover = the illiquid stock this strategy wants to buy (bottom decile).
        data = {
            "ILLIQUID": _constant_volume_series(1_000),
            "MODERATE": _constant_volume_series(50_000),
            "LIQUID": _constant_volume_series(500_000),
        }
        shares = {"ILLIQUID": 1_000_000, "MODERATE": 1_000_000, "LIQUID": 1_000_000}
        ranks = compute_turnover_percentile_ranks(data, shares)
        last_date = data["ILLIQUID"].index[-1]
        self.assertLess(ranks["ILLIQUID"].loc[last_date], ranks["MODERATE"].loc[last_date])
        self.assertLess(ranks["MODERATE"].loc[last_date], ranks["LIQUID"].loc[last_date])

    def test_symbol_with_no_shares_outstanding_is_excluded_not_treated_as_illiquid(self):
        # A missing share count must not silently rank as "most illiquid" -- it should be
        # excluded from the cross-section entirely (NaN score, ignored by pandas' rank()).
        data = {
            "KNOWN_LOW": _constant_volume_series(1_000),
            "KNOWN_HIGH": _constant_volume_series(500_000),
            "UNKNOWN_SHARES": _constant_volume_series(1_000),
        }
        shares = {"KNOWN_LOW": 1_000_000, "KNOWN_HIGH": 1_000_000}   # UNKNOWN_SHARES deliberately absent
        ranks = compute_turnover_percentile_ranks(data, shares)
        last_date = data["KNOWN_LOW"].index[-1]
        self.assertTrue(pd.isna(ranks["UNKNOWN_SHARES"].loc[last_date]))
        self.assertFalse(pd.isna(ranks["KNOWN_LOW"].loc[last_date]))

    def test_percentiles_are_between_0_and_100(self):
        data = {s: _constant_volume_series(v) for s, v in
                [("A", 10_000), ("B", 400_000), ("C", 1_000), ("D", 250_000)]}
        shares = {s: 1_000_000 for s in data}
        ranks = compute_turnover_percentile_ranks(data, shares)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_turnover_percentile_ranks({}, {}), {})


class TestFormationWindow(unittest.TestCase):
    def test_formation_days_is_21(self):
        self.assertEqual(TURNOVER_FORMATION_DAYS, 21)


if __name__ == "__main__":
    unittest.main()
