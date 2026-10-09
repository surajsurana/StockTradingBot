"""
Unit tests for the market-cap functions in swing_research/cross_sectional.py
(compute_market_cap_score, compute_market_cap_percentile_ranks) -- hand-
verifiable market capitalizations and percentile ranking against a small
synthetic multi-symbol universe, plus the missing/zero-shares-outstanding
edge cases specific to this candidate. Mirrors
test_turnover_cross_sectional.py's structure. Run with:

    python test_size_premium_cross_sectional.py
"""

import unittest

import pandas as pd

from swing_research.cross_sectional import compute_market_cap_percentile_ranks, compute_market_cap_score


def _constant_price_series(price, n=10):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [price] * n
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestComputeMarketCapScore(unittest.TestCase):
    def test_matches_hand_calculation(self):
        df = _constant_price_series(100.0)
        score = compute_market_cap_score(df, shares_outstanding=1_000_000)
        self.assertAlmostEqual(score.iloc[-1], 100_000_000.0, places=2)

    def test_no_formation_window_valid_from_the_first_bar(self):
        # Unlike turnover (rolling average), market cap is point-in-time --
        # even a very short history should have a valid score on every bar.
        df = _constant_price_series(100.0, n=3)
        score = compute_market_cap_score(df, shares_outstanding=1_000_000)
        self.assertFalse(score.isna().any())

    def test_more_shares_gives_higher_market_cap(self):
        df = _constant_price_series(100.0)
        small = compute_market_cap_score(df, shares_outstanding=1_000_000)
        large = compute_market_cap_score(df, shares_outstanding=100_000_000)
        self.assertLess(small.iloc[-1], large.iloc[-1])

    def test_missing_shares_outstanding_returns_all_nan(self):
        df = _constant_price_series(100.0)
        score = compute_market_cap_score(df, shares_outstanding=None)
        self.assertTrue(score.isna().all())

    def test_zero_shares_outstanding_returns_all_nan_not_a_crash(self):
        df = _constant_price_series(100.0)
        score = compute_market_cap_score(df, shares_outstanding=0)
        self.assertTrue(score.isna().all())


class TestComputeMarketCapPercentileRanks(unittest.TestCase):
    def test_smaller_market_cap_symbol_ranks_lower(self):
        # LOW percentile = LOW market cap = the small-cap stock this strategy wants to buy (bottom decile).
        data = {
            "SMALL": _constant_price_series(50.0),
            "MID": _constant_price_series(50.0),
            "LARGE": _constant_price_series(50.0),
        }
        shares = {"SMALL": 1_000_000, "MID": 50_000_000, "LARGE": 1_000_000_000}
        ranks = compute_market_cap_percentile_ranks(data, shares)
        last_date = data["SMALL"].index[-1]
        self.assertLess(ranks["SMALL"].loc[last_date], ranks["MID"].loc[last_date])
        self.assertLess(ranks["MID"].loc[last_date], ranks["LARGE"].loc[last_date])

    def test_symbol_with_no_shares_outstanding_is_excluded_not_treated_as_small_cap(self):
        # A missing share count must not silently rank as "smallest cap" -- it should be
        # excluded from the cross-section entirely (NaN score, ignored by pandas' rank()).
        data = {
            "KNOWN_SMALL": _constant_price_series(50.0),
            "KNOWN_LARGE": _constant_price_series(50.0),
            "UNKNOWN_SHARES": _constant_price_series(50.0),
        }
        shares = {"KNOWN_SMALL": 1_000_000, "KNOWN_LARGE": 1_000_000_000}   # UNKNOWN_SHARES deliberately absent
        ranks = compute_market_cap_percentile_ranks(data, shares)
        last_date = data["KNOWN_SMALL"].index[-1]
        self.assertTrue(pd.isna(ranks["UNKNOWN_SHARES"].loc[last_date]))
        self.assertFalse(pd.isna(ranks["KNOWN_SMALL"].loc[last_date]))

    def test_percentiles_are_between_0_and_100(self):
        data = {s: _constant_price_series(p) for s, p in
                [("A", 10.0), ("B", 400.0), ("C", 1.0), ("D", 250.0)]}
        shares = {s: 1_000_000 for s in data}
        ranks = compute_market_cap_percentile_ranks(data, shares)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_market_cap_percentile_ranks({}, {}), {})


if __name__ == "__main__":
    unittest.main()
