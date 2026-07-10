"""
Mock-based unit tests for monitor_positions.py's price_pnl_text() -- the
short "current price (P&L Rs., P&L %)" fragment shown on every
position-check Telegram line. Run with:

    python test_monitor_positions.py
"""

import unittest

from execution.positions import Holding
from monitor_positions import price_pnl_text


class TestPricePnlText(unittest.TestCase):
    def test_profit_shows_positive_pnl_and_pct(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=344.10, last_price=345.60)

        text = price_pnl_text(holding)

        self.assertIn("Rs.345.60", text)
        self.assertIn("+0.44%", text)
        self.assertIn("Rs.+22.50", text)

    def test_loss_shows_negative_pnl_and_pct(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=344.10, last_price=330.00)

        text = price_pnl_text(holding)

        self.assertIn("Rs.330.00", text)
        self.assertIn("-4.10%", text)
        self.assertIn("Rs.-211.50", text)

    def test_missing_last_price_returns_empty_string(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=344.10, last_price=None)
        self.assertEqual(price_pnl_text(holding), "")

    def test_zero_average_price_returns_empty_string(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=0.0, last_price=345.60)
        self.assertEqual(price_pnl_text(holding), "")


if __name__ == "__main__":
    unittest.main()
