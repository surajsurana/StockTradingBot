"""
Unit tests for ma_crossover.py's and mean_reversion.py's diagnose() --
the step-by-step funnel breakdown used by run_daily.py's daily scan
summary. Confirms diagnose() reports the right stage for each case AND
agrees with generate_signal()'s final pass/fail (the two are meant to be
kept in sync by hand, so any drift should show up here). Run with:

    python test_strategy_diagnose.py
"""

import unittest
import pandas as pd

from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy


def _ma_crossover_history(n_days: int = 100, volume_today: float = 300_000) -> pd.DataFrame:
    """Flat then a final-day jump -- fires a clean crossover, volume- and
    momentum-confirmed (see test_ma_crossover_volume.py / _momentum_resistance.py)."""
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days)
    prices = [100.0] * (n_days - 1) + [130.0]
    close = pd.Series(prices, index=dates)
    volume = pd.Series([100_000] * n_days, index=dates)
    volume.iloc[-1] = volume_today
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.98,
        "Close": close, "Volume": volume,
    }, index=dates)


def _flat_history(n_days: int = 80) -> pd.DataFrame:
    """No crossover, no oversold condition -- essentially flat, with a
    tiny +/-0.05 wiggle so RSI has actual gains/losses to compute a ratio
    from (a perfectly constant price makes RSI genuinely NaN -- no
    movement at all to measure -- which is correct indicator behavior,
    not something worth relying on for a "nothing's happening" fixture)."""
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days)
    close = pd.Series([100.0 + (0.05 if i % 2 == 0 else -0.05) for i in range(n_days)], index=dates)
    volume = pd.Series([100_000] * n_days, index=dates)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.98,
        "Close": close, "Volume": volume,
    }, index=dates)


def _oversold_transition_history(n_days: int = 60) -> pd.DataFrame:
    """Same tiny-wiggle flat baseline, then a single sharp final-day drop --
    RSI/Bollinger oversold transition fires exactly on the final day, not before."""
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days)
    prices = [100.0 + (0.05 if i % 2 == 0 else -0.05) for i in range(n_days - 1)] + [93.0]
    close = pd.Series(prices, index=dates)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.98, "Close": close}, index=dates)


class TestMACrossoverDiagnose(unittest.TestCase):
    def test_all_gates_pass_matches_generate_signal(self):
        history = _ma_crossover_history()
        strategy = MACrossoverStrategy()

        diagnosis = strategy.diagnose(history)
        signal = strategy.generate_signal(history)

        self.assertTrue(diagnosis["sufficient_history"])
        self.assertTrue(diagnosis["crossed_up"])
        self.assertTrue(diagnosis["volume_confirmed"])
        self.assertTrue(diagnosis["momentum_confirmed"])
        self.assertTrue(diagnosis["valid_stop"])
        self.assertIsNotNone(diagnosis["signal"])
        self.assertIsNotNone(signal)

    def test_insufficient_history(self):
        strategy = MACrossoverStrategy()
        diagnosis = strategy.diagnose(_ma_crossover_history(n_days=10))
        self.assertFalse(diagnosis["sufficient_history"])
        self.assertIsNone(diagnosis["crossed_up"])
        self.assertIsNone(diagnosis["signal"])

    def test_no_crossover_stops_at_that_gate(self):
        strategy = MACrossoverStrategy()
        diagnosis = strategy.diagnose(_flat_history())
        self.assertTrue(diagnosis["sufficient_history"])
        self.assertFalse(diagnosis["crossed_up"])
        self.assertIsNone(diagnosis["volume_confirmed"])  # never reached
        self.assertIsNone(diagnosis["signal"])

    def test_thin_volume_stops_at_that_gate(self):
        strategy = MACrossoverStrategy()
        diagnosis = strategy.diagnose(_ma_crossover_history(volume_today=100_000))  # not elevated
        self.assertTrue(diagnosis["crossed_up"])
        self.assertFalse(diagnosis["volume_confirmed"])
        self.assertIsNone(diagnosis["momentum_confirmed"])  # never reached
        self.assertIsNone(diagnosis["signal"])


class TestMeanReversionDiagnose(unittest.TestCase):
    def test_all_gates_pass_matches_generate_signal(self):
        history = _oversold_transition_history()
        strategy = MeanReversionStrategy()

        diagnosis = strategy.diagnose(history)
        signal = strategy.generate_signal(history)

        self.assertTrue(diagnosis["sufficient_history"])
        self.assertTrue(diagnosis["oversold_transition"])
        self.assertTrue(diagnosis["valid_stop"])
        self.assertTrue(diagnosis["valid_target"])
        self.assertIsNotNone(diagnosis["signal"])
        self.assertIsNotNone(signal)

    def test_insufficient_history(self):
        strategy = MeanReversionStrategy()
        diagnosis = strategy.diagnose(_oversold_transition_history(n_days=10))
        self.assertFalse(diagnosis["sufficient_history"])
        self.assertIsNone(diagnosis["signal"])

    def test_no_oversold_transition_stops_at_that_gate(self):
        strategy = MeanReversionStrategy()
        diagnosis = strategy.diagnose(_flat_history())
        self.assertTrue(diagnosis["sufficient_history"])
        self.assertFalse(diagnosis["oversold_transition"])
        self.assertIsNone(diagnosis["valid_stop"])  # never reached
        self.assertIsNone(diagnosis["signal"])


if __name__ == "__main__":
    unittest.main()
