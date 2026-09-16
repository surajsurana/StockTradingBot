"""
Unit tests for Pool F (partial profit booking): the paper engine's
PartialBookingConfig -- half sold at +5%, stop to entry, once only, stop
takes priority, remainder follows the strategy -- plus the pool's place
in the daily summary and the dashboard.

    python test_pool_f.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta

import pandas as pd

from deployment import paper_trading_engine as pte
from swing_research.base import OpenPosition, Signal, Strategy


def _frame(rows, start=date(2026, 9, 1)):
    """rows: list of (open, high, low, close) -- one per calendar day from start."""
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(rows))])
    return pd.DataFrame([{"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1000} for o, h, lo, c in rows], index=idx)


class _HoldForever(Strategy):
    """Enters X.NS on the first day, never signals an exit -- so what
    happens after entry is entirely the engine's stop / partial logic."""
    risk_pct_per_unit = 0.01

    def precompute(self, df):
        df = df.copy(); df["date"] = df.index.date; return df

    def entry_signal_at(self, row):
        if row.date != date(2026, 9, 1):
            return None
        return Signal(symbol="", direction="BUY", entry_price=100.0, stop_loss=92.0)

    def exit_signal_at(self, row, pos):
        return None


class TestPartialBooking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.original = pte.PAPER_TRADING_STATE_DIR
        pte.PAPER_TRADING_STATE_DIR = self.tmp
        self.cfg = pte.PartialBookingConfig(trigger_pct=0.05, book_fraction=0.5, move_stop_to_entry=True)

    def tearDown(self):
        pte.PAPER_TRADING_STATE_DIR = self.original

    def _run(self, data, day, partial=True):
        return pte.run_daily("book", _HoldForever(), lambda: data, as_of_date=day, force=True,
                             execution_config=pte.ExecutionRealismConfig(fill_timing="same_day_close"),
                             partial_booking=self.cfg if partial else None)

    def _pf(self):
        with open(os.path.join(self.tmp, "book", "portfolio.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_half_booked_at_plus_five_percent_and_stop_moves_to_entry(self):
        data = {"X.NS": _frame([(100, 101, 99, 100), (101, 106, 100, 104), (104, 104.5, 103, 104)])}
        self._run(data, date(2026, 9, 1))
        qty = self._pf()["positions"]["X.NS"]["quantity"]          # 1% of 1L / 8 = 125 shares
        self.assertEqual(qty, 125)
        r = self._run(data, date(2026, 9, 2))                        # High 106 >= 105 trigger
        self.assertEqual(len(r["new_partial_exits"]), 1)
        p = r["new_partial_exits"][0]
        self.assertEqual((p["quantity"], p["exit_price"], p["remaining"]), (62, 105.0, 63))
        self.assertAlmostEqual(p["pnl"], 62 * 5.0)
        pos = self._pf()["positions"]["X.NS"]
        self.assertEqual((pos["quantity"], pos["stop_loss"], pos["partial_booked"]), (63, 100.0, True))
        with open(os.path.join(self.tmp, "book", "trades.jsonl"), encoding="utf-8") as f:
            trades = [json.loads(l) for l in f if l.strip()]
        self.assertEqual((trades[0]["exit_reason"], trades[0]["quantity"]), ("partial_profit", 62))
        r = self._run(data, date(2026, 9, 3))                        # no second booking
        self.assertEqual(r["new_partial_exits"], [])
        self.assertEqual(self._pf()["positions"]["X.NS"]["quantity"], 63)

    def test_raised_stop_then_protects_the_remainder(self):
        data = {"X.NS": _frame([(100, 101, 99, 100), (101, 106, 100, 104), (103, 103, 99.5, 100)])}
        self._run(data, date(2026, 9, 1)); self._run(data, date(2026, 9, 2))
        r = self._run(data, date(2026, 9, 3))                        # Low 99.5 <= raised stop 100
        self.assertEqual(len(r["new_exits"]), 1)
        self.assertEqual((r["new_exits"][0]["reason"], r["new_exits"][0]["exit_price"], r["new_exits"][0]["quantity"]),
                         ("stop_loss", 100.0, 63))
        self.assertNotIn("X.NS", self._pf()["positions"])

    def test_stop_takes_priority_over_the_trigger_on_the_same_day(self):
        data = {"X.NS": _frame([(100, 101, 99, 100), (100, 106, 91, 95)])}
        self._run(data, date(2026, 9, 1))
        r = self._run(data, date(2026, 9, 2))
        self.assertEqual(r["new_partial_exits"], [])
        self.assertEqual(r["new_exits"][0]["reason"], "stop_loss")

    def test_no_config_means_no_booking(self):
        data = {"X.NS": _frame([(100, 101, 99, 100), (101, 106, 100, 104)])}
        self._run(data, date(2026, 9, 1), partial=False)
        r = self._run(data, date(2026, 9, 2), partial=False)
        self.assertEqual(r["new_partial_exits"], [])
        self.assertEqual(self._pf()["positions"]["X.NS"]["quantity"], 125)


class TestPoolFReporting(unittest.TestCase):
    def test_summary_and_dashboard_see_pool_f_books(self):
        from reporting.pool_summary import build_pool_summary, format_pool_summary
        root = tempfile.mkdtemp()
        for pool_dir in ("paper_trading", "pool_f"):
            os.makedirs(os.path.join(root, pool_dir, "alpha"))
            with open(os.path.join(root, pool_dir, "alpha", "portfolio.json"), "w") as f:
                json.dump({"cash": 90000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-16",
                           "positions": {"X.NS": {"entry_price": 100.0, "quantity": 100, "stop_loss": 92.0,
                                                  "entry_date": "2026-09-16"}}}, f)
        s = build_pool_summary(root, {"alpha": "Alpha"}, lambda syms: {"X.NS": 110.0}, today=date(2026, 9, 16))
        self.assertEqual(s["pools"]["F"]["positions"], 1)
        self.assertAlmostEqual(s["pools"]["F"]["unrealised"], 1000.0)
        self.assertAlmostEqual(s["overall"]["unrealised"], 2000.0)      # A + F
        text = format_pool_summary(s)
        self.assertIn("*Pool F (Pool A twin, partial profit booking)*", text)
        self.assertIn("A, B, C, D, F, E post-tax", text)


if __name__ == "__main__":
    unittest.main()
