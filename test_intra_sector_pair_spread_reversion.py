"""
Unit tests for research_lab/strategies/intra_sector_pair_spread_reversion.py.
A deliberate exception to research_lab's no-per-strategy-tests habit: the
one piece of logic here that matters -- z-score sign -> which leg is long
-- would silently reverse every position if it were backwards. Run with:

    python test_intra_sector_pair_spread_reversion.py
"""

import unittest

import pandas as pd

from research_lab.strategies.intra_sector_pair_spread_reversion import IntraSectorPairSpreadReversionStrategy


def _bar(close):
    return pd.DataFrame([{"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000}],
                        index=pd.DatetimeIndex([pd.Timestamp("2026-02-03 09:20")]))


def _context(**overrides):
    ctx = {"symbol_a": "A", "symbol_b": "B", "baseline_mean": 2.0, "baseline_std": 0.1,
           "correlation": 0.9, "correlation_ok": True}
    ctx.update(overrides)
    return ctx


class TestIntraSectorPairSpreadReversion(unittest.TestCase):
    def setUp(self):
        self.strategy = IntraSectorPairSpreadReversionStrategy()

    def test_ratio_rich_shorts_a_and_longs_b(self):
        signal = self.strategy.generate_pair_signal(_bar(2.25), _bar(1.0), _context())   # z = +2.5
        self.assertEqual(signal.long_leg, "b")
        self.assertAlmostEqual(signal.entry_zscore, 2.5)
        self.assertEqual(signal.stop_zscore, 3.5)
        self.assertEqual(signal.target_zscore, 0.5)

    def test_ratio_cheap_longs_a_and_shorts_b(self):
        signal = self.strategy.generate_pair_signal(_bar(1.75), _bar(1.0), _context())   # z = -2.5
        self.assertEqual(signal.long_leg, "a")
        self.assertAlmostEqual(signal.entry_zscore, -2.5)

    def test_fires_exactly_at_the_threshold(self):
        signal = self.strategy.generate_pair_signal(_bar(2.2), _bar(1.0), _context())   # z = +2.0
        self.assertIsNotNone(signal)

    def test_none_below_threshold(self):
        self.assertIsNone(self.strategy.generate_pair_signal(_bar(2.15), _bar(1.0), _context()))   # z = +1.5

    def test_none_when_correlation_gate_is_closed(self):
        self.assertIsNone(self.strategy.generate_pair_signal(
            _bar(2.25), _bar(1.0), _context(correlation=0.5, correlation_ok=False)))

    def test_none_without_a_baseline(self):
        self.assertIsNone(self.strategy.generate_pair_signal(_bar(2.25), _bar(1.0), _context(baseline_std=None)))
        self.assertIsNone(self.strategy.generate_pair_signal(_bar(2.25), _bar(1.0), _context(baseline_mean=None)))
        self.assertIsNone(self.strategy.generate_pair_signal(_bar(2.25), _bar(1.0), _context(baseline_std=0.0)))

    def test_none_without_context(self):
        self.assertIsNone(self.strategy.generate_pair_signal(_bar(2.25), _bar(1.0), None))


if __name__ == "__main__":
    unittest.main()
