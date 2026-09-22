"""
Unit tests for Pool E1 (run_pool_e1.py): Pool E's twin with partial profit booking, exactly Pool F's
relationship to Pool A. PartialBookingConfig's own mechanics (half at +5%, stop to entry, stop-takes-
priority) are already proven generic in test_pool_f.py -- these tests are about E1's own wiring: its
own state folder, reusing run_pool_e.py's strategy catalogue/guard, and reporting/pool_e.py reading it.

    python test_pool_e1.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta

import pandas as pd

import run_pool_e1
from deployment import paper_trading_engine as pte


def _frame(rows, start=date(2026, 9, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(rows))])
    return pd.DataFrame([{"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1000} for o, h, lo, c in rows], index=idx)


class TestSeeding(unittest.TestCase):
    def test_seeds_its_own_folder_not_pool_e1s(self):
        tmp = tempfile.mkdtemp()
        original = run_pool_e1.POOL_E1_STATE_DIR
        run_pool_e1.POOL_E1_STATE_DIR = tmp
        try:
            run_pool_e1._seed_if_missing("crypto_trend_timing")
            with open(os.path.join(tmp, "crypto_trend_timing", "portfolio.json")) as f:
                pf = json.load(f)
            self.assertEqual((pf["cash"], pf["book_currency"]), (1000.0, "USDT"))
            pf["cash"] = 5.0
            with open(os.path.join(tmp, "crypto_trend_timing", "portfolio.json"), "w") as f:
                json.dump(pf, f)
            run_pool_e1._seed_if_missing("crypto_trend_timing")   # never re-seeds an existing book
            with open(os.path.join(tmp, "crypto_trend_timing", "portfolio.json")) as f:
                self.assertEqual(json.load(f)["cash"], 5.0)
        finally:
            run_pool_e1.POOL_E1_STATE_DIR = original

    def test_reuses_pool_e_py_s_strategy_catalogue_directly(self):
        import run_pool_e
        self.assertIs(run_pool_e1.POOL_E_STRATEGIES, run_pool_e.POOL_E_STRATEGIES)   # one source of truth
        self.assertIs(run_pool_e1._guard, run_pool_e._guard)


class TestPartialBookingAppliesToACryptoBook(unittest.TestCase):
    """Confirms the engine's PartialBookingConfig (already proven generic in test_pool_f.py) actually
    fires for Pool E1's own call shape -- fractional quantities, same_day_close fills, its own folder."""

    def test_half_booked_fractionally_at_plus_five_percent(self):
        tmp = tempfile.mkdtemp()
        original, original_e1 = pte.PAPER_TRADING_STATE_DIR, run_pool_e1.POOL_E1_STATE_DIR
        pte.PAPER_TRADING_STATE_DIR = tmp
        run_pool_e1.POOL_E1_STATE_DIR = tmp
        try:
            from swing_research.base import Signal, Strategy

            class _HoldForever(Strategy):
                risk_pct_per_unit = 0.01   # same as test_pool_f.py's own _HoldForever -- 1000 USDT * 1% / Rs.8 stop = 1.25 coins
                fractional_quantities = True

                def precompute(self, df):
                    df = df.copy(); df["date"] = df.index.date; return df

                def entry_signal_at(self, row):
                    return Signal(symbol="", direction="BUY", entry_price=100.0, stop_loss=92.0) if row.date == date(2026, 9, 1) else None

                def exit_signal_at(self, row, pos):
                    return None

            run_pool_e1._seed_if_missing("crypto_trend_timing")
            data = {"BTC": _frame([(100, 101, 99, 100), (101, 106, 100, 104)])}
            run = lambda day: pte.run_daily("crypto_trend_timing", _HoldForever(), lambda: data, as_of_date=day, force=True,
                                            execution_config=pte.ExecutionRealismConfig(fill_timing="same_day_close"),
                                            min_position_value_rupees=5.0, partial_booking=run_pool_e1.PARTIAL_BOOKING)
            r1 = run(date(2026, 9, 1))
            self.assertEqual(r1["new_entries"][0]["quantity"], 1.25)
            r = run(date(2026, 9, 2))
            self.assertEqual(len(r["new_partial_exits"]), 1)
            p = r["new_partial_exits"][0]
            self.assertEqual((p["exit_price"], p["remaining"]), (105.0, 0.625))   # 1.25 coins in, half stays
        finally:
            pte.PAPER_TRADING_STATE_DIR = original
            run_pool_e1.POOL_E1_STATE_DIR = original_e1


