import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from reporting.pool_i import build_pool_i, POOL_I_STARTING_CAPITAL_USD


class TestBuildPoolI(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _write_book(self, key, portfolio, trades):
        book_dir = os.path.join(self.state_dir, "pool_i", key)
        os.makedirs(book_dir, exist_ok=True)
        with open(os.path.join(book_dir, "portfolio.json"), "w", encoding="utf-8") as f:
            json.dump(portfolio, f)
        with open(os.path.join(book_dir, "trades.jsonl"), "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

    def test_no_books_yet(self):
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1))
        self.assertFalse(result["exists"])
        self.assertEqual(result["books"], [])

    def test_seeded_book_with_no_activity(self):
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": POOL_I_STARTING_CAPITAL_USD, "starting_capital": POOL_I_STARTING_CAPITAL_USD,
                          "positions": {}, "last_processed_date": "2026-10-01"}, [])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1))
        self.assertTrue(result["exists"])
        b = result["books"][0]
        self.assertEqual(b["cash"], POOL_I_STARTING_CAPITAL_USD)
        self.assertEqual(b["positions"], 0)
        self.assertTrue(b["updated_today"])

    def test_open_position_unbooked_pnl_and_inr_conversion(self):
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 500.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD,
                          "positions": {"AAPL": {"entry_price": 100.0, "quantity": 5, "entry_date": "2026-09-01"}},
                          "last_processed_date": "2026-10-01"}, [])
        result = build_pool_i(self.state_dir, {"AAPL": 110.0}, 90.0, today=date(2026, 10, 1),
                              other_annual_income=1_400_000)
        b = result["books"][0]
        self.assertEqual(b["positions"], 1)
        self.assertEqual(b["deployed"], 500.0)
        # (110-100)*5 = 50 raw gain, held ~1 month -> short-term, taxed at the 15% New Regime
        # bracket (1,400,000 other income) + 4% cess.
        self.assertEqual(b["unbooked"]["raw"], 50.0)
        self.assertAlmostEqual(b["unbooked"]["tax"], 50.0 * 0.15 * 1.04, places=2)
        self.assertAlmostEqual(result["inr"]["unbooked"]["raw"], 50.0 * 90.0, places=2)

    def test_long_term_closed_trade_taxed_flat_20_plus_cess(self):
        self._write_book("cross_sectional_momentum_us",
                         {"cash": 1_200.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"},
                         [{"symbol": "MSFT", "entry_price": 100.0, "exit_price": 200.0, "quantity": 10,
                           "entry_date": "2023-01-01", "exit_date": "2026-01-01", "pnl": 1000.0}])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1), other_annual_income=1_400_000)
        b = result["books"][0]
        self.assertAlmostEqual(b["booked"]["tax"], 1000.0 * 0.20 * 1.04, places=2)
        self.assertAlmostEqual(b["booked"]["post_tax"], 1000.0 - 1000.0 * 0.20 * 1.04, places=2)

    def test_progressive_stcg_across_multiple_trades_same_fy(self):
        # Two short-term trades in the same FY: the second trade's marginal rate must
        # reflect the first trade's gain already pushing income into a higher bracket.
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 1_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"},
                         [{"symbol": "AAPL", "entry_price": 100.0, "exit_price": 300.0, "quantity": 1,
                           "entry_date": "2026-04-05", "exit_date": "2026-05-01", "pnl": 200_000.0},
                          {"symbol": "MSFT", "entry_price": 100.0, "exit_price": 150.0, "quantity": 1,
                           "entry_date": "2026-05-05", "exit_date": "2026-06-01", "pnl": 50.0}])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1), other_annual_income=1_400_000)
        b = result["books"][0]
        # 1,400,000 + 200,000 = 1,600,000 floor for the second trade -> lands at the 20% bracket edge.
        from swing_research.us_equity_costs import marginal_stcg_rate
        expected_second_rate = marginal_stcg_rate(1_400_000, 200_000, 50.0)
        self.assertGreater(expected_second_rate, marginal_stcg_rate(1_400_000, 0, 50.0))
        total_expected_tax = (200_000 * marginal_stcg_rate(1_400_000, 0, 200_000)
                              + 50 * expected_second_rate)
        self.assertAlmostEqual(b["booked"]["tax"], total_expected_tax, places=2)

    def test_financial_year_boundary_resets_the_progressive_total(self):
        # A trade just before April 1 and one just after must NOT share a running STCG total.
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 1_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"},
                         [{"symbol": "AAPL", "entry_price": 100.0, "exit_price": 300.0, "quantity": 1,
                           "entry_date": "2026-02-01", "exit_date": "2026-03-20", "pnl": 200_000.0},
                          {"symbol": "MSFT", "entry_price": 100.0, "exit_price": 150.0, "quantity": 1,
                           "entry_date": "2026-04-05", "exit_date": "2026-04-10", "pnl": 50.0}])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1), other_annual_income=1_400_000)
        b = result["books"][0]
        from swing_research.us_equity_costs import marginal_stcg_rate
        expected_tax = (200_000 * marginal_stcg_rate(1_400_000, 0, 200_000)
                        + 50 * marginal_stcg_rate(1_400_000, 0, 50.0))   # resets to 0 for the new FY
        self.assertAlmostEqual(b["booked"]["tax"], expected_tax, places=2)

    def test_totals_and_inr_fold_across_multiple_books(self):
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 1_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"}, [])
        self._write_book("cross_sectional_momentum_us",
                         {"cash": 2_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"}, [])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1))
        self.assertEqual(len(result["books"]), 2)
        self.assertEqual(result["usd"]["cash"], 3_000.0)
        self.assertAlmostEqual(result["inr"]["cash"], 3_000.0 * 90.0, places=2)


if __name__ == "__main__":
    unittest.main()
