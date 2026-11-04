"""
Unit tests for swing_research/research_director.py's
run_turn_of_month_us_experiment() -- the US-market port of the India
turn_of_month strategy (us_turn_of_month).

The Strategy class itself (TurnOfMonthStrategy) and its per-month
combined-rank diversification fix (compute_monthly_eligibility()) are
unmodified and already covered by test_turn_of_month.py's hand-built
fixtures -- these tests instead cover the one thing genuinely new here:
that the US wrapper wires the EXACT SAME class and the EXACT SAME
eligibility-column function to the US published-strategy record, the same
way run_minervini_us_experiment()/run_short_term_reversal_us_experiment()
wire their own strategies. No network calls.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from swing_research.published_research_analyst import TURN_OF_MONTH_US
from swing_research.research_director import run_turn_of_month_us_experiment
from swing_research.strategies.turn_of_month import ELIGIBLE_PER_MONTH, TurnOfMonthStrategy


def _flat_series(n=70, start_price=100.0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    closes = [start_price] * n
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestRunTurnOfMonthUsExperiment(unittest.TestCase):
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
            exp_id = run_turn_of_month_us_experiment(
                data=data, start_date=min(df.index.date.min() for df in data.values()),
                end_date=max(df.index.date.max() for df in data.values()),
            )

        self.assertEqual(exp_id, "EXP-999")
        self.assertIsInstance(captured["strategy"], TurnOfMonthStrategy)
        self.assertIs(captured["published"], TURN_OF_MONTH_US)

    def test_the_extra_columns_are_the_real_per_month_eligibility_fix(self):
        # 50 symbols, same shape compute_monthly_eligibility() is tested against directly in
        # test_turn_of_month.py -- here we only check the US wrapper actually calls it (real
        # eligibility values, named "eligible_this_month"), not a placeholder.
        n, n_symbols = 250, 50
        idx = pd.bdate_range("2020-01-01", periods=n)
        closes = [100.0] * n
        data = {
            f"SYM{i}": pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                                      "Volume": [1000] * n}, index=idx)
            for i in range(n_symbols)
        }
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_columns_by_symbol"] = extra_columns_by_symbol
            return "EXP-998"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_turn_of_month_us_experiment(
                data=data, start_date=idx.date.min(), end_date=idx.date.max(),
            )

        cols = captured["extra_columns_by_symbol"]
        self.assertEqual(set(cols), set(data))
        for series in cols.values():
            self.assertEqual(series.name, "eligible_this_month")
        first_month = list(cols.values())[0].index.to_period("M")[0]
        eligible_count = sum(
            1 for series in cols.values()
            if series[series.index.to_period("M") == first_month].any()
        )
        self.assertEqual(eligible_count, ELIGIBLE_PER_MONTH)

    def test_extra_parameters_match_the_documented_rules(self):
        data = {"AAPL": _flat_series()}
        captured = {}

        def fake_generic(strategy, published, data_, start_date, end_date, starting_capital,
                          n_walk_forward_windows, extra_columns_by_symbol=None, **kwargs):
            captured["extra_parameters"] = kwargs.get("extra_parameters")
            return "EXP-997"

        with patch("swing_research.research_director.run_generic_swing_experiment", fake_generic):
            run_turn_of_month_us_experiment(
                data=data, start_date=data["AAPL"].index.date.min(),
                end_date=data["AAPL"].index.date.max(),
            )

        params = captured["extra_parameters"]
        self.assertEqual(params["turn_of_month_stop_loss_pct"], 0.08)
        self.assertEqual(params["turn_of_month_exit_lag_trading_days"], 3)
        self.assertTrue(params["turn_of_month_no_cross_sectional_selection"])
        self.assertFalse(params["turn_of_month_percentile_based_early_exit"])
        self.assertEqual(params["turn_of_month_eligible_per_month"], ELIGIBLE_PER_MONTH)
        self.assertEqual(params["turn_of_month_market"], "US")


class TestUsTurnOfMonthPaperWiring(unittest.TestCase):
    """us_turn_of_month's PAPER_US_STRATEGIES entry must use the SAME extra-columns function
    (compute_monthly_eligibility, same "eligible_this_month" column) the research wrapper above
    passes to the backtest -- paper must trade exactly what was tested."""

    def test_paper_entry_uses_the_same_strategy_and_eligibility_function(self):
        from swing_research.strategy_catalog import PAPER_US_STRATEGIES

        self.assertIn("us_turn_of_month", PAPER_US_STRATEGIES)
        factory, extra_fn = PAPER_US_STRATEGIES["us_turn_of_month"]
        self.assertIsInstance(factory(), TurnOfMonthStrategy)

        data = {"AAPL": _flat_series(n=70)}
        cols = extra_fn(data)
        self.assertEqual(set(cols), {"AAPL"})
        self.assertEqual(cols["AAPL"].name, "eligible_this_month")


if __name__ == "__main__":
    unittest.main()
