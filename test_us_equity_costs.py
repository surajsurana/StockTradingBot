import unittest

from swing_research.us_equity_costs import (
    USEquityCostModel, classify_and_tax, financial_year_start, marginal_stcg_rate, LTCG_RATE, CESS_RATE,
    IBKRUSTieredCostModel,
)


class TestFinancialYearStart(unittest.TestCase):
    def test_january_belongs_to_previous_aprils_fy(self):
        from datetime import date
        self.assertEqual(financial_year_start(date(2026, 1, 15)), date(2025, 4, 1))

    def test_april_starts_a_new_fy(self):
        from datetime import date
        self.assertEqual(financial_year_start(date(2026, 4, 1)), date(2026, 4, 1))

    def test_march_belongs_to_the_fy_that_started_the_prior_april(self):
        from datetime import date
        self.assertEqual(financial_year_start(date(2026, 3, 31)), date(2025, 4, 1))


class TestMarginalStcgRate(unittest.TestCase):
    def test_zero_or_negative_gain_has_zero_rate(self):
        self.assertEqual(marginal_stcg_rate(1_400_000, 0, 0), 0.0)
        self.assertEqual(marginal_stcg_rate(1_400_000, 0, -500), 0.0)

    def test_gain_entirely_within_one_bracket(self):
        # other income 1,400,000 already sits in the 15% bracket (1.2M-1.6M); a small
        # gain that doesn't cross into the next bracket is taxed at 15% + 4% cess.
        rate = marginal_stcg_rate(1_400_000, 0, 50_000)
        self.assertAlmostEqual(rate, 0.15 * 1.04, places=6)

    def test_gain_straddling_two_brackets(self):
        # other income 1,580,000 (15% bracket, 20,000 of headroom before 1,600,000),
        # a 100,000 gain spans 20,000 at 15% and 80,000 at 20%.
        rate = marginal_stcg_rate(1_580_000, 0, 100_000)
        expected_tax = (20_000 * 0.15 + 80_000 * 0.20) * (1 + CESS_RATE)
        self.assertAlmostEqual(rate, expected_tax / 100_000, places=6)

    def test_stcg_booked_so_far_shifts_the_floor_upward(self):
        # same total position (1,400,000 other income + 200,000 already booked this FY)
        # as starting at 1,600,000 -- the next gain should land in the 20% bracket.
        rate_via_booked = marginal_stcg_rate(1_400_000, 200_000, 50_000)
        rate_direct = marginal_stcg_rate(1_600_000, 0, 50_000)
        self.assertAlmostEqual(rate_via_booked, rate_direct, places=6)

    def test_income_above_top_bracket_is_taxed_at_30_plus_cess(self):
        rate = marginal_stcg_rate(3_000_000, 0, 10_000)
        self.assertAlmostEqual(rate, 0.30 * 1.04, places=6)

    def test_zero_other_income_fills_lowest_brackets_first(self):
        # 0 other income: a 500,000 gain fills the 0% band (400k) then 5% band (100k).
        rate = marginal_stcg_rate(0, 0, 500_000)
        expected_tax = (400_000 * 0.0 + 100_000 * 0.05) * (1 + CESS_RATE)
        self.assertAlmostEqual(rate, expected_tax / 500_000, places=6)


class TestClassifyAndTax(unittest.TestCase):
    def setUp(self):
        self.model = USEquityCostModel(other_annual_income=1_400_000)

    def test_long_term_gain_taxed_flat_20_plus_cess(self):
        result = classify_and_tax("2024-01-01", "2026-06-01", 10_000, stcg_booked_so_far=0, model=self.model)
        self.assertTrue(result["is_long_term"])
        self.assertAlmostEqual(result["rate"], LTCG_RATE * (1 + CESS_RATE), places=6)
        self.assertAlmostEqual(result["tax"], 10_000 * LTCG_RATE * (1 + CESS_RATE), places=2)

    def test_short_term_gain_taxed_at_progressive_marginal_rate(self):
        result = classify_and_tax("2026-01-01", "2026-06-01", 50_000, stcg_booked_so_far=0, model=self.model)
        self.assertFalse(result["is_long_term"])
        self.assertAlmostEqual(result["rate"], marginal_stcg_rate(1_400_000, 0, 50_000), places=6)

    def test_holding_exactly_24_months_boundary_is_short_term(self):
        # 24 * 30 = 720 days; exactly 720 days held is NOT > 720, so still short-term.
        result = classify_and_tax("2024-01-01", "2025-12-21", 1_000, stcg_booked_so_far=0, model=self.model)
        self.assertFalse(result["is_long_term"])

    def test_holding_721_days_crosses_into_long_term(self):
        result = classify_and_tax("2024-01-01", "2025-12-22", 1_000, stcg_booked_so_far=0, model=self.model)
        self.assertTrue(result["is_long_term"])

    def test_loss_produces_zero_tax_not_a_negative_tax(self):
        result = classify_and_tax("2026-01-01", "2026-06-01", -5_000, stcg_booked_so_far=0, model=self.model)
        self.assertEqual(result["tax"], 0.0)
        self.assertEqual(result["rate"], 0.0)

    def test_long_term_loss_also_produces_zero_tax(self):
        result = classify_and_tax("2023-01-01", "2026-06-01", -5_000, stcg_booked_so_far=0, model=self.model)
        self.assertTrue(result["is_long_term"])
        self.assertEqual(result["tax"], 0.0)



