"""
Unit tests for swing_research/strategies/turn_of_month.py -- month-end
detection, the row-position-based 3-trading-day exit, the per-month
combined-rank diversification fix, and the absence of any percentile/
state-transition machinery (this signal has none). Run with:

    python test_turn_of_month.py
"""

import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.turn_of_month import (
    ELIGIBLE_PER_MONTH, STOP_LOSS_PCT, TOM_EXIT_LAG_TRADING_DAYS,
    TurnOfMonthStrategy, _symbol_month_priority_hash, compute_monthly_eligibility,
)


def _steady_series(n=70, start_price=100.0, eligible=True):
    idx = pd.bdate_range("2024-01-01", periods=n)
    closes = [start_price] * n
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                        "Volume": [1000] * n}, index=idx)
    df["eligible_this_month"] = eligible
    return df


def _dummy_position():
    return OpenPosition(symbol="TEST", direction="BUY",
                         units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                         stop_loss=92.0)


class TestSymbolMonthPriorityHash(unittest.TestCase):
    def test_deterministic_and_stable_across_calls(self):
        self.assertEqual(_symbol_month_priority_hash("RELIANCE.NS", 24289),
                          _symbol_month_priority_hash("RELIANCE.NS", 24289))

    def test_varies_by_month_for_the_same_symbol(self):
        hashes = {_symbol_month_priority_hash("RELIANCE.NS", m) for m in range(24289, 24301)}
        self.assertGreater(len(hashes), 1)

    def test_varies_by_symbol_for_the_same_month(self):
        hashes = {_symbol_month_priority_hash(s, 24289) for s in ["RELIANCE.NS", "TCS.NS", "INFY.NS", "ITC.NS"]}
        self.assertGreater(len(hashes), 1)


class TestComputeMonthlyEligibility(unittest.TestCase):
    def _synthetic_universe(self, n_symbols=50, n_days=250):
        idx = pd.bdate_range("2024-01-01", periods=n_days)
        closes = [100.0] * n_days
        data = {}
        for i in range(n_symbols):
            data[f"SYM{i}.NS"] = pd.DataFrame(
                {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [1000] * n_days},
                index=idx,
            )
        return data

    def test_exactly_eligible_per_month_symbols_are_eligible_each_month(self):
        data = self._synthetic_universe(n_symbols=50)
        eligibility = compute_monthly_eligibility(data, eligible_per_month=10)
        first_month = list(eligibility.values())[0].index.to_period("M")[0]
        count = sum(
            1 for series in eligibility.values()
            if series[series.index.to_period("M") == first_month].any()
        )
        self.assertEqual(count, 10)

    def test_a_symbols_eligibility_varies_across_months(self):
        data = self._synthetic_universe(n_symbols=50)
        eligibility = compute_monthly_eligibility(data, eligible_per_month=10)
        series = eligibility["SYM0.NS"]
        monthly_flags = series.groupby(series.index.to_period("M")).any()
        # With only 10/50 eligible each month, a single symbol should not
        # be eligible in EVERY month across an 11-12 month synthetic window.
        self.assertFalse(monthly_flags.all())

    def test_empty_data_returns_empty_dict(self):
        self.assertEqual(compute_monthly_eligibility({}), {})


