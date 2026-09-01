"""Tests for portfolio_b/report.py's Telegram message formatting."""

import unittest

from portfolio_b.report import format_portfolio_b_message


class TestFormatPortfolioBMessage(unittest.TestCase):
    def test_skipped_already_processed_shows_that_status(self):
        result = {"status": "skipped_already_processed", "as_of_date": "2024-01-05",
                  "last_processed_date": "2024-01-05"}
        message = format_portfolio_b_message(result)
        self.assertIn("Already processed", message)

    def test_no_activity_day_says_so(self):
        result = {"status": "processed", "as_of_date": "2024-01-05", "new_entries": [], "new_exits": [],
                  "open_positions": 2, "cash": 50000.0, "mark_to_market_equity": 101000.0}
        message = format_portfolio_b_message(result)
        self.assertIn("No new entries or exits today", message)
        self.assertIn("Portfolio B", message)

    def test_entries_and_exits_are_both_listed(self):
        result = {
            "status": "processed", "as_of_date": "2024-01-05",
            "new_entries": [{"symbol": "RVNL.NS", "quantity": 10, "entry_price": 100.0}],
            "new_exits": [{"symbol": "VEDL.NS", "quantity": 5, "exit_price": 210.0, "pnl": 50.0,
                            "exit_reason": "stop_loss"}],
            "open_positions": 3, "cash": 20000.0, "mark_to_market_equity": 99000.0,
        }
        message = format_portfolio_b_message(result)
        self.assertIn("RVNL.NS", message)
        self.assertIn("VEDL.NS", message)


if __name__ == "__main__":
    unittest.main()
