"""
Unit tests for swing_research/strategies/turn_of_year.py -- year-end
entry detection, the row-position-based 5-trading-day exit, the bottom-
decile small-cap qualification, and the absence of any diversification-
hash machinery (this strategy relies on the market-cap filter itself to
narrow the universe, unlike Turn-of-the-Month). Run with:

    python test_turn_of_year.py
"""

import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.turn_of_year import (
    JANUARY_EFFECT_HOLD_TRADING_DAYS, SMALL_CAP_PERCENTILE_THRESHOLD, STOP_LOSS_PCT,
    TurnOfYearStrategy,
)


def _steady_series(n=400, start_price=100.0, market_cap_percentile=None):
    idx = pd.bdate_range("2023-06-01", periods=n)
    closes = [start_price] * n
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                        "Volume": [1000] * n}, index=idx)
    if market_cap_percentile is not None:
        df["market_cap_percentile"] = market_cap_percentile
    return df


def _dummy_position():
    return OpenPosition(symbol="TEST", direction="BUY",
                         units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                         stop_loss=92.0)


class TestPrecompute(unittest.TestCase):
    def test_last_trading_day_of_each_full_year_is_flagged(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        flagged_dates = precomputed.index[precomputed["is_year_end_entry_day"]].tolist()
        self.assertIn(pd.Timestamp("2023-12-29"), flagged_dates)   # last NYSE/NSE business day of 2023

    def test_ordinary_mid_year_day_is_not_flagged(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed.loc[pd.Timestamp("2023-07-17"), "is_year_end_entry_day"]))

    def test_exit_day_is_exactly_n_trading_days_after_year_end(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        year_end_pos = precomputed.index.get_loc(pd.Timestamp("2023-12-29"))
        expected_exit_date = precomputed.index[year_end_pos + JANUARY_EFFECT_HOLD_TRADING_DAYS]
        self.assertTrue(bool(precomputed.loc[expected_exit_date, "is_january_exit_day"]))

    def test_day_immediately_after_year_end_is_not_yet_the_exit_day(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        year_end_pos = precomputed.index.get_loc(pd.Timestamp("2023-12-29"))
        next_day = precomputed.index[year_end_pos + 1]
        self.assertFalse(bool(precomputed.loc[next_day, "is_january_exit_day"]))

    def test_year_end_day_when_small_cap_qualifies(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed.loc[pd.Timestamp("2023-12-29"), "qualifies_for_entry"]))

    def test_year_end_day_when_not_small_cap_does_not_qualify(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=50.0)
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed.loc[pd.Timestamp("2023-12-29"), "qualifies_for_entry"]))

    def test_exactly_at_threshold_qualifies(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=SMALL_CAP_PERCENTILE_THRESHOLD)
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed.loc[pd.Timestamp("2023-12-29"), "qualifies_for_entry"]))

    def test_missing_market_cap_percentile_column_defaults_to_disqualified(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series()  # no market_cap_percentile column at all
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed.loc[pd.Timestamp("2023-12-29"), "qualifies_for_entry"]))


class TestEntrySignal(unittest.TestCase):
    def test_fires_on_the_year_end_row_when_small_cap(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        row = next(precomputed.loc[[pd.Timestamp("2023-12-29")]].itertuples(index=False))
        signal = strategy.entry_signal_at(row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_no_signal_on_the_year_end_row_when_not_small_cap(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=50.0)
        precomputed = strategy.precompute(df)
        row = next(precomputed.loc[[pd.Timestamp("2023-12-29")]].itertuples(index=False))
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_no_signal_on_an_ordinary_day(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        row = next(precomputed.loc[[pd.Timestamp("2023-07-17")]].itertuples(index=False))
        self.assertIsNone(strategy.entry_signal_at(row))


class TestExitSignal(unittest.TestCase):
    def test_fires_on_the_january_exit_day(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        year_end_pos = precomputed.index.get_loc(pd.Timestamp("2023-12-29"))
        exit_date = precomputed.index[year_end_pos + JANUARY_EFFECT_HOLD_TRADING_DAYS]
        row = next(precomputed.loc[[exit_date]].itertuples(index=False))
        exit_price = strategy.exit_signal_at(row, _dummy_position())
        self.assertAlmostEqual(exit_price, float(row.Close))

    def test_no_exit_the_day_before_the_exit_day(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        year_end_pos = precomputed.index.get_loc(pd.Timestamp("2023-12-29"))
        day_before_exit = precomputed.index[year_end_pos + JANUARY_EFFECT_HOLD_TRADING_DAYS - 1]
        row = next(precomputed.loc[[day_before_exit]].itertuples(index=False))
        self.assertIsNone(strategy.exit_signal_at(row, _dummy_position()))

    def test_no_exit_the_day_after_the_exit_day(self):
        strategy = TurnOfYearStrategy()
        df = _steady_series(market_cap_percentile=5.0)
        precomputed = strategy.precompute(df)
        year_end_pos = precomputed.index.get_loc(pd.Timestamp("2023-12-29"))
        day_after_exit = precomputed.index[year_end_pos + JANUARY_EFFECT_HOLD_TRADING_DAYS + 1]
        row = next(precomputed.loc[[day_after_exit]].itertuples(index=False))
        self.assertIsNone(strategy.exit_signal_at(row, _dummy_position()))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(JANUARY_EFFECT_HOLD_TRADING_DAYS, 5)
        self.assertAlmostEqual(SMALL_CAP_PERCENTILE_THRESHOLD, 10.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(TurnOfYearStrategy.max_units, 1)
        self.assertAlmostEqual(TurnOfYearStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(TurnOfYearStrategy.min_lookback_days, 1)

    def test_threshold_is_a_bottom_decile(self):
        self.assertLess(SMALL_CAP_PERCENTILE_THRESHOLD, 50.0)

    def test_hold_is_within_the_roadmap_candidates_disclosed_range(self):
        # research_roadmap.py's turn_of_year CandidateProfile declares
        # holding_days_min=1, holding_days_max=10.
        self.assertGreaterEqual(JANUARY_EFFECT_HOLD_TRADING_DAYS, 1)
        self.assertLessEqual(JANUARY_EFFECT_HOLD_TRADING_DAYS, 10)


if __name__ == "__main__":
    unittest.main()
