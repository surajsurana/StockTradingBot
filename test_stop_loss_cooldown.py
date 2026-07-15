"""
Mock-based unit tests for execution/position_state.py's stop-loss cooldown
-- the rule that stops the bot re-entering a symbol it was just stopped out
of. Motivated by a real live incident: PATANJALI.NS was bought in the
morning run, hit its stop-loss (-Rs.94.50), then the afternoon run bought
it again at a HIGHER price than the morning entry with a wider stop and a
bigger position. Run with:

    python test_stop_loss_cooldown.py
"""

import csv
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

from execution.position_state import _trading_days_between, symbols_in_cooldown

FIELDS = ["timestamp", "symbol", "quantity", "entry_price", "exit_price", "realized_pnl", "reason"]


def _write_log(rows):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    f.close()
    return f.name


def _row(symbol, days_ago, pnl, today):
    ts = datetime.combine(today - timedelta(days=days_ago), datetime.min.time())
    return {"timestamp": ts.isoformat(), "symbol": symbol, "quantity": 9,
            "entry_price": 349.85, "exit_price": 339.35,
            "realized_pnl": pnl, "reason": "GTT stop-loss/target triggered"}


class TestTradingDaysBetween(unittest.TestCase):
    def test_same_day_is_zero(self):
        d = date(2026, 7, 15)  # a Wednesday
        self.assertEqual(_trading_days_between(d, d), 0)

    def test_weekdays_count(self):
        wed = date(2026, 7, 15)
        fri = date(2026, 7, 17)
        self.assertEqual(_trading_days_between(wed, fri), 2)

    def test_weekend_skipped(self):
        fri = date(2026, 7, 17)
        mon = date(2026, 7, 20)
        self.assertEqual(_trading_days_between(fri, mon), 1)


class TestSymbolsInCooldown(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 7, 15)  # a Wednesday
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def _log(self, rows):
        path = _write_log(rows)
        self._paths.append(path)
        return path

    def test_same_day_stop_loss_is_in_cooldown(self):
        # The exact live incident: stopped out this morning, must not
        # re-enter this afternoon.
        path = self._log([_row("PATANJALI.NS", days_ago=0, pnl="-94.50", today=self.today)])
        self.assertIn("PATANJALI.NS",
                      symbols_in_cooldown(3, today=self.today, path=path))

    def test_loss_two_trading_days_ago_still_cooling(self):
        path = self._log([_row("PATANJALI.NS", days_ago=2, pnl="-94.50", today=self.today)])
        self.assertIn("PATANJALI.NS",
                      symbols_in_cooldown(3, today=self.today, path=path))

    def test_loss_three_trading_days_ago_released(self):
        path = self._log([_row("PATANJALI.NS", days_ago=3, pnl="-94.50", today=self.today)])
        self.assertEqual(symbols_in_cooldown(3, today=self.today, path=path), set())

    def test_weekend_does_not_count_toward_cooldown(self):
        # Closed at a loss on Friday; the following Wednesday is only 3
        # trading days later (Mon, Tue, Wed) even though 5 calendar days
        # passed -- released exactly then, not earlier.
        friday_loss = _row("PATANJALI.NS", days_ago=5, pnl="-94.50", today=self.today)
        path = self._log([friday_loss])
        self.assertEqual(symbols_in_cooldown(3, today=self.today, path=path), set())
        self.assertIn("PATANJALI.NS",
                      symbols_in_cooldown(4, today=self.today, path=path))

    def test_profitable_exit_never_cools(self):
        # A target hit is a setup that WORKED -- no reason to block re-entry.
        path = self._log([_row("WINNER.NS", days_ago=0, pnl="512.30", today=self.today)])
        self.assertEqual(symbols_in_cooldown(3, today=self.today, path=path), set())

    def test_unknown_pnl_treated_as_loss(self):
        # Exit price couldn't be recovered from Kite -- conservative default.
        path = self._log([_row("UNKNOWN.NS", days_ago=0, pnl="", today=self.today)])
        self.assertIn("UNKNOWN.NS",
                      symbols_in_cooldown(3, today=self.today, path=path))

    def test_missing_log_file_means_no_cooldowns(self):
        self.assertEqual(
            symbols_in_cooldown(3, today=self.today, path="does_not_exist.csv"), set())


if __name__ == "__main__":
    unittest.main()
