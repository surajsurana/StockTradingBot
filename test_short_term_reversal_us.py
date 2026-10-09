"""
Unit tests for swing_research/research_director.py's
run_short_term_reversal_us_experiment() -- the US-market Pool I port of the
India short_term_reversal strategy (us_short_term_reversal, added 2026-10-07).

The Strategy class itself (ShortTermReversalStrategy) is unmodified and
already covered by test_short_term_reversal.py's hand-built fixtures -- these
tests instead cover the one thing genuinely new here: that the US wrapper
wires the EXACT SAME class to the US published-strategy record and to a
real cross-sectional reversal-percentile column, the same way
run_minervini_us_experiment()/run_cross_sectional_momentum_us_experiment()
wire their own strategies. No network calls.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from swing_research.published_research_analyst import SHORT_TERM_REVERSAL_US
from swing_research.research_director import run_short_term_reversal_us_experiment
from swing_research.strategies.short_term_reversal import ShortTermReversalStrategy


def _flat_series(n=40, start_price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price + i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestRunShortTermReversalUsExperiment(unittest.TestCase):
    def test_wires_the_unmodified_india_strategy_and_the_us_published_record(self):
        data = {"AAPL": _flat_series(), "MSFT": _flat_series(start_price=50.0)}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["strategy"] = strategy
            captured["published"] = published
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-999"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            exp_id = run_short_term_reversal_us_experiment(
                data=data, start_date=min(df.index.date.min() for df in data.values()),
                end_date=max(df.index.date.max() for df in data.values()),
            )

        self.assertEqual(exp_id, "EXP-999")
        self.assertIsInstance(captured["strategy"], ShortTermReversalStrategy)
        self.assertIs(captured["published"], SHORT_TERM_REVERSAL_US)

    def test_the_extra_column_is_a_real_cross_sectional_reversal_percentile(self):
        # AAPL falls hard over the formation window, MSFT doesn't -- AAPL must end up in the
        # bottom decile (a LOW percentile), not an arbitrary/placeholder value.
        n = 30
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        falling = [100.0 - i for i in range(n)]
        rising = [100.0 + i for i in range(n)]
        data = {
            "AAPL": pd.DataFrame({"Open": falling, "High": falling, "Low": falling, "Close": falling,
                                   "Volume": [1000] * n}, index=idx),
            "MSFT": pd.DataFrame({"Open": rising, "High": rising, "Low": rising, "Close": rising,
                                   "Volume": [1000] * n}, index=idx),
        }
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            return "EXP-998"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_short_term_reversal_us_experiment(
                data=data, start_date=idx.date.min(), end_date=idx.date.max(),
            )

        cols = captured["extra_columns_by_symbol"]
        self.assertEqual(set(cols), {"AAPL", "MSFT"})
        self.assertEqual(cols["AAPL"].name, "reversal_percentile")
        last_aapl = cols["AAPL"].dropna().iloc[-1]
        last_msft = cols["MSFT"].dropna().iloc[-1]
        self.assertLess(last_aapl, last_msft)   # the loser ranks below the winner

    def test_extra_parameters_match_the_documented_rules(self):
        data = {"AAPL": _flat_series()}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-997"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_short_term_reversal_us_experiment(
                data=data, start_date=data["AAPL"].index.date.min(),
                end_date=data["AAPL"].index.date.max(),
            )

        params = captured["extra_parameters"]
        self.assertEqual(params["short_term_reversal_percentile_threshold"], 10.0)
        self.assertEqual(params["short_term_reversal_formation_days"], 21)
        self.assertEqual(params["short_term_reversal_holding_period_trading_days"], 21)
        self.assertTrue(params["short_term_reversal_single_vintage"])
        self.assertEqual(params["short_term_reversal_market"], "US")


if __name__ == "__main__":
    unittest.main()
