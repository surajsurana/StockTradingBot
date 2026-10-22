"""
Unit tests for swing_research/research_director.py's
run_high_volume_return_premium_us_experiment() -- the US-market Pool I port of
the India high_volume_return_premium strategy (us_high_volume_return_premium).

The Strategy class itself (HighVolumeReturnPremiumStrategy) is unmodified and
already covered by test_high_volume_return_premium.py's hand-built fixtures --
these tests instead cover the one thing genuinely new here: that the US
wrapper wires the EXACT SAME class to the US published-strategy record and to
a real cross-sectional volume-shock-percentile column, the same way
run_minervini_us_experiment()/run_short_term_reversal_us_experiment() wire
their own strategies. No network calls.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from swing_research.published_research_analyst import HIGH_VOLUME_RETURN_PREMIUM_US
from swing_research.research_director import run_high_volume_return_premium_us_experiment
from swing_research.strategies.high_volume_return_premium import HighVolumeReturnPremiumStrategy


def _flat_series(n=270, start_price=100.0, volume=1000):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price] * n
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [volume] * n}, index=idx)


class TestRunHighVolumeReturnPremiumUsExperiment(unittest.TestCase):
    def test_wires_the_unmodified_india_strategy_and_the_us_published_record(self):
        data = {"AAPL": _flat_series(), "MSFT": _flat_series(start_price=50.0)}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["strategy"] = strategy
            captured["published"] = published
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-996"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            exp_id = run_high_volume_return_premium_us_experiment(
                data=data, start_date=min(df.index.date.min() for df in data.values()),
                end_date=max(df.index.date.max() for df in data.values()),
            )

        self.assertEqual(exp_id, "EXP-996")
        self.assertIsInstance(captured["strategy"], HighVolumeReturnPremiumStrategy)
        self.assertIs(captured["published"], HIGH_VOLUME_RETURN_PREMIUM_US)

    def test_the_extra_column_is_a_real_cross_sectional_volume_shock_percentile(self):
        # AAPL has a hard recent volume spike relative to its own baseline, MSFT doesn't -- AAPL
        # must end up with a HIGHER percentile, not an arbitrary/placeholder value.
        n = 270
        idx = pd.date_range("2020-01-01", periods=n, freq="D")

        def make_df(recent_volume):
            volumes = [1000] * (n - 5) + [recent_volume] * 5
            return pd.DataFrame({"Open": [100.0] * n, "High": [100.0] * n, "Low": [100.0] * n,
                                  "Close": [100.0] * n, "Volume": volumes}, index=idx)

        data = {"AAPL": make_df(5000), "MSFT": make_df(1000)}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            return "EXP-995"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_high_volume_return_premium_us_experiment(
                data=data, start_date=idx.date.min(), end_date=idx.date.max(),
            )

        cols = captured["extra_columns_by_symbol"]
        self.assertEqual(set(cols), {"AAPL", "MSFT"})
        self.assertEqual(cols["AAPL"].name, "volume_shock_percentile")
        last_aapl = cols["AAPL"].dropna().iloc[-1]
        last_msft = cols["MSFT"].dropna().iloc[-1]
        self.assertGreater(last_aapl, last_msft)   # the volume-shock symbol ranks above the flat one

    def test_extra_parameters_match_the_documented_rules(self):
        data = {"AAPL": _flat_series()}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-994"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_high_volume_return_premium_us_experiment(
                data=data, start_date=data["AAPL"].index.date.min(),
                end_date=data["AAPL"].index.date.max(),
            )

        params = captured["extra_parameters"]
        self.assertEqual(params["high_volume_percentile_threshold"], 90.0)
        self.assertEqual(params["high_volume_shock_recent_days"], 5)
        self.assertEqual(params["high_volume_shock_baseline_days"], 252)
        self.assertEqual(params["high_volume_holding_period_trading_days"], 21)
        self.assertTrue(params["high_volume_single_vintage"])
        self.assertEqual(params["high_volume_market"], "US")


if __name__ == "__main__":
    unittest.main()
