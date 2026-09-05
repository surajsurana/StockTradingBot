"""
Unit tests for swing_research/strategies/overnight_return_anomaly.py --
hand-constructed synthetic series exercising the overnight-percentile
qualification (top decile), the state-transition entry trigger, the
1-trading-day (calendar-day-equivalent) time-stop exit, and no
percentile-based early exit. Run with:

    python test_overnight_return_anomaly.py
"""

import datetime
import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.overnight_return_anomaly import (
    HOLDING_PERIOD_CALENDAR_DAYS, OVERNIGHT_RETURN_FORMATION_DAYS,
    OVERNIGHT_RETURN_PERCENTILE_THRESHOLD, STOP_LOSS_PCT, OvernightReturnAnomalyStrategy,
)


def _steady_series(n=60, start_price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price * (1 + 0.002) ** i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestPrecompute(unittest.TestCase):
    def test_qualifies_when_percentile_at_or_above_threshold(self):
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series()
        df["overnight_percentile"] = 95.0   # top decile
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))

    def test_does_not_qualify_below_threshold(self):
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series()
        df["overnight_percentile"] = 89.9   # just below the top-decile cutoff
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_missing_overnight_percentile_column_defaults_to_disqualified(self):
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series()  # no overnight_percentile column at all
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_exactly_at_threshold_qualifies(self):
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series()
        df["overnight_percentile"] = OVERNIGHT_RETURN_PERCENTILE_THRESHOLD
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))


class TestEntrySignal(unittest.TestCase):
    def test_fires_only_on_the_qualification_transition_day(self):
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series()
        df["overnight_percentile"] = 95.0
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
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series()
        df["overnight_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        row = list(precomputed.itertuples(index=False))[-1]._replace(qualifies=True, qualifies_prev=True)
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = OvernightReturnAnomalyStrategy()
        df = _steady_series(n=10)  # far short of the 21-day formation window
        df["overnight_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))


class TestExitSignal(unittest.TestCase):
    def _position(self, entry_date):
        return OpenPosition(symbol="TEST", direction="BUY",
                             units=[PositionUnit(entry_price=100.0, entry_date=entry_date, quantity=10)],
                             stop_loss=92.0)

    def test_fires_at_or_past_the_holding_period_calendar_days(self):
        strategy = OvernightReturnAnomalyStrategy()
        entry_date = datetime.date(2024, 1, 1)
        exit_date = entry_date + datetime.timedelta(days=HOLDING_PERIOD_CALENDAR_DAYS)

        class Row:
            date = exit_date
            Close = 105.0
        exit_price = strategy.exit_signal_at(Row(), self._position(entry_date))
        self.assertAlmostEqual(exit_price, 105.0)

    def test_no_exit_before_the_holding_period_elapses(self):
        strategy = OvernightReturnAnomalyStrategy()
        entry_date = datetime.date(2024, 1, 1)

        class Row:
            date = entry_date   # zero elapsed days -- same-day, before any hold requirement
            Close = 105.0
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))

    def test_no_percentile_based_early_exit_exists(self):
        strategy = OvernightReturnAnomalyStrategy()
        entry_date = datetime.date(2024, 1, 1)
        near_date = entry_date  # before the 1-day hold has elapsed

        class Row:
            date = near_date
            Close = 150.0
            overnight_percentile = 1.0  # now firmly in the bottom decile -- should NOT trigger an exit here
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(OVERNIGHT_RETURN_FORMATION_DAYS, 21)
        self.assertAlmostEqual(OVERNIGHT_RETURN_PERCENTILE_THRESHOLD, 90.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(OvernightReturnAnomalyStrategy.max_units, 1)
        self.assertAlmostEqual(OvernightReturnAnomalyStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(OvernightReturnAnomalyStrategy.min_lookback_days, 22)
        self.assertEqual(HOLDING_PERIOD_CALENDAR_DAYS, 1)

    def test_threshold_is_top_decile_like_momentum_strategies_not_reversal(self):
        self.assertGreater(OVERNIGHT_RETURN_PERCENTILE_THRESHOLD, 50.0)


class TestOvernightReturnScoreComputation(unittest.TestCase):
    """Covers swing_research/cross_sectional.py's new
    compute_overnight_return_score()/compute_overnight_return_percentile_ranks()
    -- specifically that it measures ONLY the Close-to-Open move, not
    total return (the whole point of this strategy being distinct from
    every existing Close-to-Close-based strategy)."""

    def test_score_is_zero_for_a_flat_open_no_gap_series(self):
        from swing_research.cross_sectional import compute_overnight_return_score
        # Open always equals the prior day's Close -- zero overnight return
        # every day, regardless of how much the stock moves intraday.
        n = 30
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        closes = [100.0 + i for i in range(n)]
        opens = [100.0] + closes[:-1]   # today's Open = yesterday's Close
        df = pd.DataFrame({"Open": opens, "High": closes, "Low": closes, "Close": closes,
                            "Volume": [1000] * n}, index=idx)
        score = compute_overnight_return_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.0, places=8)

    def test_score_is_positive_when_every_open_gaps_up_from_prior_close(self):
        from swing_research.cross_sectional import compute_overnight_return_score
        n = 30
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        closes = [100.0] * n   # flat Close-to-Close -- a naive total-return score would see nothing
        opens = [100.0] + [101.0] * (n - 1)   # every day gaps up 1% at the open, then drifts back flat
        df = pd.DataFrame({"Open": opens, "High": opens, "Low": closes, "Close": closes,
                            "Volume": [1000] * n}, index=idx)
        score = compute_overnight_return_score(df)
        self.assertGreater(score.iloc[-1], 0.0)

    def test_percentile_ranks_rank_across_symbols(self):
        from swing_research.cross_sectional import compute_overnight_return_percentile_ranks
        n = 30
        idx = pd.date_range("2020-01-01", periods=n, freq="D")

        def make_df(gap_pct):
            closes = [100.0] * n
            opens = [100.0] + [100.0 * (1 + gap_pct)] * (n - 1)
            return pd.DataFrame({"Open": opens, "High": opens, "Low": closes, "Close": closes,
                                  "Volume": [1000] * n}, index=idx)

        data = {"STRONG": make_df(0.02), "WEAK": make_df(-0.02)}
        ranks = compute_overnight_return_percentile_ranks(data)
        self.assertGreater(ranks["STRONG"].iloc[-1], ranks["WEAK"].iloc[-1])


if __name__ == "__main__":
    unittest.main()