class TestPrecompute(unittest.TestCase):
    def test_last_trading_day_of_each_full_month_is_flagged(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        flagged_dates = precomputed.index[precomputed["is_month_end"]].tolist()
        self.assertIn(pd.Timestamp("2024-01-31"), flagged_dates)
        self.assertIn(pd.Timestamp("2024-02-29"), flagged_dates)

    def test_ordinary_mid_month_day_is_not_flagged(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed.loc[pd.Timestamp("2024-01-15"), "is_month_end"]))

    def test_exit_day_is_exactly_n_trading_days_after_month_end(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        month_end_pos = precomputed.index.get_loc(pd.Timestamp("2024-01-31"))
        expected_exit_date = precomputed.index[month_end_pos + TOM_EXIT_LAG_TRADING_DAYS]
        self.assertTrue(bool(precomputed.loc[expected_exit_date, "is_tom_exit_day"]))

    def test_day_immediately_after_month_end_is_not_yet_the_exit_day(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        month_end_pos = precomputed.index.get_loc(pd.Timestamp("2024-01-31"))
        next_day = precomputed.index[month_end_pos + 1]
        self.assertFalse(bool(precomputed.loc[next_day, "is_tom_exit_day"]))

    def test_month_end_day_when_eligible_qualifies(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series(eligible=True)
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed.loc[pd.Timestamp("2024-01-31"), "qualifies_for_entry"]))

    def test_month_end_day_when_not_eligible_does_not_qualify(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series(eligible=False)
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed.loc[pd.Timestamp("2024-01-31"), "qualifies_for_entry"]))

    def test_missing_eligibility_column_defaults_to_disqualified(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        df = df.drop(columns=["eligible_this_month"])
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed.loc[pd.Timestamp("2024-01-31"), "qualifies_for_entry"]))


class TestEntrySignal(unittest.TestCase):
    def test_fires_on_the_month_end_row_when_eligible(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series(eligible=True)
        precomputed = strategy.precompute(df)
        row = precomputed.loc[[pd.Timestamp("2024-01-31")]].itertuples(index=False)
        signal = strategy.entry_signal_at(next(row))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_no_signal_on_the_month_end_row_when_not_eligible(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series(eligible=False)
        precomputed = strategy.precompute(df)
        row = precomputed.loc[[pd.Timestamp("2024-01-31")]].itertuples(index=False)
        self.assertIsNone(strategy.entry_signal_at(next(row)))

    def test_no_signal_on_an_ordinary_day(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        row = precomputed.loc[[pd.Timestamp("2024-01-15")]].itertuples(index=False)
        self.assertIsNone(strategy.entry_signal_at(next(row)))


class TestExitSignal(unittest.TestCase):
    def test_fires_on_the_tom_exit_day(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        month_end_pos = precomputed.index.get_loc(pd.Timestamp("2024-01-31"))
        exit_date = precomputed.index[month_end_pos + TOM_EXIT_LAG_TRADING_DAYS]
        row = next(precomputed.loc[[exit_date]].itertuples(index=False))
        exit_price = strategy.exit_signal_at(row, _dummy_position())
        self.assertAlmostEqual(exit_price, float(row.Close))

    def test_no_exit_the_day_before_the_exit_day(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        month_end_pos = precomputed.index.get_loc(pd.Timestamp("2024-01-31"))
        day_before_exit = precomputed.index[month_end_pos + TOM_EXIT_LAG_TRADING_DAYS - 1]
        row = next(precomputed.loc[[day_before_exit]].itertuples(index=False))
        self.assertIsNone(strategy.exit_signal_at(row, _dummy_position()))

    def test_no_exit_the_day_after_the_exit_day(self):
        strategy = TurnOfMonthStrategy()
        df = _steady_series()
        precomputed = strategy.precompute(df)
        month_end_pos = precomputed.index.get_loc(pd.Timestamp("2024-01-31"))
        day_after_exit = precomputed.index[month_end_pos + TOM_EXIT_LAG_TRADING_DAYS + 1]
        row = next(precomputed.loc[[day_after_exit]].itertuples(index=False))
        self.assertIsNone(strategy.exit_signal_at(row, _dummy_position()))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(TOM_EXIT_LAG_TRADING_DAYS, 3)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(ELIGIBLE_PER_MONTH, 40)
        self.assertEqual(TurnOfMonthStrategy.max_units, 1)
        self.assertAlmostEqual(TurnOfMonthStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(TurnOfMonthStrategy.min_lookback_days, 1)


if __name__ == "__main__":
    unittest.main()
