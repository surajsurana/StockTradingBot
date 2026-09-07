"""
Unit tests for swing_research/strategies/earnings_announcement_premium.py
and swing_research/announcement_features.py -- hand-constructed
announcement-date and volume fixtures exercising the previous-year-month
forecast, the exactly-4-announcements restriction, the lagged volume
concentration ratio (and its no-lookahead property), month-end entry and
exit, and ranking by the ratio. Run with:

    python test_earnings_announcement_premium.py
"""

import datetime
import unittest

import pandas as pd

from swing_research.announcement_features import (
    VCR_LAG_MONTHS, VCR_MIN_MONTHS, compute_announcement_features, compute_announcement_features_by_symbol,
    dedupe_announcement_dates,
)
from swing_research.base import OpenPosition, PositionUnit
from swing_research.strategies.earnings_announcement_premium import (
    MIN_LOOKBACK_TRADING_DAYS, REQUIRED_PRIOR_YEAR_ANNOUNCEMENTS, STOP_LOSS_PCT,
    EarningsAnnouncementPremiumStrategy,
)


def _bdays(start, end, volume=100):
    idx = pd.bdate_range(start, end)
    closes = [100.0 + i * 0.01 for i in range(len(idx))]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [volume] * len(idx)}, index=idx)


def _row_at(precomputed, day):
    return next(r for r in precomputed.itertuples(index=False) if r.date == day)


class TestDedupe(unittest.TestCase):
    def test_dates_within_seven_days_collapse_to_one(self):
        d = datetime.date
        self.assertEqual(dedupe_announcement_dates([d(2024, 7, 18), d(2024, 7, 15), d(2024, 10, 20)]),
                         [d(2024, 7, 15), d(2024, 10, 20)])


class TestExpectedAnnouncerAndCount(unittest.TestCase):
    def test_expected_true_in_the_month_before_last_years_announcement_month(self):
        df = _bdays("2025-06-02", "2025-07-31")
        features = compute_announcement_features(df, [datetime.date(2024, 7, 15)])
        june_30 = features.loc["2025-06-30"]
        july_15 = features.loc["2025-07-15"]
        self.assertTrue(bool(june_30["expected_announcer_next_month"]))    # next month = July, announced July 2024
        self.assertFalse(bool(july_15["expected_announcer_next_month"]))   # next month = August, no announcement

    def test_prior_12m_count_uses_a_trailing_365_day_window(self):
        d = datetime.date
        dates = [d(2024, 8, 1), d(2024, 11, 1), d(2025, 2, 1), d(2025, 5, 1)]
        df = _bdays("2025-06-02", "2025-09-30")
        features = compute_announcement_features(df, dates)
        self.assertEqual(int(features.loc["2025-06-30"]["announcements_prior_12m"]), 4)
        self.assertEqual(int(features.loc["2025-09-01"]["announcements_prior_12m"]), 3)   # 2024-08-01 has aged out

    def test_symbol_without_history_never_qualifies(self):
        features = compute_announcement_features_by_symbol({"X": _bdays("2025-01-01", "2025-03-31")}, {})
        self.assertFalse(features["X"]["expected_announcer_next_month"].any())
        self.assertEqual(int(features["X"]["announcements_prior_12m"].max()), 0)
        self.assertTrue(features["X"]["volume_concentration_ratio"].isna().all())


class TestVolumeConcentrationRatio(unittest.TestCase):
    """36 months (Jan 2021-Dec 2023): 100/day normally, 300/day in the
    announcement months (Jan/Apr/Jul/Oct, results on the 15th)."""

    def setUp(self):
        idx = pd.bdate_range("2021-01-01", "2023-12-29")
        self.dates = [datetime.date(y, m, 15) for y in (2021, 2022, 2023) for m in (1, 4, 7, 10)]
        ann_months = {(d.year, d.month) for d in self.dates}
        volume = [300 if (ts.year, ts.month) in ann_months else 100 for ts in idx]
        closes = [100.0] * len(idx)
        self.df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                                "Volume": volume}, index=idx)
        self.ann_months = ann_months

    def _expected_vcr(self, df, year, month):
        # independent computation: monthly sums, window of up to 48 months ending 3 months before (year, month)
        monthly = df["Volume"].groupby([df.index.year, df.index.month]).sum()
        keys = list(monthly.index)
        end = keys.index((year, month)) - VCR_LAG_MONTHS
        window = keys[max(0, end + 1 - 48):end + 1]
        total = sum(monthly[k] for k in window)
        ann = sum(monthly[k] for k in window if k in self.ann_months)
        return ann / total

    def test_hand_calculated_ratio_for_the_last_month(self):
        features = compute_announcement_features(self.df, self.dates)
        actual = features.loc["2023-12-15"]["volume_concentration_ratio"]
        self.assertAlmostEqual(actual, self._expected_vcr(self.df, 2023, 12), places=10)
        self.assertGreater(actual, 0.4)   # announcement months carry 3x daily volume, 1/3 of months

    def test_nan_before_minimum_history_plus_lag(self):
        features = compute_announcement_features(self.df, self.dates)
        # month index 26 (0-based) is the first with 24 months of history AND the 3-month lag satisfied
        self.assertTrue(pd.isna(features.loc["2022-12-15"]["volume_concentration_ratio"]))   # month 23 -> too early
        self.assertFalse(pd.isna(features.loc["2023-04-14"]["volume_concentration_ratio"]))  # month 27 -> available

    def test_ratio_ignores_the_most_recent_three_months_no_lookahead(self):
        base = compute_announcement_features(self.df, self.dates).loc["2023-12-15"]["volume_concentration_ratio"]
        tampered = self.df.copy()
        tampered.loc["2023-10-01":, "Volume"] *= 10    # Oct-Dec 2023 are inside the 3-month lag for December
        after = compute_announcement_features(tampered, self.dates).loc["2023-12-15"]["volume_concentration_ratio"]
        self.assertAlmostEqual(base, after, places=12)


