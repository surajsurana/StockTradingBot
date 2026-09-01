"""Tests for portfolio_c/report.py's Telegram message formatting."""

import unittest

from portfolio_c.report import format_portfolio_c_message


class TestFormatPortfolioCMessage(unittest.TestCase):
    def test_skipped_already_processed_shows_that_status(self):
        result = {"status": "skipped_already_processed", "as_of_date": "2024-01-05",
                  "last_processed_date": "2024-01-05"}
        message = format_portfolio_c_message(result)
        self.assertIn("Already processed", message)
        self.assertIn("2024-01-05", message)

    def test_no_activity_day_says_so(self):
        result = {"status": "processed", "as_of_date": "2024-01-05", "new_entries": [], "new_exits": [],
                  "open_positions": 2, "cash": 50000.0, "mark_to_market_equity": 101000.0}
        message = format_portfolio_c_message(result)
        self.assertIn("No new entries or exits today", message)
        self.assertIn("Open positions: 2", message)
        self.assertIn("101,000.00", message)

    def test_entries_and_exits_are_both_listed(self):
        result = {
            "status": "processed", "as_of_date": "2024-01-05",
            "new_entries": [{"symbol": "AAA.NS", "quantity": 10, "entry_price": 100.0}],
            "new_exits": [{"symbol": "BBB.NS", "quantity": 5, "exit_price": 210.0, "pnl": 50.0,
                            "exit_reason": "stop_loss"}],
            "open_positions": 3, "cash": 20000.0, "mark_to_market_equity": 99000.0,
        }
        message = format_portfolio_c_message(result)
        self.assertIn("Bought:", message)
        self.assertIn("AAA.NS", message)
        self.assertIn("Sold:", message)
        self.assertIn("BBB.NS", message)
        self.assertIn("+Rs.50.00", message)

    def test_symbol_with_markdown_special_char_is_escaped(self):
        result = {"status": "processed", "as_of_date": "2024-01-05",
                  "new_entries": [{"symbol": "A_B.NS", "quantity": 1, "entry_price": 100.0}],
                  "new_exits": [], "open_positions": 1, "cash": 0.0, "mark_to_market_equity": 100000.0}
        message = format_portfolio_c_message(result)
        self.assertIn("A\\_B.NS", message)


if __name__ == "__main__":
    unittest.main()
