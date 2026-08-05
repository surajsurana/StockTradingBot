"""
Unit tests for swing_research/strategies/turtle_system2.py -- hand-
calculated synthetic series with a known answer for N (Wilder-smoothed
True Range), the 55-day entry breakout, the 20-day exit breakdown, and
position sizing. Run with:

    python test_turtle_system2.py
"""

import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.turtle_system2 import (
    BREAKOUT_ENTRY_DAYS, BREAKOUT_EXIT_DAYS, STOP_N_MULTIPLE, TurtleSystem2Strategy,
    _true_range, _wilder_n,
)


def _flat_bars(n, price=100.0):
    """n days of a perfectly flat OHLC series -- True Range is 0 every day
    except possibly rounding, giving a clean, hand-verifiable N."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "Open": [price] * n, "High": [price] * n, "Low": [price] * n,
        "Close": [price] * n, "Volume": [1000] * n,
    }, index=idx)


class TestTrueRangeAndN(unittest.TestCase):
    def test_true_range_hand_calculated(self):
        df = pd.DataFrame({
            "Open": [100, 105], "High": [102, 108], "Low": [99, 104],
            "Close": [101, 106], "Volume": [1000, 1000],
        }, index=pd.date_range("2020-01-01", periods=2))
        tr = _true_range(df)
        # day0: no prev close -> High-Low = 102-99 = 3 (NaN prev_close makes the other two terms NaN,
        # .max(axis=1) skips NaN by default in pandas, so High-Low wins)
        self.assertAlmostEqual(tr.iloc[0], 3.0)
        # day1: max(108-104, |108-101|, |104-101|) = max(4, 7, 3) = 7
        self.assertAlmostEqual(tr.iloc[1], 7.0)

    def test_wilder_n_seeded_by_simple_average_then_smoothed(self):
        # 25 days of constant TR=2.0 (e.g. High-Low=2 every day, flat closes
        # so the other two TR terms don't dominate) -> seed N = simple
        # 20-day average = 2.0, and it should stay exactly 2.0 forever after
        # (smoothing a constant series converges immediately).
        idx = pd.date_range("2020-01-01", periods=25, freq="D")
        tr = pd.Series([2.0] * 25, index=idx)
        n = _wilder_n(tr, period=20)
        self.assertTrue(pd.isna(n.iloc[0]))  # not enough history for the first 19 rows
        self.assertAlmostEqual(n.iloc[19], 2.0)  # seed value (0-indexed day 19 = the 20th day)
        self.assertAlmostEqual(n.iloc[24], 2.0)  # smoothing a constant stays constant

    def test_wilder_n_responds_to_a_tr_spike(self):
        idx = pd.date_range("2020-01-01", periods=21, freq="D")
        tr = pd.Series([1.0] * 20 + [21.0], index=idx)  # spike on the 21st day
        n = _wilder_n(tr, period=20)
        seed = 1.0  # simple average of the first 20 (all 1.0)
        expected_day20 = (19 * seed + 21.0) / 20  # Wilder's formula
        self.assertAlmostEqual(n.iloc[20], expected_day20)


class TestEntrySignal(unittest.TestCase):
    def _breakout_df(self):
        # 60 flat days at 100 (so entry_level = 100, N stabilizes), then a
        # clean breakout to 110 on day 60.
        base = _flat_bars(60, price=100.0)
        breakout_row = pd.DataFrame({
            "Open": [105], "High": [111], "Low": [104], "Close": [110], "Volume": [1000],
        }, index=[base.index[-1] + pd.Timedelta(days=1)])
        return pd.concat([base, breakout_row])

    def test_fires_on_close_above_prior_55_day_high(self):
        strategy = TurtleSystem2Strategy()
        df = self._breakout_df()
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        signal = strategy.entry_signal_at(last_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.entry_price, 110.0)
        # stop = entry - 2N; N should be small (flat history) but nonzero after the breakout bar itself
        self.assertLess(signal.stop_loss, signal.entry_price)

    def test_no_signal_without_a_close_above_the_entry_level(self):
        strategy = TurtleSystem2Strategy()
        df = _flat_bars(60, price=100.0)  # never breaks out
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        signal = strategy.entry_signal_at(last_row)
        self.assertIsNone(signal)

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = TurtleSystem2Strategy()
        df = _flat_bars(10, price=100.0)  # far short of the 55-day window
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        signal = strategy.entry_signal_at(last_row)
        self.assertIsNone(signal)


class TestExitSignal(unittest.TestCase):
    def test_fires_on_close_below_prior_20_day_low(self):
        strategy = TurtleSystem2Strategy()
        base = _flat_bars(25, price=100.0)
        breakdown_row = pd.DataFrame({
            "Open": [95], "High": [96], "Low": [89], "Close": [90], "Volume": [1000],
        }, index=[base.index[-1] + pd.Timedelta(days=1)])
        df = pd.concat([base, breakdown_row])
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=50.0)
        exit_price = strategy.exit_signal_at(last_row, position)
        self.assertAlmostEqual(exit_price, 90.0)

    def test_no_exit_signal_while_above_the_20_day_low(self):
        strategy = TurtleSystem2Strategy()
        df = _flat_bars(25, price=100.0)
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=50.0)
        exit_price = strategy.exit_signal_at(last_row, position)
        self.assertIsNone(exit_price)


class TestPyramidSignal(unittest.TestCase):
    def test_fires_after_half_n_favorable_move_from_last_unit(self):
        strategy = TurtleSystem2Strategy()
        df = _flat_bars(60, price=100.0)
        precomputed = strategy.precompute(df)
        # Manually set N for a deterministic test (flat series gives N~0,
        # not useful to exercise the pyramid math) -- override the last row's
        # N via a fresh namedtuple substitute is awkward, so build a row-like
        # object with the fields the strategy actually reads.
        class Row:
            N = 4.0
            Close = 102.0  # last unit entered at 100; +2 = 0.5*N -> should fire
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=92.0)
        signal = strategy.pyramid_signal_at(Row(), position)
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.entry_price, 102.0)
        self.assertAlmostEqual(signal.stop_loss, 102.0 - STOP_N_MULTIPLE * 4.0)

    def test_no_pyramid_signal_before_half_n_move(self):
        strategy = TurtleSystem2Strategy()

        class Row:
            N = 4.0
            Close = 101.0  # only +1, needs +2 (0.5N)
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=92.0)
        signal = strategy.pyramid_signal_at(Row(), position)
        self.assertIsNone(signal)

    def test_never_lowers_the_whole_position_stop(self):
        strategy = TurtleSystem2Strategy()

        class Row:
            N = 4.0
            Close = 102.0
        # existing stop is already ABOVE what the new unit's 2N-below-entry would compute
        position = OpenPosition(symbol="TEST", direction="BUY",
                                 units=[PositionUnit(entry_price=100.0, entry_date=None, quantity=10)],
                                 stop_loss=99.0)  # already higher than 102-8=94
        signal = strategy.pyramid_signal_at(Row(), position)
        self.assertIsNone(signal)


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_published_rules(self):
        self.assertEqual(BREAKOUT_ENTRY_DAYS, 55)
        self.assertEqual(BREAKOUT_EXIT_DAYS, 20)
        self.assertEqual(STOP_N_MULTIPLE, 2.0)
        self.assertEqual(TurtleSystem2Strategy.max_units, 4)
        self.assertAlmostEqual(TurtleSystem2Strategy.risk_pct_per_unit, 0.02)


if __name__ == "__main__":
    unittest.main()
