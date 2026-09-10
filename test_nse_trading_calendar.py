"""
Unit tests for deployment/nse_trading_calendar.py. Run with:

    python test_nse_trading_calendar.py
"""

import unittest
from datetime import date

from deployment.nse_trading_calendar import (
    NSE_TRADING_HOLIDAYS, has_holiday_coverage, is_last_trading_day_of_month, is_trading_day, next_trading_day,
)


class TestTradingDay(unittest.TestCase):
    def test_weekends_and_listed_holidays_are_not_trading_days(self):
        self.assertFalse(is_trading_day(date(2026, 9, 12)))   # Saturday
        self.assertFalse(is_trading_day(date(2026, 9, 13)))   # Sunday
        self.assertFalse(is_trading_day(date(2026, 9, 14)))   # Ganesh Chaturthi
        self.assertTrue(is_trading_day(date(2026, 9, 15)))

    def test_next_trading_day_skips_weekend_and_holiday_runs(self):
        self.assertEqual(next_trading_day(date(2026, 9, 11)), date(2026, 9, 15))   # Fri -> Sat, Sun, holiday Mon -> Tue
        self.assertEqual(next_trading_day(date(2026, 12, 24)), date(2026, 12, 28))  # Christmas Fri + weekend

    def test_2026_list_has_sixteen_weekday_holidays(self):
        self.assertEqual(len(NSE_TRADING_HOLIDAYS[2026]), 16)
        self.assertTrue(all(d.weekday() < 5 for d in NSE_TRADING_HOLIDAYS[2026]))


class TestLastTradingDayOfMonth(unittest.TestCase):
    def test_plain_month_end(self):
        self.assertTrue(is_last_trading_day_of_month(date(2026, 9, 30)))    # Wednesday
        self.assertFalse(is_last_trading_day_of_month(date(2026, 9, 29)))

    def test_month_end_on_a_weekend_moves_to_the_friday(self):
        self.assertTrue(is_last_trading_day_of_month(date(2026, 10, 30)))   # Oct 31 is a Saturday
        self.assertFalse(is_last_trading_day_of_month(date(2026, 10, 29)))

    def test_month_end_holiday_moves_to_the_previous_trading_day(self):
        # March 31, 2026 (Mahavir Jayanti) is a holiday -> March 30 is the month's last trading day
        self.assertTrue(is_last_trading_day_of_month(date(2026, 3, 30)))
        self.assertFalse(is_last_trading_day_of_month(date(2026, 3, 27)))

    def test_uncovered_year_falls_back_to_weekdays_only(self):
        self.assertFalse(has_holiday_coverage(2031))
        self.assertTrue(is_last_trading_day_of_month(date(2031, 1, 31)))    # a Friday
        self.assertFalse(is_last_trading_day_of_month(date(2031, 1, 30)))


if __name__ == "__main__":
    unittest.main()
