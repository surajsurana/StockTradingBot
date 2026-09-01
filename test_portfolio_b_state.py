"""Tests for portfolio_b/state.py -- isolated capital/state I/O.
Mirrors test_portfolio_c_state.py exactly (same schema, own namespace)."""

import datetime
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import portfolio_b.state as pbs


class TestPortfolioBState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = patch.object(pbs, "PORTFOLIO_B_STATE_DIR", self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_fresh_portfolio_starts_at_the_fixed_starting_capital(self):
        portfolio = pbs.load_portfolio()
        self.assertEqual(portfolio["cash"], pbs.PORTFOLIO_B_STARTING_CAPITAL)
        self.assertEqual(portfolio["starting_capital"], pbs.PORTFOLIO_B_STARTING_CAPITAL)
        self.assertEqual(portfolio["positions"], {})
        self.assertIsNone(portfolio["last_processed_date"])

    def test_loading_a_fresh_portfolio_does_not_write_a_file(self):
        pbs.load_portfolio()
        import os
        self.assertFalse(os.path.exists(pbs._portfolio_path()))

    def test_save_then_load_round_trips(self):
        portfolio = pbs.load_portfolio()
        portfolio["cash"] = 55_000.0
        portfolio["positions"]["AAA.NS"] = {"entry_price": 100.0, "quantity": 10}
        pbs.save_portfolio(portfolio)

        reloaded = pbs.load_portfolio()
        self.assertEqual(reloaded["cash"], 55_000.0)
        self.assertEqual(reloaded["positions"]["AAA.NS"]["quantity"], 10)

    def test_append_trade_writes_one_json_line(self):
        pbs.append_trade({"symbol": "AAA.NS", "pnl": 123.45})
        with open(pbs._trades_path(), "r", encoding="utf-8") as f:
            lines = [line for line in f.read().strip().split("\n") if line]
        self.assertEqual(len(lines), 1)

    def test_append_daily_equity_writes_date_cash_equity(self):
        pbs.append_daily_equity(datetime.date(2024, 1, 1), cash=90_000.0, equity=101_000.0)
        with open(pbs._daily_equity_path(), "r", encoding="utf-8") as f:
            row = json.loads(f.read().strip())
        self.assertEqual(row, {"date": "2024-01-01", "cash": 90_000.0, "equity": 101_000.0})

    def test_append_decision_log_is_append_only(self):
        pbs.append_decision_log({"symbol": "AAA.NS"})
        pbs.append_decision_log({"symbol": "BBB.NS"})
        with open(pbs._decision_log_path(), "r", encoding="utf-8") as f:
            lines = [line for line in f.read().strip().split("\n") if line]
        self.assertEqual(len(lines), 2)

    def test_portfolio_b_and_portfolio_c_state_dirs_are_different(self):
        import portfolio_c.state as pcs
        self.assertNotEqual(pbs.PORTFOLIO_B_STATE_DIR, pcs.PORTFOLIO_C_STATE_DIR)

    def test_load_watchlist_seeds_from_default_on_first_call(self):
        result = pbs.load_watchlist(default={"AAA.NS": "Company A", "BBB.NS": "Company B"})
        self.assertEqual(result, {"AAA.NS": "Company A", "BBB.NS": "Company B"})
        import os
        self.assertTrue(os.path.exists(pbs._watchlist_path()))

    def test_load_watchlist_does_not_reseed_once_file_exists(self):
        pbs.load_watchlist(default={"AAA.NS": "Company A"})
        pbs.save_watchlist({"CHANGED.NS": "Changed Co"})
        result = pbs.load_watchlist(default={"AAA.NS": "Company A"})
        self.assertEqual(result, {"CHANGED.NS": "Changed Co"},
                          "must never revert to the default once a real file exists")

    def test_save_watchlist_then_load_round_trips(self):
        pbs.save_watchlist({"X.NS": "X Co", "Y.NS": "Y Co"})
        self.assertEqual(pbs.load_watchlist(default={}), {"X.NS": "X Co", "Y.NS": "Y Co"})

    def test_load_watchlist_migrates_old_flat_list_format(self):
        """Backward compatibility: the original schema (added
        2026-09-01, before company names existed) was a plain JSON
        array of symbols. An existing file in that format must be
        upgraded in place, never silently discarded."""
        import json
        import os
        os.makedirs(pbs.PORTFOLIO_B_STATE_DIR, exist_ok=True)
        with open(pbs._watchlist_path(), "w", encoding="utf-8") as f:
            json.dump(["OLD.NS", "ALSO_OLD.NS"], f)

        result = pbs.load_watchlist(default={"SHOULD_NOT.NS": "Be used"})
        self.assertEqual(result, {"OLD.NS": "", "ALSO_OLD.NS": ""})
        # And the upgrade must have been persisted, not just returned in-memory.
        reloaded = pbs.load_watchlist(default={})
        self.assertEqual(reloaded, {"OLD.NS": "", "ALSO_OLD.NS": ""})

    def test_telegram_offset_defaults_to_zero(self):
        self.assertEqual(pbs.load_telegram_offset(), 0)

    def test_telegram_offset_round_trips(self):
        pbs.save_telegram_offset(42)
        self.assertEqual(pbs.load_telegram_offset(), 42)

    def test_pending_action_defaults_to_none(self):
        self.assertIsNone(pbs.load_pending_action())

    def test_pending_action_round_trips(self):
        pbs.save_pending_action("addstock")
        self.assertEqual(pbs.load_pending_action(), "addstock")

    def test_pending_action_can_be_cleared(self):
        pbs.save_pending_action("addstock")
        pbs.save_pending_action(None)
        self.assertIsNone(pbs.load_pending_action())


if __name__ == "__main__":
    unittest.main()
