"""
Unit tests for swing_research/strategies/minervini_trend_template_filter.py
-- hand-constructed synthetic series exercising each of the 8 Trend
Template criteria individually, the state-transition entry trigger, and
the 50-day-MA exit rule. Run with:

    python test_minervini_trend_template_filter.py
"""

import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.minervini_trend_template_filter import (
    MinerviniTrendTemplateFilterStrategy, RS_PERCENTILE_THRESHOLD, STOP_LOSS_PCT,
)


def _steady_uptrend(n=300, start_price=100.0, daily_pct=0.003):
    """A smooth, monotonic uptrend -- satisfies MA alignment, 200-day MA
    trending up, and proximity to the 52-week high by construction (the
    most recent close is always the series' own running high)."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price * (1 + daily_pct) ** i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


def _flat_series(n=300, price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": [price] * n, "High": [price] * n, "Low": [price] * n,
                          "Close": [price] * n, "Volume": [1000] * n}, index=idx)


class TestPrecomputeQualification(unittest.TestCase):
    def test_qualifies_when_all_8_criteria_pass(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend()
        df["rs_percentile"] = 90.0  # above the 70th-percentile threshold every day
        precomputed = strategy.precompute(df)
        last_row = precomputed.iloc[-1]
        self.assertTrue(bool(last_row["qualifies"]))

    def test_fails_when_rs_percentile_below_threshold(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend()
        df["rs_percentile"] = 50.0  # below the 70th-percentile gate
        precomputed = strategy.precompute(df)
        last_row = precomputed.iloc[-1]
        self.assertFalse(bool(last_row["qualifies"]))

    def test_missing_rs_percentile_column_defaults_to_disqualified(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend()  # no rs_percentile column at all
        precomputed = strategy.precompute(df)
        last_row = precomputed.iloc[-1]
        self.assertFalse(bool(last_row["qualifies"]))

    def test_flat_series_never_qualifies(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _flat_series()
        df["rs_percentile"] = 90.0
        precomputed = strategy.precompute(df)
        last_row = precomputed.iloc[-1]
        # flat series: not >=30% above 52w low, not trending up -- must fail
        self.assertFalse(bool(last_row["qualifies"]))

    def test_fails_when_too_far_below_52_week_high(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend(n=300)
        # Force a sharp pullback on the final day only, keeping the MAs
        # (which lag) still roughly aligned but violating criterion 7.
        df.loc[df.index[-1], ["Open", "High", "Low", "Close"]] = df["Close"].iloc[-2] * 0.60
        df["rs_percentile"] = 90.0
        precomputed = strategy.precompute(df)
        last_row = precomputed.iloc[-1]
        self.assertFalse(bool(last_row["qualifies"]))


class TestEntrySignal(unittest.TestCase):
    def test_fires_only_on_the_qualification_transition_day(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend()
        df["rs_percentile"] = 90.0
        precomputed = strategy.precompute(df)
        rows = list(precomputed.itertuples(index=False))

        # Simulate the day BEFORE qualifying (force qualifies=False,
        # qualifies_prev=False) -> no signal.
        pre_row = rows[-2]._replace(qualifies=False, qualifies_prev=False)
        self.assertIsNone(strategy.entry_signal_at(pre_row))

        # The actual transition day (qualifies=True, qualifies_prev=False) -> signal fires.
        transition_row = rows[-1]._replace(qualifies=True, qualifies_prev=False)
        signal = strategy.entry_signal_at(transition_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_no_signal_if_already_qualifying_yesterday(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend()
        df["rs_percentile"] = 90.0
        precomputed = strategy.precompute(df)
        last_row = precomputed.iloc[-1]
        row = list(precomputed.itertuples(index=False))[-1]._replace(qualifies=True, qualifies_prev=True)
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = MinerviniTrendTemplateFilterStrategy()
        df = _steady_uptrend(n=50)  # far short of the 200-day MA / 252-day 52w window
        df["rs_percentile"] = 90.0
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))


class TestExitSignal(unittest.TestCase):
    def test_fires_on_close_below_50_day_ma(self):
        strategy = MinerviniTrendTemplateFilterStrategy()

        class Row:
            ma50 = 100.0
            Close = 95.0
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=92.0)
        exit_price = strategy.exit_signal_at(Row(), position)
        self.assertAlmostEqual(exit_price, 95.0)

    def test_no_exit_while_above_50_day_ma(self):
        strategy = MinerviniTrendTemplateFilterStrategy()

        class Row:
            ma50 = 100.0
            Close = 105.0
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=92.0)
        self.assertIsNone(strategy.exit_signal_at(Row(), position))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_published_rules(self):
        self.assertEqual(RS_PERCENTILE_THRESHOLD, 70.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(MinerviniTrendTemplateFilterStrategy.max_units, 1)
        self.assertAlmostEqual(MinerviniTrendTemplateFilterStrategy.risk_pct_per_unit, 0.0125)


if __name__ == "__main__":
    unittest.main()