class TestPoolE1Reporting(unittest.TestCase):
    def test_reporting_pool_e_reads_pool_e1_with_the_dirname_param(self):
        from reporting.pool_e import build_pool_e
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "pool_e1", "crypto_trend_timing"))
        with open(os.path.join(root, "pool_e1", "crypto_trend_timing", "portfolio.json"), "w") as f:
            json.dump({"cash": 600.0, "starting_capital": 1000.0, "last_processed_date": "2026-09-22", "positions": {}}, f)
        f = build_pool_e(root, {}, usdinr=100.0, today=date(2026, 9, 22), dirname="pool_e1")
        self.assertTrue(f["exists"])
        self.assertEqual(f["books"][0]["cash"], 600.0)


class TestPoolE1OnTheStrategiesTab(unittest.TestCase):
    """strategies_view()'s "Pool E, E1" combined label and variant tab -- the crypto equivalent of
    "Pool A, F", exercised against real reporting.pool_e.build_pool_e() output, not a hand-rolled dict."""

    def setUp(self):
        from types import SimpleNamespace

        from reporting.pool_e import build_pool_e
        self.root = tempfile.mkdtemp()
        for dirname, cash in (("pool_e", 800.0), ("pool_e1", 900.0)):
            book_dir = os.path.join(self.root, dirname, "crypto_trend_timing")
            os.makedirs(book_dir)
            with open(os.path.join(book_dir, "portfolio.json"), "w") as f:
                json.dump({"cash": cash, "starting_capital": 1000.0, "last_processed_date": "2026-09-22",
                          "positions": {}}, f)
            with open(os.path.join(book_dir, "trades.jsonl"), "w") as f:
                f.write(json.dumps({"symbol": "BTC", "entry_date": "2026-09-01", "exit_date": "2026-09-10",
                                    "entry_price": 100.0, "exit_price": 110.0, "quantity": 1.0, "pnl": 10.0}) + "\n")
        self.pool_e = build_pool_e(self.root, {}, usdinr=100.0, today=date(2026, 9, 22))
        self.pool_e1 = build_pool_e(self.root, {}, usdinr=100.0, today=date(2026, 9, 22), dirname="pool_e1")
        self.record = SimpleNamespace(strategy_key="crypto_trend_timing", display_name="Crypto Trend Timing",
                                      strategy_id="SW-020", deployment_status="DeploymentStatus.PAPER_TRADING",
                                      research_verdict="ResearchVerdict.PASS", primary_experiment_id="EXP-020",
                                      strategy_family="crypto research published strategy")

    def test_combined_pool_label_and_variant_tab(self):
        from dashboard.state_view import strategies_view
        rows = {r["key"]: r for r in strategies_view([self.record], "pool_d_vwap_fade",
                                                      pool_e=self.pool_e, pool_e1=self.pool_e1,
                                                      state_dir=self.root)}
        row = rows["crypto_trend_timing"]
        self.assertEqual(row["pool"], "Pool E, E1")
        self.assertEqual([v["pool"] for v in row["variants"]], ["Pool E", "Pool E1"])
        self.assertTrue(row["variants"][1]["diff"])
        self.assertIn("half is sold", row["variants"][1]["brief"])
        self.assertIn("Pool F", row["variants"][1]["how"]["source"])   # cites Pool F's own precedent for the numbers

    def test_pool_breakdown_has_one_row_per_pool_with_its_own_capital_and_trades(self):
        from dashboard.state_view import _strategy_pool_breakdown
        breakdown = _strategy_pool_breakdown("crypto_trend_timing", [], self.root, [], {}, self.pool_e, {},
                                             pool_e1=self.pool_e1)
        by_pool = {b["pool"]: b for b in breakdown}
        self.assertEqual(set(by_pool), {"Pool E", "Pool E1"})
        self.assertEqual(by_pool["Pool E"]["closed_trades"], 1)
        self.assertEqual(by_pool["Pool E1"]["closed_trades"], 1)
        self.assertAlmostEqual(by_pool["Pool E"]["capital"], self.pool_e["books"][0]["capital"] * 100.0)
        self.assertAlmostEqual(by_pool["Pool E1"]["capital"], self.pool_e1["books"][0]["capital"] * 100.0)

    def test_no_e1_book_means_no_variant_or_combined_label(self):
        from dashboard.state_view import strategies_view
        rows = {r["key"]: r for r in strategies_view([self.record], "pool_d_vwap_fade",
                                                      pool_e=self.pool_e, state_dir=self.root)}
        row = rows["crypto_trend_timing"]
        self.assertEqual(row["pool"], "Pool E")
        self.assertEqual(row["variants"], [])


if __name__ == "__main__":
    unittest.main()
