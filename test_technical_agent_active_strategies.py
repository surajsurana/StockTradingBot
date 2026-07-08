"""
Mock-based unit tests for strategies/technical_agent.py's active_strategies
override on get_technical_signals() -- confirms it defaults to
settings.ACTIVE_STRATEGIES but respects an explicit override (what
cio/plan_state.py's effective_active_strategies() feeds it once Chief
Investment AI has a real monthly plan). Run with:

    python test_technical_agent_active_strategies.py
"""

import unittest
from unittest.mock import patch

import pandas as pd

from strategies.technical_agent import get_technical_signals


def _fake_price_history():
    dates = pd.date_range("2026-01-01", periods=5)
    return pd.DataFrame({"Close": [100, 101, 102, 103, 104]}, index=dates)


def _fake_regime_series(price_history):
    return pd.Series(True, index=price_history.index)


class _NullStrategy:
    """Always returns no signal -- just used to record which strategy keys
    get_technical_signals actually iterated over."""
    uses_regime_filter = False

    def generate_signal(self, price_history):
        return None


class TestActiveStrategiesOverride(unittest.TestCase):
    @patch("strategies.technical_agent.STRATEGY_REGISTRY", {
        "ma_crossover": _NullStrategy, "mean_reversion": _NullStrategy,
    })
    @patch("strategies.technical_agent.settings")
    def test_defaults_to_settings_active_strategies_when_not_passed(self, mock_settings):
        mock_settings.ACTIVE_STRATEGIES = ["ma_crossover"]
        mock_settings.USE_MARKET_REGIME_FILTER = False

        signals = get_technical_signals("TEST.NS", (ph := _fake_price_history()), _fake_regime_series(ph))

        self.assertEqual(set(signals.keys()), {"ma_crossover"})

    @patch("strategies.technical_agent.STRATEGY_REGISTRY", {
        "ma_crossover": _NullStrategy, "mean_reversion": _NullStrategy,
    })
    @patch("strategies.technical_agent.settings")
    def test_explicit_override_ignores_settings(self, mock_settings):
        mock_settings.ACTIVE_STRATEGIES = ["ma_crossover"]  # should be ignored
        mock_settings.USE_MARKET_REGIME_FILTER = False

        signals = get_technical_signals("TEST.NS", (ph := _fake_price_history()), _fake_regime_series(ph),
                                         active_strategies=["mean_reversion"])

        self.assertEqual(set(signals.keys()), {"mean_reversion"})

    @patch("strategies.technical_agent.STRATEGY_REGISTRY", {
        "ma_crossover": _NullStrategy, "mean_reversion": _NullStrategy,
    })
    @patch("strategies.technical_agent.settings")
    def test_empty_override_means_no_strategies_run(self, mock_settings):
        mock_settings.ACTIVE_STRATEGIES = ["ma_crossover", "mean_reversion"]
        mock_settings.USE_MARKET_REGIME_FILTER = False

        signals = get_technical_signals("TEST.NS", (ph := _fake_price_history()), _fake_regime_series(ph),
                                         active_strategies=[])

        self.assertEqual(signals, {})


if __name__ == "__main__":
    unittest.main()
