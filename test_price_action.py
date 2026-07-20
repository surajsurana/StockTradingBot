"""
Mock-based unit tests for strategies/price_action.py -- the price-move/MA/
volume facts fed into the Research Analyst, so it can actually see things
like PATANJALI.NS's double-digit slide instead of only ever seeing "no
technical signal today." Run with:

    python test_price_action.py
"""

import unittest
import pandas as pd

from strategies.price_action import compute_price_action


def _make_history(closes, highs=None, volumes=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    volumes = volumes or [1_000_000] * n
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "Open": closes, "High": highs, "Low": [c * 0.99 for c in closes],
        "Close": closes, "Volume": volumes,
    }, index=idx)


class TestComputePriceAction(unittest.TestCase):
    def test_insufficient_history_returns_none(self):
        df = _make_history([100] * 10)
        self.assertIsNone(compute_price_action(df))

    def test_pct_off_high_computed_correctly(self):
        closes = [100] * 19 + [150, 120]  # 20-day high is 150 (well within the window)
        df = _make_history(closes, highs=[c * 1.0 for c in closes])
        pa = compute_price_action(df, high_lookback_days=20)
        self.assertAlmostEqual(pa.pct_off_high, (120 - 150) / 150 * 100, places=1)

    def test_above_ma_flags(self):
        # steadily rising series -- today's close should be above all MAs
        closes = [100 + i * 2 for i in range(220)]
        df = _make_history(closes)
        pa = compute_price_action(df)
        self.assertTrue(pa.above_20ma)
        self.assertTrue(pa.above_50ma)
        self.assertTrue(pa.above_200ma)

    def test_below_all_mas_on_a_downtrend(self):
        closes = [500 - i * 1.5 for i in range(220)]
        df = _make_history(closes)
        pa = compute_price_action(df)
        self.assertFalse(pa.above_20ma)
        self.assertFalse(pa.above_50ma)
        self.assertFalse(pa.above_200ma)

    def test_ma50_and_ma200_none_when_insufficient_history(self):
        closes = [100] * 25
        df = _make_history(closes)
        pa = compute_price_action(df)
        self.assertIsNotNone(pa.above_20ma)
        self.assertIsNone(pa.above_50ma)
        self.assertIsNone(pa.above_200ma)

    def test_volume_ratio(self):
        closes = [100] * 25
        volumes = [1_000_000] * 20 + [3_000_000] * 5  # last 5 days elevated, incl. today
        df = _make_history(closes, volumes=volumes)
        pa = compute_price_action(df)
        self.assertIsNotNone(pa.volume_ratio)
        self.assertGreater(pa.volume_ratio, 1.0)

    def test_is_down_day(self):
        closes = [100] * 24 + [90]
        df = _make_history(closes)
        pa = compute_price_action(df)
        self.assertTrue(pa.is_down_day)

    def test_pct_since_entry_none_without_entry_price(self):
        df = _make_history([100] * 25)
        pa = compute_price_action(df, entry_price=None)
        self.assertIsNone(pa.pct_since_entry)

    def test_pct_since_entry_computed_when_given(self):
        df = _make_history([100] * 24 + [80])
        pa = compute_price_action(df, entry_price=100.0)
        self.assertAlmostEqual(pa.pct_since_entry, -20.0, places=1)

    def test_patanjali_like_double_digit_slide_detected(self):
        # Mirrors the real live case: a stock well off its recent high and
        # below all its own moving averages.
        closes = [500] * 200 + list(range(500, 330, -8))  # long decline into a slide
        df = _make_history(closes)
        pa = compute_price_action(df, entry_price=350.45)
        self.assertLess(pa.pct_off_high, -10)
        self.assertFalse(pa.above_20ma)
        self.assertFalse(pa.above_50ma)


if __name__ == "__main__":
    unittest.main()
