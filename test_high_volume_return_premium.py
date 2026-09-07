"""
Unit tests for swing_research/strategies/high_volume_return_premium.py --
hand-constructed synthetic series exercising the volume-shock percentile
qualification (top decile), the state-transition entry trigger, the
21-trading-day (calendar-day-equivalent) time-stop exit, and no
percentile-based early exit. Run with:

    python test_high_volume_return_premium.py
"""

import datetime
import unittest

import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.high_volume_return_premium import (
    HIGH_VOLUME_PERCENTILE_THRESHOLD, HOLDING_PERIOD_CALENDAR_DAYS, STOP_LOSS_PCT,
    HighVolumeReturnPremiumStrategy,
)


def _steady_series(n=270, start_price=100.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start_price * (1 + 0.001) ** i for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * n}, index=idx)


class TestPrecompute(unittest.TestCase):
    def test_qualifies_when_percentile_at_or_above_threshold(self):
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series()
        df["volume_shock_percentile"] = 95.0   # top decile
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))

    def test_does_not_qualify_below_threshold(self):
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series()
        df["volume_shock_percentile"] = 89.9   # just below the top-decile cutoff
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_missing_percentile_column_defaults_to_disqualified(self):
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series()  # no volume_shock_percentile column at all
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_exactly_at_threshold_qualifies(self):
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series()
        df["volume_shock_percentile"] = HIGH_VOLUME_PERCENTILE_THRESHOLD
        precomputed = strategy.precompute(df)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))


class TestEntrySignal(unittest.TestCase):
    def test_fires_only_on_the_qualification_transition_day(self):
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series()
        df["volume_shock_percentile"] = 95.0
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
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series()
        df["volume_shock_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        row = list(precomputed.itertuples(index=False))[-1]._replace(qualifies=True, qualifies_prev=True)
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = HighVolumeReturnPremiumStrategy()
        df = _steady_series(n=30)  # far short of the 257-day min_lookback_days
        df["volume_shock_percentile"] = 95.0
        precomputed = strategy.precompute(df)
        last_row = list(precomputed.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))


class TestExitSignal(unittest.TestCase):
    def _position(self, entry_date):
        return OpenPosition(symbol="TEST", direction="BUY",
                             units=[PositionUnit(entry_price=100.0, entry_date=entry_date, quantity=10)],
                             stop_loss=92.0)

    def test_fires_at_or_past_the_holding_period_calendar_days(self):
        strategy = HighVolumeReturnPremiumStrategy()
        entry_date = datetime.date(2024, 1, 1)
        exit_date = entry_date + datetime.timedelta(days=HOLDING_PERIOD_CALENDAR_DAYS)

        class Row:
            date = exit_date
            Close = 105.0
        exit_price = strategy.exit_signal_at(Row(), self._position(entry_date))
        self.assertAlmostEqual(exit_price, 105.0)

    def test_no_exit_before_the_holding_period_elapses(self):
        strategy = HighVolumeReturnPremiumStrategy()
        entry_date = datetime.date(2024, 1, 1)
        near_date = entry_date + datetime.timedelta(days=HOLDING_PERIOD_CALENDAR_DAYS - 1)

        class Row:
            date = near_date
            Close = 105.0
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))

    def test_no_percentile_based_early_exit_exists(self):
        strategy = HighVolumeReturnPremiumStrategy()
        entry_date = datetime.date(2024, 1, 1)
        near_date = entry_date + datetime.timedelta(days=5)

        class Row:
            date = near_date
            Close = 150.0
            volume_shock_percentile = 1.0  # now firmly in the bottom decile -- should NOT trigger an exit
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertAlmostEqual(HIGH_VOLUME_PERCENTILE_THRESHOLD, 90.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(HighVolumeReturnPremiumStrategy.max_units, 1)
        self.assertAlmostEqual(HighVolumeReturnPremiumStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(HighVolumeReturnPremiumStrategy.min_lookback_days, 257)


class TestVolumeShockScoreComputation(unittest.TestCase):
    """Covers swing_research/cross_sectional.py's new
    compute_volume_shock_score()/compute_volume_shock_percentile_ranks()."""

    def test_score_is_one_for_flat_constant_volume(self):
        from swing_research.cross_sectional import compute_volume_shock_score
        n = 270
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        df = pd.DataFrame({"Open": [100.0] * n, "High": [100.0] * n, "Low": [100.0] * n,
                            "Close": [100.0] * n, "Volume": [1000] * n}, index=idx)
        score = compute_volume_shock_score(df)
        self.assertAlmostEqual(score.iloc[-1], 1.0, places=8)

    def test_score_is_greater_than_one_on_a_recent_volume_spike(self):
        from swing_research.cross_sectional import compute_volume_shock_score
        n = 270
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        volumes = [1000] * n
        for i in range(n - 5, n):   # last 5 days spike hard
            volumes[i] = 5000
        df = pd.DataFrame({"Open": [100.0] * n, "High": [100.0] * n, "Low": [100.0] * n,
                            "Close": [100.0] * n, "Volume": volumes}, index=idx)
        score = compute_volume_shock_score(df)
        self.assertGreater(score.iloc[-1], 1.0)

    def test_percentile_ranks_rank_across_symbols(self):
        from swing_research.cross_sectional import compute_volume_shock_percentile_ranks
        n = 270
        idx = pd.date_range("2020-01-01", periods=n, freq="D")

        def make_df(recent_volume):
            volumes = [1000] * (n - 5) + [recent_volume] * 5
            return pd.DataFrame({"Open": [100.0] * n, "High": [100.0] * n, "Low": [100.0] * n,
                                  "Close": [100.0] * n, "Volume": volumes}, index=idx)

        data = {"SPIKE": make_df(5000), "FLAT": make_df(1000)}
        ranks = compute_volume_shock_percentile_ranks(data)
        self.assertGreater(ranks["SPIKE"].iloc[-1], ranks["FLAT"].iloc[-1])


if __name__ == "__main__":
    unittest.main()
