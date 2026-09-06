"""
Unit tests for swing_research/strategies/volume_backed_breakout_pool_a.py
-- the fully faithful Pool A port of strategies/volume_backed_breakout.py.
Covers the new-N-day-high + volume-confirmation entry gate, the stop-loss
construction, and the fixed 2:1 price target (restored 2026-09-06 via
Signal.target_price -- "we must not play with strategy rules" -- after an
earlier version had substituted a time-stop). Run with:

    python test_volume_backed_breakout_pool_a.py
"""

import unittest

import pandas as pd

from swing_research.strategies.volume_backed_breakout_pool_a import (
    BREAKOUT_LOOKBACK_DAYS, REWARD_RISK_MULTIPLE, VOLUME_MULTIPLE,
    VolumeBackedBreakoutPoolAStrategy,
)


def _flat_series_with_breakout(n=60, breakout_volume_multiple=2.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [100.0] * n
    highs = [100.5] * n
    lows = [99.5] * n
    opens = [100.0] * n
    volumes = [1000] * n

    # Engineer the last day as a genuine breakout: new high + volume surge.
    highs[-1] = 110.0
    closes[-1] = 109.0
    opens[-1] = 100.5
    volumes[-1] = int(1000 * breakout_volume_multiple)

    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                          "Volume": volumes}, index=idx)


class TestPrecomputeAndEntry(unittest.TestCase):
    def test_fires_on_new_high_with_volume_confirmation_and_sets_target(self):
        strategy = VolumeBackedBreakoutPoolAStrategy()
        df = strategy.precompute(_flat_series_with_breakout(breakout_volume_multiple=2.0))
        last_row = list(df.itertuples(index=False))[-1]
        self.assertTrue(bool(last_row.qualifies))
        signal = strategy.entry_signal_at(last_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertIsNotNone(signal.target_price)
        expected_target = signal.entry_price + REWARD_RISK_MULTIPLE * (signal.entry_price - signal.stop_loss)
        self.assertAlmostEqual(signal.target_price, expected_target)

    def test_no_signal_when_volume_not_confirmed(self):
        strategy = VolumeBackedBreakoutPoolAStrategy()
        # Same price breakout, but volume stays at 1.0x average -- below the 1.5x threshold.
        df = strategy.precompute(_flat_series_with_breakout(breakout_volume_multiple=1.0))
        last_row = list(df.itertuples(index=False))[-1]
        self.assertFalse(bool(last_row.qualifies))
        self.assertIsNone(strategy.entry_signal_at(last_row))

    def test_no_signal_when_no_new_high(self):
        strategy = VolumeBackedBreakoutPoolAStrategy()
        n = 60
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        flat = [100.0] * n
        df_raw = pd.DataFrame({"Open": flat, "High": [100.5] * n, "Low": [99.5] * n, "Close": flat,
                                "Volume": [3000] * n}, index=idx)  # high volume throughout, but no breakout
        df = strategy.precompute(df_raw)
        last_row = list(df.itertuples(index=False))[-1]
        self.assertFalse(bool(last_row.qualifies))

    def test_no_signal_without_sufficient_history(self):
        strategy = VolumeBackedBreakoutPoolAStrategy()
        short_df = _flat_series_with_breakout(n=15)  # short of min_lookback_days
        df = strategy.precompute(short_df)
        last_row = list(df.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))


class TestExitSignal(unittest.TestCase):
    def test_no_signal_based_exit_ever_fires(self):
        """Entirely engine-mechanical now (stop-loss or target_price, both
        handled by paper_trading_engine.py) -- exit_signal_at() must
        always return None, matching the original's design exactly."""
        strategy = VolumeBackedBreakoutPoolAStrategy()

        class Row:
            date = None
            Close = 999.0
        self.assertIsNone(strategy.exit_signal_at(Row(), open_position=None))


class TestConstants(unittest.TestCase):
    def test_documented_parameters(self):
        self.assertEqual(BREAKOUT_LOOKBACK_DAYS, 20)
        self.assertAlmostEqual(VOLUME_MULTIPLE, 1.5)
        self.assertAlmostEqual(REWARD_RISK_MULTIPLE, 2.0)
        self.assertEqual(VolumeBackedBreakoutPoolAStrategy.max_units, 1)
        self.assertAlmostEqual(VolumeBackedBreakoutPoolAStrategy.risk_pct_per_unit, 0.01)


if __name__ == "__main__":
    unittest.main()
