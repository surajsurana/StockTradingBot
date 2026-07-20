"""
Mock-based unit tests for monitor_positions.py's price_pnl_text() -- the
short "current price (P&L Rs., P&L %)" fragment shown on every
position-check Telegram line -- and _highest_high_since(), which arms the
trailing stop (risk/trailing_stop.py). Run with:

    python test_monitor_positions.py
"""

import unittest
import pandas as pd

from execution.positions import Holding
from monitor_positions import price_pnl_text, _highest_high_since


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


class TestHighestHighSince(unittest.TestCase):
    def _history(self, highs):
        idx = pd.date_range("2026-07-01", periods=len(highs), freq="D")
        return pd.DataFrame({"High": highs}, index=idx)

    def test_takes_max_high_from_entry_date_onward(self):
        df = self._history([100, 105, 98, 110, 102])  # 2026-07-01 .. 2026-07-05
        opened_at = "2026-07-03T09:15:00"
        self.assertEqual(_highest_high_since(df, opened_at), 110)

    def test_ignores_highs_before_entry_date(self):
        df = self._history([500, 105, 98, 110, 102])  # 500 is BEFORE entry, must be excluded
        opened_at = "2026-07-02T09:15:00"
        self.assertEqual(_highest_high_since(df, opened_at), 110)

    def test_no_rows_on_or_after_entry_returns_none(self):
        df = self._history([100, 105])  # 2026-07-01, 2026-07-02
        opened_at = "2026-08-01T09:15:00"
        self.assertIsNone(_highest_high_since(df, opened_at))


if __name__ == "__main__":
    unittest.main()
