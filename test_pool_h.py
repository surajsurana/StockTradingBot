"""Pool H: the real Groww portfolio shown as a pool in Live mode (reporting/pool_h.py)."""
import unittest
from datetime import date

from reporting.pool_h import add_pool_h, build, fifo

ORDERS = [["2025-01-10", "AAA", "B", 10, 1000.0], ["2025-03-10", "AAA", "B", 10, 1500.0], ["2026-01-05", "AAA", "S", 15, 2250.0],
          ["2025-02-01", "BBB", "B", 4, 400.0]]
FY = [{"fy": "FY25-26", "charges": 60.0, "gst": 9.0, "brokerage": 30.0, "stt": 10.0, "dp": 5.0, "exchange": 2.0, "sebi": 1.0, "stamp": 3.0,
       "short_term": 100.0, "long_term": -20.0, "intraday": 0.0}]
MINE = {"holdings": [
    {"symbol": "AAA", "quantity": 5.0, "avg_price": 150.0, "invested": 750.0, "price": 160.0, "value": 800.0, "pnl": 50.0, "pct": 6.7, "today": 5.0},
    {"symbol": "BBB", "quantity": 4.0, "avg_price": 100.0, "invested": 400.0, "price": 90.0, "value": 360.0, "pnl": -40.0, "pct": -10.0, "today": 0.0}]}
RAW = {"orders": ORDERS, "fy": FY}


class TestFifo(unittest.TestCase):
    def test_a_sale_uses_the_oldest_buys_first(self):
        closed, lots = fifo(ORDERS)
        sale = next(c for c in closed if c["symbol"] == "AAA")
        self.assertEqual((sale["qty"], sale["matched"], sale["oldest"]), (15.0, 15.0, "2025-01-10"))
        self.assertAlmostEqual(sale["cost"], 10 * 100 + 5 * 150)                # 10 shares at 100, then 5 at 150
        self.assertEqual([(l["date"], l["qty"]) for l in lots["AAA"]], [("2025-03-10", 5.0)])

    def test_a_sale_the_history_cannot_explain_is_flagged_not_invented(self):
        closed, _ = fifo([["2025-01-10", "ZZZ", "B", 2, 200.0], ["2025-06-01", "ZZZ", "S", 5, 700.0]])
        self.assertEqual((closed[0]["qty"], closed[0]["matched"]), (5.0, 2.0))


class TestBuild(unittest.TestCase):
    def test_pool_strategy_statement_and_ledger_rows(self):
        h = build(MINE, RAW, 500.0, date(2026, 9, 21), None)
        p = h["pool"]
        self.assertEqual((p["positions"], p["deployed"], p["cash"], p["unrealised"], p["realised"]), (2, 1150.0, 500.0, 10.0, 80.0))
        self.assertAlmostEqual(p["capital"], 500 + 1150 - 80)                       # capital = cash + deployed - realised, like the other pools
        s = h["strategy"]
        self.assertEqual((s["pool"], s["type"], s["started"], s["closed_trades"], s["wins"]), ("Pool H", "Long-term", "2025-01-10", 1, 1))
        line = h["line"]
        self.assertEqual((line["charges"], line["gst"]), (51.0, 9.0))               # the real charges paid; GST shown separately
        self.assertAlmostEqual(sum(line["detail"][k] for k in ("brokerage", "exchange", "sebi", "dp", "stt", "stamp")), 51.0)
        rows = {(r["symbol"], r["status"]): r for r in h["ledger"]}
        self.assertEqual(rows[("AAA", "Open")]["pnl"], 50.0)
        sold = rows[("AAA", "Closed")]
        self.assertAlmostEqual(sold["pnl"], 2250 - 1750)                            # sold for 2250; the oldest 15 shares cost 10x100 + 5x150
        self.assertEqual((sold["bought_on"], sold["held_days"]), ("2025-01-10", (date(2026, 1, 5) - date(2025, 1, 10)).days))

    def test_nothing_to_add_without_holdings_or_orders(self):
        self.assertIsNone(build({"holdings": []}, RAW, 0, date(2026, 9, 21), None))
        self.assertIsNone(build(MINE, {"orders": [], "fy": []}, 0, date(2026, 9, 21), None))


class TestAddToState(unittest.TestCase):
    def test_pool_h_is_added_everywhere_the_other_pools_appear(self):
        state = {"pools": {"A": {"positions": 0}}, "overall": {"deployed": 0.0, "cash": 0.0, "unrealised": 0.0, "realised": 0.0, "capital": 0.0, "positions": 0},
                 "pools_info": [{"pool": "A"}], "strategies": [], "statement": [], "ledger": [{"date": "2026-09-01", "time": "", "pool": "Pool A"}]}
        self.assertTrue(add_pool_h(state, MINE, RAW, 500.0, date(2026, 9, 21), None))
        self.assertIn("H", state["pools"])
        self.assertEqual(state["overall"]["positions"], 2)
        self.assertEqual([p["pool"] for p in state["pools_info"]], ["A", "H"])
        self.assertEqual(len(state["strategies"]), 1)
        self.assertEqual(state["statement"][0]["pool"], "Pool H")
        dates = [a["date"] for a in state["ledger"]]
        self.assertEqual(dates, sorted(dates, reverse=True))                        # newest first
        self.assertFalse(add_pool_h({"pools": {}}, {"holdings": []}, RAW, 0, date(2026, 9, 21), None))


if __name__ == "__main__":
    unittest.main()
