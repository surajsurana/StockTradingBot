"""
Unit tests for reporting/pool_summary.py -- a synthetic deployment/state
tree in a temp dir, a fake price function, hand-calculated numbers, and
the Telegram formatter. Run with:

    python test_pool_summary.py
"""

import json
import os
import tempfile
import unittest
from datetime import date

from reporting.pool_summary import build_pool_summary, format_pool_summary, inr

TODAY = date(2026, 9, 10)


def _write(path, payload, jsonl=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if jsonl:
            for row in payload:
                f.write(json.dumps(row) + "\n")
        else:
            json.dump(payload, f)


def _state_tree():
    root = tempfile.mkdtemp()
    # Pool A: one book updated today with a position and two closed trades, one book not updated
    _write(os.path.join(root, "paper_trading", "alpha", "portfolio.json"), {
        "cash": 40000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-10",
        "positions": {"X.NS": {"entry_price": 100.0, "quantity": 500, "stop_loss": 92.0}},
    })
    _write(os.path.join(root, "paper_trading", "alpha", "trades.jsonl"), [
        {"symbol": "Y.NS", "pnl": -1000.0, "exit_date": "2026-09-09"},
        {"symbol": "Z.NS", "pnl": 250.0, "exit_date": "2026-09-10"},
    ], jsonl=True)
    _write(os.path.join(root, "paper_trading", "beta", "portfolio.json"), {
        "cash": 100000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-09", "positions": {},
    })
    # Pool A1: one legacy book
    _write(os.path.join(root, "paper_trading_legacy", "legacy1", "portfolio.json"), {
        "cash": 20000.0, "starting_capital": 1000000.0, "last_processed_date": "2026-09-10",
        "positions": {"L.NS": {"entry_price": 200.0, "quantity": 100, "stop_loss": 184.0}},
    })
    _write(os.path.join(root, "portfolio_b", "portfolio.json"),
           {"cash": 100000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-10", "positions": {}})
    _write(os.path.join(root, "portfolio_c", "portfolio.json"), {
        "cash": 88000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-10",
        "positions": {"C.NS": {"entry_price": 12.0, "quantity": 1000, "stop_loss": 11.0}},
    })
    _write(os.path.join(root, "pool_d", "portfolio.json"), {
        "last_processed_date": "2026-09-10", "positions": {}, "starting_capital": 100000.0, "cash": 100108.0,
        "realized_pnl_today": 50.0, "trades_today_by_symbol": {"A": 1, "B": 1},
    })
    _write(os.path.join(root, "pool_d", "trades.jsonl"),
           [{"symbol": "A", "pnl": 60.0}, {"symbol": "B", "pnl": -10.0}, {"symbol": "A", "pnl": 58.0}], jsonl=True)
    return root


PRICES = {"X.NS": 104.0, "L.NS": 190.0, "C.NS": 12.5}


class TestBuildPoolSummary(unittest.TestCase):
    def setUp(self):
        self.root = _state_tree()
        self.summary = build_pool_summary(self.root, {"alpha": "Alpha", "beta": "Beta"},
                                          lambda symbols: {s: PRICES[s] for s in symbols}, today=TODAY)

    def test_pool_a_numbers_are_hand_calculated(self):
        a = self.summary["pools"]["A"]
        self.assertEqual(a["positions"], 1)
        self.assertAlmostEqual(a["deployed"], 50000.0)          # 100 x 500
        self.assertAlmostEqual(a["cash"], 140000.0)             # 40k + 100k
        self.assertAlmostEqual(a["unrealised"], 2000.0)         # (104-100) x 500
        self.assertAlmostEqual(a["realised"], -750.0)           # -1000 + 250
        self.assertAlmostEqual(a["realised_today"], 250.0)
        self.assertEqual((a["books"], a["updated_today"]), (2, 1))
        self.assertEqual(a["not_updated"], ["Beta"])

    def test_other_pools(self):
        p = self.summary["pools"]
        self.assertAlmostEqual(p["A1"]["unrealised"], -1000.0)   # (190-200) x 100
        self.assertAlmostEqual(p["C"]["unrealised"], 500.0)      # (12.5-12) x 1000
        self.assertAlmostEqual(p["B"]["cash"], 100000.0)
        d = self.summary["pool_d"]
        self.assertAlmostEqual(d["realised"], 108.0)
        self.assertAlmostEqual(d["realised_today"], 50.0)
        self.assertAlmostEqual(d["cash"], 100108.0)
        self.assertEqual((d["trades_today"], d["trades_total"]), (2, 3))

    def test_overall_totals_include_pool_d_realised_and_exclude_a1(self):
        o = self.summary["overall"]
        self.assertAlmostEqual(o["deployed"], 50000 + 12000)          # A + C (+ D's 0); A1's 20,000 not counted
        self.assertAlmostEqual(o["cash"], 140000 + 100000 + 88000 + 100108)   # A + B + C + D
        self.assertAlmostEqual(o["unrealised"], 2000.0 + 500.0)        # A1's -1,000 not counted
        self.assertAlmostEqual(o["realised"], -750.0 + 108.0)
        self.assertAlmostEqual(o["realised_today"], 250.0 + 50.0)
        self.assertEqual(o["positions"], 2)

    def test_symbol_without_a_price_falls_back_to_entry(self):
        summary = build_pool_summary(self.root, {"alpha": "Alpha"}, lambda symbols: {}, today=TODAY)
        self.assertAlmostEqual(summary["pools"]["A"]["unrealised"], 0.0)


class TestFormat(unittest.TestCase):
    def test_indian_grouping_and_signs(self):
        self.assertEqual(inr(568418), "Rs.5,68,418")
        self.assertEqual(inr(-3898, signed=True), "-Rs.3,898")
        self.assertEqual(inr(828, signed=True), "+Rs.828")
        self.assertEqual(inr(0, signed=True), "Rs.0")
        self.assertEqual(inr(3115331), "Rs.31,15,331")

    def test_message_has_every_pool_and_no_underscores(self):
        root = _state_tree()
        summary = build_pool_summary(root, {"alpha": "Alpha", "beta": "Beta"},
                                     lambda symbols: {s: PRICES[s] for s in symbols}, today=TODAY)
        text = format_pool_summary(summary)
        for needle in ("*Paper Trading -- Thu 10 Sep 2026*", "*Pool A*", "*Pool A1", "*Pool B*", "*Pool C*",
                       "*Pool D (intraday)*", "*All pools (A, B, C, D -- A1 not counted)*",
                       "Deployed Rs.50,000 | Cash Rs.1,40,000",
                       "Unrealised +Rs.2,000 | Realised -Rs.750 (today +Rs.250)", "Not updated today: Beta"):
            self.assertIn(needle, text)
        self.assertNotIn("_", text)


if __name__ == "__main__":
    unittest.main()
