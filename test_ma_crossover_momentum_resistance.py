"""
Unit tests for ma_crossover.py's momentum confirmation (Rate of Change)
and resistance-aware target capping. Run with:

    python test_ma_crossover_momentum_resistance.py
"""

import unittest
import pandas as pd

from strategies.ma_crossover import MACrossoverStrategy


def _crossover_price_history(n_days: int = 100, spike_day_offset: int = None,
                              spike_high: float = None) -> pd.DataFrame:
    """
    Same flat-then-final-day-jump shape as test_ma_crossover_volume.py's
    helper (crossover fires on exactly the final day), optionally with one
    day's High raised to spike_high at spike_day_offset days before the
    end -- used to plant a "resistance" level in the prior price history
    without disturbing the crossover mechanics (a single elevated High
    among 50-99 days has negligible effect on the 20/50-day Close-based
    moving averages that drive the crossover itself).
    """
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days)
    prices = [100.0] * (n_days - 1) + [130.0]

    close = pd.Series(prices, index=dates)
    high = close * 1.01
    low = close * 0.98
    # today's volume must be elevated relative to its own trailing average
    # to pass the (default-on) volume confirmation gate -- these tests are
    # about momentum/resistance, not volume, so this just neutralizes it.
    volume = pd.Series([100_000] * n_days, index=dates)
    volume.iloc[-1] = 300_000

    if spike_day_offset is not None:
        high.iloc[-1 - spike_day_offset] = spike_high

    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates)


NEGATIVE_MOMENTUM_PERIOD = 60  # deliberately > slow_period (50)


def _crossover_with_negative_recent_momentum(n_days: int = 100) -> pd.DataFrame:
    """
    A crossover fires on the final day, but the close NEGATIVE_MOMENTUM_PERIOD
    days ago was HIGHER than today's close -- ROC is negative despite the
    crossover completing today. The reference day is placed further back
    than slow_period (50), so it sits entirely outside both the 20-day and
    50-day moving-average windows and can't disturb the crossover mechanics
    at all -- only the ROC calculation ever reads it.
    """
    df = _crossover_price_history(n_days=n_days)
    spike_index = -1 - NEGATIVE_MOMENTUM_PERIOD
    df.iloc[spike_index, df.columns.get_loc("Close")] = 200.0
    return df


class TestMomentumConfirmation(unittest.TestCase):
    def test_positive_momentum_crossover_fires(self):
        history = _crossover_price_history()
        strategy = MACrossoverStrategy(require_momentum_confirmation=True,
                                        momentum_period=NEGATIVE_MOMENTUM_PERIOD)

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)
        self.assertIn("momentum", signal.reason.lower())

    def test_negative_momentum_crossover_does_not_fire(self):
        history = _crossover_with_negative_recent_momentum()
        strategy = MACrossoverStrategy(require_momentum_confirmation=True,
                                        momentum_period=NEGATIVE_MOMENTUM_PERIOD)

        signal = strategy.generate_signal(history)

        self.assertIsNone(signal)

    def test_momentum_confirmation_disabled_ignores_negative_momentum(self):
        history = _crossover_with_negative_recent_momentum()
        strategy = MACrossoverStrategy(require_momentum_confirmation=False,
                                        momentum_period=NEGATIVE_MOMENTUM_PERIOD)

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)

    def test_momentum_on_by_default(self):
        # Backtested (150 Nifty 500 symbols, 3y): win rate 34.7%->36.0%,
        # total P&L +Rs.3,043.56->+Rs.5,230.28, max drawdown 10.9%->9.8% --
        # a clean improvement, same pattern as volume confirmation. On by default.
        strategy = MACrossoverStrategy()
        self.assertTrue(strategy.require_momentum_confirmation)


class TestResistanceAwareTarget(unittest.TestCase):
    def test_target_capped_at_nearby_resistance(self):
        # Theoretical 2:1 target for this setup is ~150; a resistance high
        # of 140 planted 20 days before today sits between entry (130) and
        # that target, so it should cap the target down to 140.
        history = _crossover_price_history(spike_day_offset=20, spike_high=140.0)
        strategy = MACrossoverStrategy(use_resistance_aware_target=True)

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.target, 140.0, places=2)
        self.assertIn("resistance", signal.reason.lower())

    def test_resistance_beyond_theoretical_target_does_not_cap(self):
        # A resistance level far above the theoretical target shouldn't
        # override it -- the cap only applies when resistance is a NEARER
        # ceiling than the standard 2:1 target.
        history = _crossover_price_history(spike_day_offset=20, spike_high=500.0)
        strategy = MACrossoverStrategy(use_resistance_aware_target=True)

        uncapped = MACrossoverStrategy(use_resistance_aware_target=False).generate_signal(
            _crossover_price_history()
        )
        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.target, uncapped.target, places=2)

    def test_resistance_aware_target_disabled_uses_theoretical_target(self):
        history = _crossover_price_history(spike_day_offset=20, spike_high=140.0)
        strategy = MACrossoverStrategy(use_resistance_aware_target=False)

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)
        self.assertGreater(signal.target, 140.0)

    def test_resistance_aware_target_off_by_default(self):
        strategy = MACrossoverStrategy()
        self.assertFalse(strategy.use_resistance_aware_target)


if __name__ == "__main__":
    unittest.main()
