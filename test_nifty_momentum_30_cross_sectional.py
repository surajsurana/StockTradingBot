"""
Unit tests for the Nifty200 Momentum 30-style functions in
swing_research/cross_sectional.py (compute_nifty_momentum_30_score,
compute_nifty_momentum_30_percentile_ranks) -- hand-verifiable blended
risk-adjusted momentum scores and percentile ranking against a small
synthetic multi-symbol universe. Mirrors test_size_premium_cross_sectional.py's
structure. Run with:

    python test_nifty_momentum_30_cross_sectional.py
"""

import unittest

import pandas as pd

from swing_research.cross_sectional import (
    compute_nifty_momentum_30_percentile_ranks, compute_nifty_momentum_30_score,
)


def _trending_noisy_series(n, daily_drift, noise=0.01, start_price=100.0):
    """
    A deterministic compounding drift with a fixed-amplitude alternating
    multiplicative bump every other bar -- gives every series the SAME
    daily-return volatility shape regardless of drift (so two series only
    differ in their formation-period RETURN, letting the risk-adjusted
    ratio's ordering be driven purely by the drift being compared), while
    still being non-flat (non-zero trailing std, so the risk-adjusted
    ratio is never a 0/0 NaN).
    """
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = []
    for i in range(n):
        base = start_price * (1 + daily_drift) ** i
        bump = 1 + (noise if i % 2 == 0 else -noise)
        closes.append(base * bump)
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestComputeNiftyMomentum30Score(unittest.TestCase):
    def test_flat_drift_has_zero_score(self):
        df = _trending_noisy_series(300, daily_drift=0.0)
        score = compute_nifty_momentum_30_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=6)

    def test_positive_drift_gives_positive_score(self):
        df = _trending_noisy_series(300, daily_drift=0.004)
        score = compute_nifty_momentum_30_score(df)
        self.assertGreater(score.iloc[-1], 0.0)

    def test_stronger_drift_gives_a_higher_score(self):
        strong = _trending_noisy_series(300, daily_drift=0.004)
        weak = _trending_noisy_series(300, daily_drift=0.0005)
        self.assertGreater(
            compute_nifty_momentum_30_score(strong).iloc[-1],
            compute_nifty_momentum_30_score(weak).iloc[-1],
        )

    def test_no_lookahead_early_rows_are_nan(self):
        df = _trending_noisy_series(300, daily_drift=0.004)
        score = compute_nifty_momentum_30_score(df)
        self.assertTrue(pd.isna(score.iloc[50]))  # short of the 252-day volatility window


class TestComputeNiftyMomentum30PercentileRanks(unittest.TestCase):
    def test_higher_blended_score_symbol_ranks_higher(self):
        data = {
            "STRONG": _trending_noisy_series(300, daily_drift=0.004),
            "WEAK": _trending_noisy_series(300, daily_drift=0.0005),
            "FLAT": _trending_noisy_series(300, daily_drift=0.0),
        }
        ranks = compute_nifty_momentum_30_percentile_ranks(data)
        last_date = data["STRONG"].index[-1]
        self.assertGreater(ranks["STRONG"].loc[last_date], ranks["WEAK"].loc[last_date])
        self.assertGreater(ranks["WEAK"].loc[last_date], ranks["FLAT"].loc[last_date])

    def test_top_symbol_of_three_lands_at_100th_percentile(self):
        data = {
            "STRONG": _trending_noisy_series(300, daily_drift=0.004),
            "WEAK": _trending_noisy_series(300, daily_drift=0.0005),
            "FLAT": _trending_noisy_series(300, daily_drift=0.0),
        }
        ranks = compute_nifty_momentum_30_percentile_ranks(data)
        last_date = data["STRONG"].index[-1]
        self.assertAlmostEqual(ranks["STRONG"].loc[last_date], 100.0)

    def test_percentiles_are_between_0_and_100(self):
        data = {
            "A": _trending_noisy_series(300, daily_drift=0.001),
            "B": _trending_noisy_series(300, daily_drift=0.004),
            "C": _trending_noisy_series(300, daily_drift=-0.001),
            "D": _trending_noisy_series(300, daily_drift=0.0002),
        }
        ranks = compute_nifty_momentum_30_percentile_ranks(data)
        last_date = data["A"].index[-1]
        for symbol in data:
            pct = ranks[symbol].loc[last_date]
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_nifty_momentum_30_percentile_ranks({}), {})


if __name__ == "__main__":
    unittest.main()
