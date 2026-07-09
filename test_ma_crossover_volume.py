"""
Unit tests for ma_crossover.py's volume confirmation filter -- a crossover
on unusually thin volume is more likely noise than a real move, so a fresh
signal now also requires today's volume to be at least
volume_confirmation_multiple x its own recent average. Run with:

    python test_ma_crossover_volume.py
"""

import unittest
import numpy as np
import pandas as pd

from strategies.ma_crossover import MACrossoverStrategy


def _crossover_price_history(volume_today: float, volume_avg: float = 100_000, n_days: int = 80) -> pd.DataFrame:
    """
    Builds a synthetic OHLCV history that produces a fast-MA-crosses-above-
    slow-MA signal on exactly the final day: flat prices for every day
    before that (so the 20-day and 50-day averages sit together, satisfying
    "yesterday fast_ma <= slow_ma"), then one sharp single-day jump on the
    final day that pulls the 20-day average above the 50-day average (which
    barely moves, being 1/50th as sensitive to one new price). Every day's
    volume is volume_avg except the final day, which uses volume_today --
    isolates the volume check from the crossover mechanics themselves.
    """
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days)
    prices = [100.0] * (n_days - 1) + [130.0]  # flat, then a sharp final-day jump

    close = pd.Series(prices, index=dates)
    volume = pd.Series([volume_avg] * n_days, index=dates)
    volume.iloc[-1] = volume_today

    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.98,
        "Close": close, "Volume": volume,
    }, index=dates)


class TestVolumeConfirmation(unittest.TestCase):
    def test_high_volume_crossover_fires(self):
        history = _crossover_price_history(volume_today=200_000, volume_avg=100_000)
        strategy = MACrossoverStrategy()

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)
        self.assertIn("volume", signal.reason.lower())

    def test_low_volume_crossover_does_not_fire(self):
        history = _crossover_price_history(volume_today=50_000, volume_avg=100_000)
        strategy = MACrossoverStrategy()

        signal = strategy.generate_signal(history)

        self.assertIsNone(signal)

    def test_volume_confirmation_disabled_ignores_thin_volume(self):
        history = _crossover_price_history(volume_today=50_000, volume_avg=100_000)
        strategy = MACrossoverStrategy(require_volume_confirmation=False)

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)

    def test_exactly_at_threshold_fires(self):
        # 1.5x is the default multiple -- exactly 1.5x should pass (>=, not >)
        history = _crossover_price_history(volume_today=150_000, volume_avg=100_000)
        strategy = MACrossoverStrategy()

        signal = strategy.generate_signal(history)

        self.assertIsNotNone(signal)

    def test_custom_multiple_respected(self):
        history = _crossover_price_history(volume_today=250_000, volume_avg=100_000)
        strict_strategy = MACrossoverStrategy(volume_confirmation_multiple=3.0)
        lenient_strategy = MACrossoverStrategy(volume_confirmation_multiple=2.0)

        self.assertIsNone(strict_strategy.generate_signal(history))
        self.assertIsNotNone(lenient_strategy.generate_signal(history))


if __name__ == "__main__":
    unittest.main()
