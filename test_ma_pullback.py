"""
Unit tests for swing_research/strategies/ma_pullback.py -- the fully
faithful Pool A port of strategies/pullback_continuation.py. Covers the
uptrend + pullback + bullish-reaction entry gate, the stop-loss
construction, and the fixed price target (restored 2026-09-06 via
Signal.target_price -- "we must not play with strategy rules" -- after an
earlier version had substituted a time-stop). Run with:

    python test_ma_pullback.py
"""

import unittest

import pandas as pd

from swing_research.strategies.ma_pullback import (
    FAST_MA_PERIOD, MIN_REWARD_RISK, MaPullbackStrategy, SLOW_MA_PERIOD, TARGET_LOOKBACK_DAYS,
)


def _uptrend_with_pullback(n=90, high_before_pullback=None):
    """A steady uptrend for most of the window, then one clean pullback-and-
    bounce day near the end: price dips to touch the 20-day MA (a lower
    Low, still above the MA) and closes back up green. Optionally spike a
    high earlier in the window (high_before_pullback) so the target
    (rolling 20-day high) sits meaningfully above entry, clearing the
    1.8x reward:risk gate."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [100.0 + i * 0.5 for i in range(n)]
    opens = list(closes)
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]

    if high_before_pullback is not None:
        highs[-5] = high_before_pullback   # within the 20-day target lookback window

    # Engineer the last day as a pullback-and-bounce.
    ma20_est = sum(closes[-21:-1]) / 20
    lows[-1] = ma20_est * 1.005     # touches within the 1.5% pullback band
    opens[-1] = ma20_est * 0.99     # opened lower
    closes[-1] = ma20_est * 1.02    # closed above the MA and above its own open, green
    highs[-1] = max(highs[-1], closes[-1])

    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestPrecomputeAndEntry(unittest.TestCase):
    def test_no_signal_without_a_wide_enough_target(self):
        """Without an engineered high nearby, the rolling 20-day high sits
        too close to entry to clear MIN_REWARD_RISK -- correctly no signal,
        same as the original's own "no room left to the target" gate."""
        strategy = MaPullbackStrategy()
        df = strategy.precompute(_uptrend_with_pullback())
        last_row = list(df.itertuples(index=False))[-1]
        self.assertTrue(bool(last_row.qualifies))   # the entry PATTERN still qualifies...
        self.assertIsNone(strategy.entry_signal_at(last_row))   # ...but the reward:risk gate blocks it

    def test_fires_with_target_and_reward_risk_when_room_exists(self):
        strategy = MaPullbackStrategy()
        df = strategy.precompute(_uptrend_with_pullback(high_before_pullback=200.0))
        last_row = list(df.itertuples(index=False))[-1]
        signal = strategy.entry_signal_at(last_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertAlmostEqual(signal.target_price, 200.0)
        reward_risk = (signal.target_price - signal.entry_price) / (signal.entry_price - signal.stop_loss)
        self.assertGreaterEqual(reward_risk, MIN_REWARD_RISK - 1e-6)

    def test_no_signal_without_sufficient_history(self):
        strategy = MaPullbackStrategy()
        short_df = _uptrend_with_pullback(n=30)  # short of SLOW_MA_PERIOD + 2
        df = strategy.precompute(short_df)
        last_row = list(df.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))

    def test_no_signal_when_not_in_uptrend(self):
        strategy = MaPullbackStrategy()
        n = 90
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        flat = [100.0] * n   # fast_ma == slow_ma, never a confirmed uptrend
        df_raw = pd.DataFrame({"Open": flat, "High": flat, "Low": flat, "Close": flat,
                                "Volume": [1000] * n}, index=idx)
        df = strategy.precompute(df_raw)
        last_row = list(df.itertuples(index=False))[-1]
        self.assertFalse(bool(last_row.qualifies))

    def test_stop_loss_bounds_enforced(self):
        strategy = MaPullbackStrategy()
        df = strategy.precompute(_uptrend_with_pullback(high_before_pullback=200.0))
        last_row = list(df.itertuples(index=False))[-1]
        signal = strategy.entry_signal_at(last_row)
        self.assertIsNotNone(signal)
        risk_pct = (signal.entry_price - signal.stop_loss) / signal.entry_price
        self.assertGreaterEqual(risk_pct, 0.015 - 1e-6)
        self.assertLessEqual(risk_pct, 0.10 + 1e-6)


class TestExitSignal(unittest.TestCase):
    def test_no_signal_based_exit_ever_fires(self):
        """Entirely engine-mechanical now (stop-loss or target_price, both
        handled by paper_trading_engine.py) -- exit_signal_at() must
        always return None, matching the original's design exactly."""
        strategy = MaPullbackStrategy()

        class Row:
            date = None
            Close = 999.0
        self.assertIsNone(strategy.exit_signal_at(Row(), open_position=None))


class TestConstants(unittest.TestCase):
    def test_documented_parameters(self):
        self.assertEqual(FAST_MA_PERIOD, 20)
        self.assertEqual(SLOW_MA_PERIOD, 50)
        self.assertEqual(TARGET_LOOKBACK_DAYS, 20)
        self.assertAlmostEqual(MIN_REWARD_RISK, 1.8)
        self.assertEqual(MaPullbackStrategy.max_units, 1)
        self.assertAlmostEqual(MaPullbackStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(MaPullbackStrategy.min_lookback_days, 52)


if __name__ == "__main__":
    unittest.main()
