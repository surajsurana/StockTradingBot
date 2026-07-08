"""
Mock-based unit tests for reporting/trade_history.py's
load_closed_trades_for_month() -- turning real closed_trades_log.csv rows
into the same BacktestResult shape Chief Investment AI's review_month()
already knows how to read. Run with:

    python test_trade_history.py
"""

import csv
import os
import tempfile
import unittest

from reporting.trade_history import load_closed_trades_for_month

FIELDNAMES = ["timestamp", "symbol", "quantity", "entry_price", "exit_price", "realized_pnl", "reason"]


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class TestLoadClosedTradesForMonth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_missing_file_returns_empty_result(self):
        os.unlink(self.path)
        result = load_closed_trades_for_month(2026, 7, starting_capital=5000, path=self.path)
        self.assertEqual(result.trades, [])
        self.assertEqual(result.total_pnl, 0.0)

    def test_filters_to_the_target_month_only(self):
        _write_csv(self.path, [
            {"timestamp": "2026-07-15T10:00:00", "symbol": "INFY.NS", "quantity": 5,
             "entry_price": 1500.0, "exit_price": 1550.0, "realized_pnl": 250.0, "reason": "GTT target hit"},
            {"timestamp": "2026-06-20T10:00:00", "symbol": "TCS.NS", "quantity": 2,
             "entry_price": 3200.0, "exit_price": 3100.0, "realized_pnl": -200.0, "reason": "GTT stop-loss hit"},
        ])

        result = load_closed_trades_for_month(2026, 7, starting_capital=5000, path=self.path)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].symbol, "INFY.NS")
        self.assertEqual(result.total_pnl, 250.0)

    def test_skips_rows_with_unknown_realized_pnl(self):
        _write_csv(self.path, [
            {"timestamp": "2026-07-10T10:00:00", "symbol": "INFY.NS", "quantity": 5,
             "entry_price": 1500.0, "exit_price": "", "realized_pnl": "", "reason": "unknown -- check Kite"},
            {"timestamp": "2026-07-11T10:00:00", "symbol": "TCS.NS", "quantity": 2,
             "entry_price": 3200.0, "exit_price": 3300.0, "realized_pnl": 200.0, "reason": "GTT target hit"},
        ])

        result = load_closed_trades_for_month(2026, 7, starting_capital=5000, path=self.path)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].symbol, "TCS.NS")

    def test_win_rate_and_ending_capital_computed_correctly(self):
        _write_csv(self.path, [
            {"timestamp": "2026-07-05T10:00:00", "symbol": "A.NS", "quantity": 1,
             "entry_price": 100.0, "exit_price": 110.0, "realized_pnl": 10.0, "reason": "target"},
            {"timestamp": "2026-07-06T10:00:00", "symbol": "B.NS", "quantity": 1,
             "entry_price": 100.0, "exit_price": 90.0, "realized_pnl": -10.0, "reason": "stop_loss"},
            {"timestamp": "2026-07-07T10:00:00", "symbol": "C.NS", "quantity": 1,
             "entry_price": 100.0, "exit_price": 120.0, "realized_pnl": 20.0, "reason": "target"},
        ])

        result = load_closed_trades_for_month(2026, 7, starting_capital=5000, path=self.path)

        self.assertEqual(len(result.trades), 3)
        self.assertAlmostEqual(result.total_pnl, 20.0)
        self.assertAlmostEqual(result.win_rate, 2 / 3)
        self.assertEqual(result.ending_capital, 5020.0)


if __name__ == "__main__":
    unittest.main()
