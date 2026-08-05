"""
Unit tests for swing_research/strategies/cross_sectional_momentum.py --
hand-constructed synthetic series exercising the formation-return
qualification, the state-transition entry trigger, the 126-trading-day
(calendar-day-equivalent) time-stop exit, and no percentile-based early
exit. Run with:

    python test_cross_sectional_momentum.py
"""

import datetime
import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.cross_sectional_momentum import (
    CrossSectionalMomentumStrategy, HOLDING_PERIOD_CALENDAR_DAYS, MOMENTUM_FORMATION_DAYS,
    MOMENTUM_PERCENTILE_THRESHOLD, STOP_LOSS_PCT,
)


def _steady_series(n=200, start_price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price * (1 + 0.002) ** i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestPrecompute(unittest.TestCase):
    def test_qualifies_when_percentile_at_or_above_threshold(self):
        strategy = CrossSectionalMomentumStrategy()
        df = _steady_series()
        df["momentum_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))

    def test_does_not_qualify_below_threshold(self):
        strategy = CrossSectionalMomentumStrategy()
        df = _steady_series()
        df["momentum_percentile"] = 89.9
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_missing_momentum_percentile_column_defaults_to_disqualified(self):
        strategy = CrossSectionalMomentumStrategy()
        df = _steady_series()  # no momentum_percentile column at all
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))


class TestEntrySignal(unittest.TestCase):
    def test_fires_only_on_the_qualification_transition_day(self):
        strategy = CrossSectionalMomentumStrategy()
        df = _steady_series()
        df["momentum_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        rows = list(precomputed.itertuples(index=False))

        pre_row = rows[-2]._replace(qualifies=False, qualifies_prev=False)
        self.assertIsNone(strategy.entry_signal_at(pre_row))

        transition_row = rows[-1]._replace(qualifies=True, qualifies_prev=False)
        signal = strategy.entry_signal_at(transition_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_no_signal_if_already_qualifying_yesterday(self):
        strategy = CrossSectionalMomentumStrategy()
        df = _steady_series()
        df["momentum_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        row = list(precomputed.itertuples(index=False))[-1]._replace(qualifies=True, qualifies_prev=True)
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = CrossSectionalMomentumStrategy()
        df = _steady_series(n=50)  # far short of the 126-day formation window
        df["momentum_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))


class TestExitSignal(unittest.TestCase):
    def _position(self, entry_date):
        return OpenPosition(symbol="TEST", direction="BUY",
                             units=[PositionUnit(entry_price=100.0, entry_date=entry_date, quantity=10)],
                             stop_loss=92.0)

    def test_fires_at_or_past_the_holding_period_calendar_days(self):
        strategy = CrossSectionalMomentumStrategy()
        entry_date = datetime.date(2024, 1, 1)
        exit_date = entry_date + datetime.timedelta(days=HOLDING_PERIOD_CALENDAR_DAYS)

        class Row:
            date = exit_date
            Close = 123.45
        exit_price = strategy.exit_signal_at(Row(), self._position(entry_date))
        self.assertAlmostEqual(exit_price, 123.45)

    def test_no_exit_before_the_holding_period_elapses(self):
        strategy = CrossSectionalMomentumStrategy()
        entry_date = datetime.date(2024, 1, 1)
        near_date = entry_date + datetime.timedelta(days=HOLDING_PERIOD_CALENDAR_DAYS - 1)

        class Row:
            date = near_date
            Close = 123.45
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))

    def test_no_percentile_based_early_exit_exists(self):
        strategy = CrossSectionalMomentumStrategy()
        entry_date = datetime.date(2024, 1, 1)
        near_date = entry_date + datetime.timedelta(days=10)

        class Row:
            date = near_date
            Close = 50.0
            momentum_percentile = 1.0  # deep in the bottom decile
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(MOMENTUM_FORMATION_DAYS, 126)
        self.assertAlmostEqual(MOMENTUM_PERCENTILE_THRESHOLD, 90.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(CrossSectionalMomentumStrategy.max_units, 1)
        self.assertAlmostEqual(CrossSectionalMomentumStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(CrossSectionalMomentumStrategy.min_lookback_days, 126)


if __name__ == "__main__":
    unittest.main()
