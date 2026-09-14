"""
Unit tests for Pool E reporting (reporting/pool_e.py), its place in the
daily summary totals, and run_pool_e.py's seeding/guard -- hand-calculated
fees and India VDA tax on a synthetic state tree. Run with:

    python test_pool_e.py
"""

import json
import os
import tempfile
import unittest
from datetime import date

from reporting.pool_e import build_pool_e, format_pool_e_block, usdt
from reporting.pool_summary import build_pool_summary, format_pool_summary

TODAY = date(2026, 9, 13)


def _write(path, payload, jsonl=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if jsonl:
            for row in payload:
                f.write(json.dumps(row) + "\n")
        else:
            json.dump(payload, f)


def _tree():
    root = tempfile.mkdtemp()
    _write(os.path.join(root, "pool_e", "crypto_trend_timing", "portfolio.json"), {
        "cash": 800.0, "starting_capital": 1000.0, "last_processed_date": "2026-09-13",
        "positions": {"BTC": {"entry_price": 80000.0, "quantity": 0.0025, "stop_loss": 64000.0,
                              "entry_date": "2026-09-13"}},
    })
    _write(os.path.join(root, "pool_e", "crypto_trend_timing", "trades.jsonl"), [
        {"symbol": "ETH", "entry_date": "2026-07-31", "exit_date": "2026-09-13", "entry_price": 3000.0,
         "exit_price": 3300.0, "quantity": 0.1, "pnl": 30.0, "exit_reason": "signal_exit"},
        {"symbol": "SOL", "entry_date": "2026-07-31", "exit_date": "2026-08-20", "entry_price": 150.0,
         "exit_price": 120.0, "quantity": 1.0, "pnl": -30.0, "exit_reason": "stop_loss"},
    ], jsonl=True)
    return root


class TestBuildPoolE(unittest.TestCase):
    def setUp(self):
        self.f = build_pool_e(_tree(), {"BTC": 84000.0}, usdinr=100.0, today=TODAY)

    def test_open_position_unbooked_pre_and_post_tax(self):
        u = self.f["usdt"]["unbooked"]
        self.assertAlmostEqual(u["raw"], 10.0)                      # (84000-80000) x 0.0025
        self.assertAlmostEqual(u["fees"], (200 + 210) * 0.004)      # 0.30% + 10 bps, both legs
        self.assertAlmostEqual(u["pre_tax"], 10.0 - 1.64)
        self.assertAlmostEqual(u["tax"], 3.12)                       # 31.2% of the raw gain
        self.assertAlmostEqual(u["post_tax"], 10.0 - 1.64 - 3.12)
        p = self.f["books"][0]["open_positions"][0]
        self.assertTrue(p["priced"])
        self.assertAlmostEqual(p["pct"], 5.0)
        self.assertAlmostEqual(p["unbooked_post_tax"], 5.24)

    def test_booked_ledger_no_relief_for_the_loser(self):
        b = self.f["usdt"]["booked"]
        self.assertAlmostEqual(b["raw"], 0.0)                        # +30 - 30
        self.assertAlmostEqual(b["fees"], (300 + 330) * 0.004 + (150 + 120) * 0.004)
        self.assertAlmostEqual(b["tax"], 30 * 0.312)                 # only the ETH winner is taxed
        self.assertAlmostEqual(b["post_tax"], -3.6 - 9.36)
        self.assertAlmostEqual(b["tds"], (330 + 120) * 0.01)
        t = self.f["usdt"]["booked_today"]
        self.assertAlmostEqual(t["raw"], 30.0)                       # only ETH closed today
        self.assertAlmostEqual(t["post_tax"], 30 - 2.52 - 9.36)

    def test_capital_cash_and_rupees(self):
        u, r = self.f["usdt"], self.f["inr"]
        self.assertAlmostEqual(u["deployed"], 200.0)
        self.assertAlmostEqual(u["capital"], 800 + 200 - 0.0)
        self.assertAlmostEqual(u["cash_after_tax"], 800 - 3.6 - 9.36)
        self.assertAlmostEqual(r["deployed"], 20000.0)
        self.assertAlmostEqual(r["booked"]["post_tax"], -1296.0)
        self.assertTrue(self.f["updated_today"])
        self.assertEqual(self.f["books"][0]["display_name"], "Crypto Trend Timing (Faber 10-month SMA)")

    def test_missing_price_falls_back_to_entry(self):
        f = build_pool_e(_tree(), {}, usdinr=100.0, today=TODAY)
        self.assertAlmostEqual(f["usdt"]["unbooked"]["raw"], 0.0)
        self.assertFalse(f["books"][0]["open_positions"][0]["priced"])

    def test_no_book(self):
        f = build_pool_e(tempfile.mkdtemp(), {}, usdinr=95.0, today=TODAY)
        self.assertFalse(f["exists"])
        self.assertEqual(f["usdt"]["booked"]["post_tax"], 0.0)
        self.assertEqual(format_pool_e_block(f), ["*Pool E (crypto)* -- no book yet", ""])


class TestPoolEInSummary(unittest.TestCase):
    def test_summary_totals_take_pool_e_post_tax_in_rupees(self):
        root = _tree()
        s = build_pool_summary(root, {}, lambda symbols: {}, today=TODAY, crypto_prices={"BTC": 84000.0}, usdinr=100.0)
        o = s["overall"]
        self.assertAlmostEqual(o["deployed"], 20000.0)
        self.assertAlmostEqual(o["cash"], 80000.0)
        self.assertAlmostEqual(o["unrealised"], 524.0)               # post-tax unbooked x 100
        self.assertAlmostEqual(o["realised"], -1296.0)
        self.assertAlmostEqual(o["realised_today"], (30 - 2.52 - 9.36) * 100)
        self.assertEqual(o["positions"], 1)
        text = format_pool_summary(s)
        for needle in ("*Pool E (crypto, USDT; Rs. at 100.0/USD)* -- 1 positions, 2 trades",
                       "Deployed 200.00 USDT (Rs.20,000)", "Unbooked pre-tax +8.36 USDT / post-tax +5.24 USDT (+Rs.524)",
                       "Booked raw 0.00 USDT | fees 3.60 USDT | tax 9.36 USDT",
                       "Booked pre-tax -3.60 USDT / post-tax -12.96 USDT (-Rs.1,296; today post-tax +18.12 USDT)",
                       "TDS withheld, refundable 4.50 USDT", "*All pools (A, B, C, D, E post-tax -- A1 not counted)*"):
            self.assertIn(needle, text)
        self.assertNotIn("_", text)

    def test_usdt_formatter(self):
        self.assertEqual(usdt(1234.5), "1,234.50 USDT")
        self.assertEqual(usdt(-3.6, True), "-3.60 USDT")
        self.assertEqual(usdt(5.24, True), "+5.24 USDT")


class TestRunPoolE(unittest.TestCase):
    def test_seed_and_last_completed_utc_day(self):
        import datetime
        import run_pool_e
        tmp = tempfile.mkdtemp()
        original = run_pool_e.POOL_E_STATE_DIR
        run_pool_e.POOL_E_STATE_DIR = tmp
        try:
            run_pool_e._seed_if_missing("crypto_trend_timing")
            with open(os.path.join(tmp, "crypto_trend_timing", "portfolio.json")) as f:
                pf = json.load(f)
            self.assertEqual(pf["cash"], 1000.0)
            self.assertEqual(pf["book_currency"], "USDT")
            pf["cash"] = 5.0
            with open(os.path.join(tmp, "crypto_trend_timing", "portfolio.json"), "w") as f:
                json.dump(pf, f)
            run_pool_e._seed_if_missing("crypto_trend_timing")     # never re-seeds an existing book
            with open(os.path.join(tmp, "crypto_trend_timing", "portfolio.json")) as f:
                self.assertEqual(json.load(f)["cash"], 5.0)
        finally:
            run_pool_e.POOL_E_STATE_DIR = original
        now = datetime.datetime(2026, 9, 14, 0, 15, tzinfo=datetime.timezone.utc)   # 05:45 IST on the 14th
        self.assertEqual(run_pool_e.last_completed_utc_day(now), datetime.date(2026, 9, 13))


if __name__ == "__main__":
    unittest.main()