class TestPrecompute(unittest.TestCase):
    def test_month_end_flags_come_from_the_calendar_and_never_the_final_bar(self):
        df = _bdays("2024-01-02", "2024-03-28")
        precomputed = EarningsAnnouncementPremiumStrategy().precompute(df)
        self.assertTrue(bool(precomputed.loc["2024-01-31"]["is_month_end"]))
        self.assertTrue(bool(precomputed.loc["2024-02-29"]["is_month_end"]))
        self.assertFalse(bool(precomputed.loc["2024-01-30"]["is_month_end"]))
        self.assertFalse(bool(precomputed.iloc[-1]["is_month_end"]))   # 2024-03-28: tomorrow unknown

    def test_qualifies_only_at_month_end_with_expected_flag_and_exactly_four(self):
        df = _bdays("2024-01-02", "2024-03-28")
        df["expected_announcer_next_month"] = True
        df["announcements_prior_12m"] = 4
        precomputed = EarningsAnnouncementPremiumStrategy().precompute(df)
        self.assertTrue(bool(precomputed.loc["2024-01-31"]["qualifies"]))
        self.assertFalse(bool(precomputed.loc["2024-01-30"]["qualifies"]))

        df["announcements_prior_12m"] = 5
        self.assertFalse(bool(EarningsAnnouncementPremiumStrategy().precompute(df).loc["2024-01-31"]["qualifies"]))
        df["announcements_prior_12m"] = 4
        df["expected_announcer_next_month"] = False
        self.assertFalse(bool(EarningsAnnouncementPremiumStrategy().precompute(df).loc["2024-01-31"]["qualifies"]))

    def test_missing_feature_columns_default_to_never_qualifying(self):
        precomputed = EarningsAnnouncementPremiumStrategy().precompute(_bdays("2024-01-02", "2024-03-28"))
        self.assertFalse(precomputed["qualifies"].any())


class TestEntrySignal(unittest.TestCase):
    def _precomputed(self, vcr):
        df = _bdays("2024-01-02", "2024-03-28")
        df["expected_announcer_next_month"] = True
        df["announcements_prior_12m"] = 4
        df["volume_concentration_ratio"] = vcr
        return EarningsAnnouncementPremiumStrategy().precompute(df)

    def test_fires_at_month_end_with_ratio_as_confidence_and_eight_pct_stop(self):
        strategy = EarningsAnnouncementPremiumStrategy()
        row = _row_at(self._precomputed(0.61), datetime.date(2024, 1, 31))
        signal = strategy.entry_signal_at(row)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.confidence, 0.61)
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_unavailable_ratio_ranks_last_with_zero_confidence(self):
        strategy = EarningsAnnouncementPremiumStrategy()
        row = _row_at(self._precomputed(float("nan")), datetime.date(2024, 1, 31))
        self.assertAlmostEqual(strategy.entry_signal_at(row).confidence, 0.0)

    def test_no_signal_off_month_end(self):
        strategy = EarningsAnnouncementPremiumStrategy()
        self.assertIsNone(strategy.entry_signal_at(_row_at(self._precomputed(0.61), datetime.date(2024, 1, 30))))


class TestExitSignal(unittest.TestCase):
    def _position(self, entry_date):
        return OpenPosition(symbol="TEST", direction="BUY",
                             units=[PositionUnit(entry_price=100.0, entry_date=entry_date, quantity=10)],
                             stop_loss=92.0)

    def test_exits_at_the_next_month_end_only(self):
        strategy = EarningsAnnouncementPremiumStrategy()
        entry = datetime.date(2024, 1, 31)

        class EntryDay:
            date = entry; Close = 100.0; is_month_end = True
        self.assertIsNone(strategy.exit_signal_at(EntryDay(), self._position(entry)))   # same day: no exit

        class MidMonth:
            date = datetime.date(2024, 2, 15); Close = 120.0; is_month_end = False
        self.assertIsNone(strategy.exit_signal_at(MidMonth(), self._position(entry)))   # no early exit, even up 20%

        class MonthEnd:
            date = datetime.date(2024, 2, 29); Close = 105.0; is_month_end = True
        self.assertAlmostEqual(strategy.exit_signal_at(MonthEnd(), self._position(entry)), 105.0)


class TestConstants(unittest.TestCase):
    def test_documented_parameters(self):
        self.assertEqual(REQUIRED_PRIOR_YEAR_ANNOUNCEMENTS, 4)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.08)
        self.assertEqual(EarningsAnnouncementPremiumStrategy.max_units, 1)
        self.assertAlmostEqual(EarningsAnnouncementPremiumStrategy.risk_pct_per_unit, 0.01)
        self.assertEqual(EarningsAnnouncementPremiumStrategy.min_lookback_days, MIN_LOOKBACK_TRADING_DAYS)
        self.assertEqual(MIN_LOOKBACK_TRADING_DAYS, round((VCR_MIN_MONTHS + VCR_LAG_MONTHS) / 12 * 252))


if __name__ == "__main__":
    unittest.main()
