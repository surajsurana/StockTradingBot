"""
Unit tests for the dashboard's manual sell (dashboard/manual_exit.py):
full and partial exits in every pool layout, Pool D's lock, the audit
trail, and the rejections. Temp state tree, no network.

    python test_manual_exit.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime

from dashboard.manual_exit import AUDIT_FILENAME, ManualExitError, apply_manual_exit

TODAY = date(2026, 9, 16)
NOW = datetime(2026, 9, 16, 11, 5)


def _write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _tree():
    root = tempfile.mkdtemp()
    _write(os.path.join(root, "paper_trading", "alpha", "portfolio.json"), {
        "cash": 1000.0, "starting_capital": 100000.0, "positions": {
            "X.NS": {"entry_price": 100.0, "quantity": 50, "stop_loss": 92.0, "entry_date": "2026-09-01"}},
        "pending_entries": {}, "pending_exits": {"X.NS": {"exit_reason": "signal_exit"}}})
    _write(os.path.join(root, "portfolio_b", "portfolio.json"), {
        "cash": 500.0, "starting_capital": 100000.0, "positions": {
            "Y.NS": {"direction": "BUY", "entry_price": 200.0, "quantity": 10, "stop_loss": 184.0,
                     "entry_date": "2026-09-10", "strategy_name": "watchlist"}},
        "pending_entries": {}, "pending_exits": {}})
    _write(os.path.join(root, "pool_d", "portfolio.json"), {
        "cash": 90000.0, "starting_capital": 100000.0, "realized_pnl_today": 0.0, "trades_today_by_symbol": {},
        "positions": {"SBIN": {"entry_price": 1000.0, "stop_loss": 1010.0, "target": 980.0, "quantity": 10,
                               "direction": "SELL", "entry_timestamp": "2026-09-16T09:40:00"}}})
    _write(os.path.join(root, "pool_e", "crypto_trend_timing", "portfolio.json"), {
        "cash": 800.0, "starting_capital": 1000.0, "positions": {
            "BTC": {"entry_price": 80000.0, "quantity": 0.0025, "stop_loss": 64000.0, "entry_date": "2026-09-13"}},
        "pending_entries": {}, "pending_exits": {}})
    return root


class TestManualExit(unittest.TestCase):
    def setUp(self):
        self.root = _tree()

    def test_pool_a_full_exit(self):
        t = apply_manual_exit(self.root, "A", "alpha", "X.NS", 50, 104.0, TODAY, NOW, price_mode="market")
        pf = json.load(open(os.path.join(self.root, "paper_trading", "alpha", "portfolio.json")))
        self.assertNotIn("X.NS", pf["positions"])
        self.assertNotIn("X.NS", pf["pending_exits"])            # a queued engine exit is cancelled too
        self.assertAlmostEqual(pf["cash"], 1000.0 + 50 * 104.0)
        self.assertEqual((t["pnl"], t["exit_reason"], t["remaining"], t["price_mode"]), (200.0, "manual", 0, "market"))
        rows = _rows(os.path.join(self.root, "paper_trading", "alpha", "trades.jsonl"))
        self.assertEqual(rows[0]["exit_date"], "2026-09-16")
        self.assertEqual(rows[0]["entry_date"], "2026-09-01")
        audit = _rows(os.path.join(self.root, AUDIT_FILENAME))
        self.assertEqual((audit[0]["pool"], audit[0]["book"], audit[0]["symbol"], audit[0]["quantity"]), ("A", "alpha", "X.NS", 50))

    def test_pool_a_partial_exit_keeps_the_rest(self):
        t = apply_manual_exit(self.root, "A", "alpha", "X.NS", 20, 98.0, TODAY, NOW)
        pf = json.load(open(os.path.join(self.root, "paper_trading", "alpha", "portfolio.json")))
        self.assertEqual(pf["positions"]["X.NS"]["quantity"], 30)
        self.assertEqual(pf["positions"]["X.NS"]["stop_loss"], 92.0)
        self.assertAlmostEqual(pf["cash"], 1000.0 + 20 * 98.0)
        self.assertEqual((t["pnl"], t["remaining"]), (-40.0, 30))

    def test_pool_b_exit_carries_strategy_name(self):
        t = apply_manual_exit(self.root, "B", None, "Y.NS", 10, 210.0, TODAY, NOW)
        pf = json.load(open(os.path.join(self.root, "portfolio_b", "portfolio.json")))
        self.assertEqual(pf["positions"], {})
        self.assertAlmostEqual(pf["cash"], 500.0 + 2100.0)
        self.assertEqual((t["pnl"], t["strategy_name"]), (100.0, "watchlist"))

    def test_pool_d_short_exit_releases_reserved_cash_and_books_today(self):
        t = apply_manual_exit(self.root, "D", None, "SBIN", 10, 990.0, TODAY, NOW)
        pf = json.load(open(os.path.join(self.root, "pool_d", "portfolio.json")))
        self.assertEqual(pf["positions"], {})
        self.assertAlmostEqual(t["pnl"], (1000.0 - 990.0) * 10)                 # short: entry - exit
        self.assertAlmostEqual(pf["cash"], 90000.0 + 1000.0 * 10 + 100.0)
        self.assertAlmostEqual(pf["realized_pnl_today"], 100.0)
        self.assertEqual(pf["trades_today_by_symbol"]["SBIN"], 1)
        row = _rows(os.path.join(self.root, "pool_d", "trades.jsonl"))[0]
        self.assertEqual((row["reason"], row["exit_timestamp"]), ("manual", "2026-09-16T11:05"))

    def test_pool_d_refuses_during_a_tick(self):
        lock = os.path.join(self.root, "pool_d", "tick.lock")
        with open(lock, "w") as f:
            f.write("pid")
        os.utime(lock, (NOW.timestamp(), NOW.timestamp()))
        with self.assertRaises(ManualExitError):
            apply_manual_exit(self.root, "D", None, "SBIN", 10, 990.0, TODAY, NOW)

    def test_pool_e_fractional_partial(self):
        t = apply_manual_exit(self.root, "E", "crypto_trend_timing", "BTC", 0.001, 84000.0, TODAY, NOW)
        pf = json.load(open(os.path.join(self.root, "pool_e", "crypto_trend_timing", "portfolio.json")))
        self.assertAlmostEqual(pf["positions"]["BTC"]["quantity"], 0.0015)
        self.assertAlmostEqual(pf["cash"], 800.0 + 84.0)
        self.assertAlmostEqual(t["pnl"], 4.0)

    def test_rejections(self):
        with self.assertRaises(ManualExitError):
            apply_manual_exit(self.root, "A", "alpha", "NOPE.NS", 1, 100.0, TODAY, NOW)
        with self.assertRaises(ManualExitError):
            apply_manual_exit(self.root, "A", "alpha", "X.NS", 51, 100.0, TODAY, NOW)
        with self.assertRaises(ManualExitError):
            apply_manual_exit(self.root, "A", "alpha", "X.NS", 0, 100.0, TODAY, NOW)
        with self.assertRaises(ManualExitError):
            apply_manual_exit(self.root, "A", "alpha", "X.NS", 5, 0, TODAY, NOW)
        with self.assertRaises(ManualExitError):
            apply_manual_exit(self.root, "Z", None, "X.NS", 5, 100.0, TODAY, NOW)


class TestManualEntry(unittest.TestCase):
    def setUp(self):
        self.root = _tree()

    def test_pool_a_buy_new_and_add(self):
        from dashboard.manual_exit import apply_manual_entry
        t = apply_manual_entry(self.root, "A", "alpha", "Z.NS", 4, 200.0, TODAY, NOW, price_mode="market")
        pf = json.load(open(os.path.join(self.root, "paper_trading", "alpha", "portfolio.json")))
        self.assertEqual(pf["positions"]["Z.NS"]["quantity"], 4)
        self.assertAlmostEqual(pf["positions"]["Z.NS"]["stop_loss"], 184.0)          # 8% below the fill
        self.assertEqual(pf["positions"]["Z.NS"]["entry_date"], "2026-09-16")
        self.assertAlmostEqual(pf["cash"], 1000.0 - 800.0)
        self.assertEqual((t["cost"], t["cash_left"]), (800.0, 200.0))
        apply_manual_entry(self.root, "A", "alpha", "X.NS", 1, 120.0, TODAY, NOW)    # adds to the existing 50 @ 100
        pf = json.load(open(os.path.join(self.root, "paper_trading", "alpha", "portfolio.json")))
        self.assertEqual(pf["positions"]["X.NS"]["quantity"], 51)
        self.assertAlmostEqual(pf["positions"]["X.NS"]["entry_price"], round(5120 / 51, 2))
        self.assertAlmostEqual(pf["positions"]["X.NS"]["stop_loss"], 110.4)          # the tighter of old 92 and new 110.4
        self.assertAlmostEqual(pf["cash"], 80.0)
        audit = _rows(os.path.join(self.root, AUDIT_FILENAME))
        self.assertEqual([a["action"] for a in audit], ["buy", "buy"])

    def test_pool_b_buy_has_the_engine_fields(self):
        from dashboard.manual_exit import apply_manual_entry
        apply_manual_entry(self.root, "B", None, "W.NS", 2, 100.0, TODAY, NOW)
        pos = json.load(open(os.path.join(self.root, "portfolio_b", "portfolio.json")))["positions"]["W.NS"]
        self.assertEqual((pos["direction"], pos["strategy_name"], pos["target"]), ("BUY", "manual", None))

    def test_refusals(self):
        from dashboard.manual_exit import apply_manual_entry
        with self.assertRaises(ManualExitError):
            apply_manual_entry(self.root, "A", "alpha", "Z.NS", 100, 200.0, TODAY, NOW)   # not enough cash
        with self.assertRaises(ManualExitError):
            apply_manual_entry(self.root, "D", None, "SBIN", 1, 1000.0, TODAY, NOW)       # intraday pool
        with self.assertRaises(ManualExitError):
            apply_manual_entry(self.root, "A", "alpha", "Z.NS", 0, 200.0, TODAY, NOW)


if __name__ == "__main__":
    unittest.main()
