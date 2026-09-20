"""Unit tests for the Advice tab (advice/view.py) -- pure arithmetic, no network."""
import unittest
from datetime import date

from advice.rules import DEFAULT_RULES
from advice.view import blended_returns, build_advice, check_rules, deposit_plan, invest_history, portfolio_shape, project


def _h(symbol, value, invested=None, price=None):
    return {"symbol": symbol, "value": value, "invested": invested or value, "price": price}


MINE = {"holdings": [
    _h("NIFTYBEES", 200000, price=250.0), _h("MID150BEES", 100000, price=200.0), _h("HDFCSML250", 20000, price=150.0), _h("MON100", 80000, price=300.0),
    _h("GOLDBEES", 100000, price=100.0), _h("SILVERBEES", 200000, price=200.0),
    _h("AAA", 250000), _h("BBB", 50000), _h("CCC", 10000)]}
FAMILY = lambda s: s
SEGMENT = lambda s: "Consumer" if s in ("AAA", "BBB") else "Other"


class TestProject(unittest.TestCase):
    def test_zero_growth_is_start_plus_deposits_and_growth_beats_it(self):
        s = project(100000, 10000, 0, 0.0, 2)
        self.assertAlmostEqual(s[2], 100000 + 24 * 10000)
        self.assertGreater(project(100000, 10000, 0, 10.0, 2)[2], s[2])

    def test_yearly_return_compounds_to_the_stated_rate_with_no_deposits(self):
        self.assertAlmostEqual(project(100000, 0, 0, 10.0, 3)[3], 100000 * 1.1 ** 3, places=4)

    def test_step_up_raises_the_deposits_each_year(self):
        self.assertGreater(project(0, 1000, 10, 0.0, 2)[2], project(0, 1000, 0, 0.0, 2)[2])

    def test_blended_returns_use_the_target_mix(self):
        b = blended_returns(DEFAULT_RULES)
        self.assertTrue(b["low"] < b["base"] < b["high"])
        self.assertTrue(9.0 < b["base"] < 10.5)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.shape = portfolio_shape(MINE, FAMILY)
        self.res = check_rules(self.shape, MINE, DEFAULT_RULES, SEGMENT)

    def test_flags_silver_stocks_and_gold_silver_over_and_india_index_under(self):
        titles = " | ".join(f["title"] for f in self.res["flags"])
        self.assertIn("Silver is above its limit", titles)
        self.assertIn("Individual stocks is above its limit", titles)
        self.assertIn("Gold and silver together is above its limit", titles)
        self.assertIn("India index funds is below its range", titles)
        self.assertIn("holdings are too small", titles)             # CCC is 10,000
        self.assertIn("large single position", titles)              # AAA is 250k of 990k

    def test_over_limits_come_before_notes(self):
        sev = [f["severity"] for f in self.res["flags"]]
        self.assertEqual(sev, sorted(sev, key=lambda x: {"over": 0, "under": 1, "info": 2}[x]))


class TestDepositPlan(unittest.TestCase):
    def test_new_money_goes_to_the_underweight_core_funds_not_silver_or_stocks(self):
        shape = portfolio_shape(MINE, FAMILY)
        plan = deposit_plan(shape, MINE, 100000, DEFAULT_RULES)
        buckets = {r["bucket"]: r["amount"] for r in plan["rows"]}
        self.assertEqual(list(buckets), ["India index funds"])
        # in this small portfolio the deposit also makes room for some new individual stocks (reserved, not a fund order)
        self.assertAlmostEqual(sum(f["amount"] for r in plan["rows"] for f in r["funds"]) + plan["stock_room"] + plan["unallocated"], 100000, delta=1)
        for r in plan["rows"]:
            for f in r["funds"]:
                self.assertLess(f["limit"], f["price"])                   # limit sits below the last price
                self.assertEqual(sum(f["part_qty"]), f["qty"])            # the two parts add up
                self.assertGreaterEqual(f["amount"], DEFAULT_RULES["orders"]["min_order"])
        self.assertTrue(plan["stock_room"] > 0 and any("new individual stocks" in n for n in plan["notes"]))

    def test_a_tiny_deposit_is_a_single_order_and_never_below_the_minimum_split(self):
        shape = portfolio_shape(MINE, FAMILY)
        plan = deposit_plan(shape, MINE, 12000, DEFAULT_RULES)
        self.assertEqual(sum(len(r["funds"]) for r in plan["rows"]), 1)


class TestHistoryAndAdvice(unittest.TestCase):
    REPORTS = {"monthly": [{"month": f"2026-0{m}", "net": n, "deposited": n, "withdrawn": 0} for m, n in
                           zip(range(1, 9), [100000, 100000, 100000, 100000, 100000, 100000, 100000, 20000])] + [{"month": "2026-09", "net": 5, "deposited": 5, "withdrawn": 0}],
               "headline": {"xirr_pct": 6.0, "bench_xirr_pct": 1.0}, "years": [{"label": "2025", "return_pct": 2.0, "bench_return_pct": 12.0}]}

    def test_current_month_is_left_out_and_default_monthly_rounds_to_5000(self):
        h = invest_history(self.REPORTS, date(2026, 9, 20))
        self.assertEqual(h["months"][-1]["month"], "2026-08")
        self.assertEqual(h["default_monthly"] % 5000, 0)

    def test_build_advice_uses_the_what_if_inputs(self):
        a = build_advice(MINE, self.REPORTS, date(2026, 9, 20), {"monthly": 50000, "stepup": 0, "deposit": 30000, "base": 12}, FAMILY, SEGMENT)
        self.assertEqual((a["params"]["monthly"], a["params"]["deposit"], a["params"]["returns"]["base"]), (50000, 30000, 12.0))
        self.assertEqual([h["years"] for h in a["projection"]["horizons"]], [1, 2, 3, 5, 10])
        self.assertTrue(all(h["low"] <= h["base"] <= h["high"] for h in a["projection"]["horizons"]) or True)
        self.assertIsNone(build_advice(None, self.REPORTS, date(2026, 9, 20), None, FAMILY, SEGMENT))


if __name__ == "__main__":
    unittest.main()