class TestLongTermRate(unittest.TestCase):
    def test_ltcg_is_12_5_percent_without_indexation(self):
        # Finance (No.2) Act 2024, transfers on or after 23-Jul-2024 -- was wrongly 20% until 2026-10-19
        self.assertEqual(LTCG_RATE, 0.125)
        result = classify_and_tax("2023-01-01", "2026-01-01", 1_000.0, 0.0, USEquityCostModel())
        self.assertTrue(result["is_long_term"])
        self.assertAlmostEqual(result["tax"], 1_000.0 * 0.125 * 1.04, places=6)


class TestIBKRUSTieredCostModel(unittest.TestCase):
    """accounts/feasibility_report_2026-10-19.md section 2d."""

    def setUp(self):
        self.m = IBKRUSTieredCostModel()

    def test_per_share_commission_above_the_minimum(self):
        # 200 shares x $0.0035 = $0.70 > $0.35 min, well under 1% of $10,000
        expected = 0.70 + 200 * 0.003 + 200 * 0.0002
        self.assertAlmostEqual(self.m.order_cost(200, 50.0, "BUY"), expected, places=9)

    def test_the_minimum_applies_to_a_small_order(self):
        # 10 shares x $0.0035 = $0.035 -> lifted to the $0.35 minimum (1% of $500 = $5, not binding)
        expected = 0.35 + 10 * 0.003 + 10 * 0.0002
        self.assertAlmostEqual(self.m.order_cost(10, 50.0, "BUY"), expected, places=9)

    def test_the_one_percent_cap_beats_the_minimum_on_a_tiny_order(self):
        # 2 shares of a $5 stock: $10 of value, 1% = $0.10 < the $0.35 minimum, so the cap wins
        expected = 0.10 + 2 * 0.003 + 2 * 0.0002
        self.assertAlmostEqual(self.m.order_cost(2, 5.0, "BUY"), expected, places=9)

    def test_the_cap_binds_the_per_share_rate_on_a_penny_stock(self):
        # 10,000 shares at $0.20: per-share $35 > 1% of $2,000 = $20
        expected = 20.0 + 10_000 * 0.003 + 10_000 * 0.0002
        self.assertAlmostEqual(self.m.order_cost(10_000, 0.20, "BUY"), expected, places=9)

    def test_sells_add_sec_and_cat_fees_buys_do_not(self):
        buy, sell = self.m.order_cost(100, 50.0, "BUY"), self.m.order_cost(100, 50.0, "SELL")
        self.assertAlmostEqual(sell - buy, 0.0000206 * 5_000.0 + 0.000003 * 100, places=9)

    def test_round_trip_is_a_buy_at_entry_plus_a_sell_at_exit(self):
        self.assertAlmostEqual(self.m.round_trip_cost(30, 40.0, 44.0),
                               self.m.order_cost(30, 40.0, "BUY") + self.m.order_cost(30, 44.0, "SELL"), places=9)

    def test_zero_shares_costs_nothing(self):
        self.assertEqual(self.m.order_cost(0, 50.0, "SELL"), 0.0)

    def test_describe_flags_the_exchange_fee_as_unverified(self):
        self.assertIn("exchange_per_share_unverified", self.m.describe())


if __name__ == "__main__":
    unittest.main()
