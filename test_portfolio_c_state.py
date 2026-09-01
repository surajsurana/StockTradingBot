"""Tests for portfolio_c/state.py -- isolated capital/state I/O."""

import datetime
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import portfolio_c.state as pcs


class TestPortfolioCState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = patch.object(pcs, "PORTFOLIO_C_STATE_DIR", self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_fresh_portfolio_starts_at_the_fixed_starting_capital(self):
        portfolio = pcs.load_portfolio()
        self.assertEqual(portfolio["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL)
        self.assertEqual(portfolio["starting_capital"], pcs.PORTFOLIO_C_STARTING_CAPITAL)
        self.assertEqual(portfolio["positions"], {})
        self.assertEqual(portfolio["pending_entries"], {})
        self.assertEqual(portfolio["pending_exits"], {})
        self.assertIsNone(portfolio["last_processed_date"])

    def test_loading_a_fresh_portfolio_does_not_write_a_file(self):
        pcs.load_portfolio()
        import os
        self.assertFalse(os.path.exists(pcs._portfolio_path()))

    def test_save_then_load_round_trips(self):
        portfolio = pcs.load_portfolio()
        portfolio["cash"] = 55_000.0
        portfolio["positions"]["AAA.NS"] = {"entry_price": 100.0, "quantity": 10}
        pcs.save_portfolio(portfolio)

        reloaded = pcs.load_portfolio()
        self.assertEqual(reloaded["cash"], 55_000.0)
        self.assertEqual(reloaded["positions"]["AAA.NS"]["quantity"], 10)

    def test_old_saved_file_without_pending_fields_gets_them_defaulted(self):
        import os
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(pcs._portfolio_path(), "w", encoding="utf-8") as f:
            json.dump({"cash": 100000.0, "starting_capital": 100000.0, "positions": {},
                       "last_processed_date": None}, f)
        portfolio = pcs.load_portfolio()
        self.assertEqual(portfolio["pending_entries"], {})
        self.assertEqual(portfolio["pending_exits"], {})

    def test_append_trade_writes_one_json_line(self):
        pcs.append_trade({"symbol": "AAA.NS", "pnl": 123.45})
        with open(pcs._trades_path(), "r", encoding="utf-8") as f:
            lines = [line for line in f.read().strip().split("\n") if line]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["symbol"], "AAA.NS")

    def test_append_daily_equity_writes_date_cash_equity(self):
        pcs.append_daily_equity(datetime.date(2024, 1, 1), cash=90_000.0, equity=101_000.0)
        with open(pcs._daily_equity_path(), "r", encoding="utf-8") as f:
            row = json.loads(f.read().strip())
        self.assertEqual(row, {"date": "2024-01-01", "cash": 90_000.0, "equity": 101_000.0})

    def test_append_decision_log_writes_arbitrary_entry(self):
        pcs.append_decision_log({"date": "2024-01-01", "symbol": "AAA.NS", "final_ranking": 1})
        with open(pcs._decision_log_path(), "r", encoding="utf-8") as f:
            row = json.loads(f.read().strip())
        self.assertEqual(row["symbol"], "AAA.NS")

    def test_decision_log_is_append_only_across_multiple_calls(self):
        pcs.append_decision_log({"symbol": "AAA.NS"})
        pcs.append_decision_log({"symbol": "BBB.NS"})
        with open(pcs._decision_log_path(), "r", encoding="utf-8") as f:
            lines = [line for line in f.read().strip().split("\n") if line]
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
