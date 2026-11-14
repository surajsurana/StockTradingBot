"""
Unit tests for swing_research/research_director.py's
run_overnight_return_us_experiment() -- the US-market Pool I port of the
India overnight_return_anomaly strategy (us_overnight_return_anomaly, added
2026-11-11).

The Strategy class itself (OvernightReturnAnomalyStrategy) is unmodified and
already covered by test_overnight_return_anomaly.py's hand-built fixtures --
these tests instead cover what's genuinely new here: that the US wrapper
wires the EXACT SAME class to the US published-strategy record, to a real
cross-sectional overnight-return-percentile column, and -- CRITICALLY for
this strategy specifically, unlike every other US port -- to the
fill_timing="close_to_next_open" execution-realism override, not the
platform default "next_day_open". Getting that wrong would silently produce
a meaningless verdict (see the wrapper's own docstring and
research_roadmap.py's known_weaknesses note for this candidate). No network
calls.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from swing_research.published_research_analyst import OVERNIGHT_RETURN_ANOMALY_US
from swing_research.research_director import run_overnight_return_us_experiment
from swing_research.strategies.overnight_return_anomaly import OvernightReturnAnomalyStrategy


def _flat_series(n=40, start_price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price + i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestRunOvernightReturnUsExperiment(unittest.TestCase):
    def test_wires_the_unmodified_india_strategy_and_the_us_published_record(self):
        data = {"AAPL": _flat_series(), "MSFT": _flat_series(start_price=50.0)}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["strategy"] = strategy
            captured["published"] = published
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            captured["walk_forward_fn"] = kwargs.get("walk_forward_fn")
            captured["full_period_trade_adjuster"] = kwargs.get("full_period_trade_adjuster")
            return "EXP-999"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            exp_id = run_overnight_return_us_experiment(
                data=data, start_date=min(df.index.date.min() for df in data.values()),
                end_date=max(df.index.date.max() for df in data.values()),
            )

        self.assertEqual(exp_id, "EXP-999")
        self.assertIsInstance(captured["strategy"], OvernightReturnAnomalyStrategy)
        self.assertIs(captured["published"], OVERNIGHT_RETURN_ANOMALY_US)

    def test_uses_close_to_next_open_fill_timing_not_the_platform_default(self):
        # The one check that matters most for this specific strategy: next_day_open would shift
        # BOTH legs to an Open price, contaminating the exact effect under test (see EXP-076's
        # documented India history). This must never silently regress to the platform default.
        data = {"AAPL": _flat_series()}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["walk_forward_fn"] = kwargs.get("walk_forward_fn")
            captured["full_period_trade_adjuster"] = kwargs.get("full_period_trade_adjuster")
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-998"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_overnight_return_us_experiment(
                data=data, start_date=data["AAPL"].index.date.min(),
                end_date=data["AAPL"].index.date.max(),
            )

        walk_forward_fn = captured["walk_forward_fn"]
        self.assertIsNotNone(walk_forward_fn)
        self.assertEqual(walk_forward_fn.keywords.get("fill_timing"), "close_to_next_open")
        self.assertIsNotNone(captured["full_period_trade_adjuster"])
        self.assertEqual(captured["extra_parameters"]["execution_realism_config"]["fill_timing"],
                          "close_to_next_open")

    def test_the_extra_column_is_a_real_cross_sectional_overnight_percentile(self):
        # AAPL gaps up at the open every day, MSFT doesn't -- AAPL must end up in the top decile
        # (a HIGH percentile), not an arbitrary/placeholder value.
        n = 30
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        flat_close = [100.0] * n
        gapping_open = [100.0] + [102.0] * (n - 1)
        flat_open = [100.0] * n
        data = {
            "AAPL": pd.DataFrame({"Open": gapping_open, "High": gapping_open, "Low": flat_close,
                                   "Close": flat_close, "Volume": [1000] * n}, index=idx),
            "MSFT": pd.DataFrame({"Open": flat_open, "High": flat_close, "Low": flat_close,
                                   "Close": flat_close, "Volume": [1000] * n}, index=idx),
        }
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            return "EXP-997"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_overnight_return_us_experiment(
                data=data, start_date=idx.date.min(), end_date=idx.date.max(),
            )

        cols = captured["extra_columns_by_symbol"]
        self.assertEqual(set(cols), {"AAPL", "MSFT"})
        self.assertEqual(cols["AAPL"].name, "overnight_percentile")
        last_aapl = cols["AAPL"].dropna().iloc[-1]
        last_msft = cols["MSFT"].dropna().iloc[-1]
        self.assertGreater(last_aapl, last_msft)   # the overnight gainer ranks above the flat one

    def test_extra_parameters_match_the_documented_rules(self):
        data = {"AAPL": _flat_series()}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-996"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_overnight_return_us_experiment(
                data=data, start_date=data["AAPL"].index.date.min(),
                end_date=data["AAPL"].index.date.max(),
            )

        params = captured["extra_parameters"]
        self.assertEqual(params["overnight_return_percentile_threshold"], 90.0)
        self.assertEqual(params["overnight_return_formation_days"], 21)
        self.assertEqual(params["overnight_return_holding_period_trading_days"], 1)
        self.assertTrue(params["overnight_return_single_vintage"])
        self.assertFalse(params["overnight_return_percentile_based_early_exit"])
        self.assertEqual(params["overnight_return_market"], "US")


if __name__ == "__main__":
    unittest.main()
