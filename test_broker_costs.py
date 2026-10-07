"""
Unit tests for swing_research/broker_costs.py -- real Indian broker charges applied to a backtest.

The bug these exist for: the research pipeline modelled friction as a percentage and had no concept
of a flat fee, so it charged 0.2% a round trip while the live books paid 0.68%. The difference is
entirely the DP charge, which is Rs13.5 per scrip however small the trade, and the trades were small.

    python test_broker_costs.py
"""

import unittest
from dataclasses import dataclass
from datetime import date

from swing_research.broker_costs import (FLAT_ROUND_TRIP, apply_broker_costs, charge_equity_curve,
                                         charge_trades, min_viable_book_size, min_viable_notional,
                                         round_trip_pct, trade_charges, variable_round_trip_pct)


@dataclass
class FakeTrade:
    symbol: str = "ACME"
    entry_date: date = date(2026, 9, 1)
    exit_date: date = date(2026, 9, 8)
    entry_price: float = 100.0
    exit_price: float = 110.0
    quantity: int = 35
    pnl: float = 350.0
    exit_reason: str = "signal_exit"
    direction: str = "BUY"
    unit_number: int = 1


class TestTheCostCurveMatchesTheRealBooks(unittest.TestCase):
    """The whole point is that cost depends on trade SIZE. These numbers are the ones measured from
    Pools A and F on 2026-10-07, so a rate change that breaks the match will be noticed."""

    def test_the_median_live_trade_costs_what_the_live_books_actually_paid(self):
        # 774 closed trades, median notional Rs3,532, measured all-in round trip ~0.68%
        self.assertAlmostEqual(round_trip_pct(3532), 0.00673, places=4)

    def test_a_small_trade_is_ruinous_and_a_large_one_is_not(self):
        self.assertAlmostEqual(round_trip_pct(870), 0.0205, places=3)      # the 10th percentile
        self.assertAlmostEqual(round_trip_pct(100_000), 0.00238, places=4)

    def test_cost_falls_monotonically_with_size_and_never_below_the_statutory_floor(self):
        sizes = [1_000, 5_000, 20_000, 100_000, 1_000_000]
        costs = [round_trip_pct(s) for s in sizes]
        self.assertEqual(costs, sorted(costs, reverse=True))
        self.assertGreater(costs[-1], variable_round_trip_pct())

    def test_the_variable_part_is_what_the_old_model_assumed_and_the_flat_part_is_what_it_missed(self):
        # execution_realism_engine.py calibrates to 0.1% one way -- 0.2% round trip. The statutory
        # percentages alone are already slightly more than that, and the flat fee is pure addition.
        self.assertAlmostEqual(variable_round_trip_pct(), 0.00222, places=4)
        self.assertAlmostEqual(FLAT_ROUND_TRIP, 15.93, places=2)

    def test_a_zero_or_negative_notional_has_no_meaningful_cost_ratio(self):
        self.assertIsNone(round_trip_pct(0))

    def test_intraday_is_far_cheaper_at_the_same_size_because_it_pays_no_dp(self):
        # not perfectly size-independent either -- intraday brokerage is capped at Rs20, which is its
        # own flat component on large trades -- but the ruinous small-trade penalty is gone
        self.assertLess(round_trip_pct(3532, intraday=True), round_trip_pct(3532) / 5)


class TestTheSizeAtWhichATradeIsWorthTaking(unittest.TestCase):
    def test_the_floor_is_the_notional_whose_cost_equals_the_budget(self):
        for budget in (0.003, 0.0035, 0.005):
            self.assertAlmostEqual(round_trip_pct(min_viable_notional(budget)), budget, places=5)

    def test_a_tighter_budget_demands_a_bigger_trade(self):
        self.assertGreater(min_viable_notional(0.003), min_viable_notional(0.005))

    def test_a_budget_below_the_statutory_floor_is_unreachable_at_any_size(self):
        # 0.1% round trip cannot be bought: STT alone is 0.2%. Saying "impossible" beats returning a
        # number that looks achievable.
        self.assertEqual(min_viable_notional(0.001), float("inf"))

    def test_intraday_needs_no_floor_because_it_pays_no_flat_fee(self):
        self.assertEqual(min_viable_notional(intraday=True), 0.0)

    def test_a_one_percent_risk_book_with_a_wide_stop_needs_three_lakh_not_one(self):
        """The finding this module was written for. Risk sizing makes a position worth
        capital x risk / stop, so a 1% risk budget against a 25% stop puts only Rs4,000 to work out
        of Rs1,00,000 -- and Rs4,000 cannot carry a Rs15.93 flat fee."""
        self.assertAlmostEqual(min_viable_book_size(0.01, 0.25), 311_730, delta=500)

    def test_a_four_percent_risk_book_is_already_viable_at_one_lakh(self):
        # the strategies that size at 4% against a 20% stop are not the problem
        self.assertLess(min_viable_book_size(0.04, 0.20), 100_000)

    def test_nonsense_sizing_inputs_give_no_answer_rather_than_a_wrong_one(self):
        self.assertIsNone(min_viable_book_size(0, 0.25))
        self.assertIsNone(min_viable_book_size(0.01, 0))


class TestChargingATradeList(unittest.TestCase):
    def test_pnl_comes_down_by_exactly_what_the_contract_note_would_take(self):
        trade = FakeTrade()
        expected = trade_charges(trade)["total"]
        out = charge_trades([trade])
        self.assertAlmostEqual(out["trades"][0].pnl, 350.0 - expected, places=6)
        self.assertAlmostEqual(out["total"], expected, places=6)

    def test_the_originals_are_left_alone(self):
        trade = FakeTrade()
        charge_trades([trade])
        self.assertEqual(trade.pnl, 350.0)

    def test_the_dp_charge_dominates_a_small_trade(self):
        # a Rs3,500 trade: DP + its GST is about half of everything paid, matching the 48.9% measured
        # across the live books
        small = FakeTrade(entry_price=100.0, exit_price=100.0, quantity=35)
        detail = charge_trades([small])["detail"]
        flat = detail["dp"] + detail["dp"] * 0.18
        self.assertGreater(flat / charge_trades([small])["total"], 0.45)

    def test_a_short_pays_stamp_duty_on_the_leg_that_is_actually_the_buy(self):
        # direction SELL means entered by selling, so the stamp-duty-bearing BUY leg is the exit
        long_, short = FakeTrade(), FakeTrade(direction="SELL")
        self.assertNotAlmostEqual(trade_charges(long_)["stamp"], trade_charges(short)["stamp"])

    def test_charges_are_booked_on_the_day_the_trade_closed(self):
        out = charge_trades([FakeTrade(exit_date=date(2026, 9, 8)),
                             FakeTrade(exit_date=date(2026, 9, 8)),
                             FakeTrade(exit_date=date(2026, 9, 9))])
        self.assertEqual(sorted(out["by_exit_date"]), [date(2026, 9, 8), date(2026, 9, 9)])
        self.assertAlmostEqual(sum(out["by_exit_date"].values()), out["total"], places=6)

    def test_an_empty_list_is_not_an_error(self):
        out = charge_trades([])
        self.assertEqual((out["trades"], out["total"]), ([], 0.0))


class TestTheEquityCurveIsChargedToo(unittest.TestCase):
    """Otherwise the trade list is net of charges while Sharpe and max drawdown are still gross --
    two different strategies described in one report."""

    def test_each_days_equity_carries_every_charge_booked_up_to_it(self):
        curve = [(date(2026, 9, 7), 100_000.0), (date(2026, 9, 8), 101_000.0),
                 (date(2026, 9, 9), 102_000.0)]
        out = charge_equity_curve(curve, {date(2026, 9, 8): 50.0, date(2026, 9, 9): 30.0})
        self.assertEqual([round(v, 2) for _, v in out], [100_000.0, 100_950.0, 101_920.0])

    def test_no_charges_leaves_the_curve_alone(self):
        curve = [(date(2026, 9, 7), 100_000.0)]
        self.assertEqual(charge_equity_curve(curve, {}), curve)

    def test_an_unrecognised_curve_shape_is_returned_untouched_rather_than_crashing(self):
        self.assertEqual(charge_equity_curve(["not a pair"], {date(2026, 9, 8): 1.0}), ["not a pair"])


class TestApplyingItToASimulationResult(unittest.TestCase):
    def test_trades_and_equity_are_both_charged_and_the_total_is_reported(self):
        result = {"trades": [FakeTrade()], "daily_equity": [(date(2026, 9, 8), 100_350.0)],
                  "trading_calendar": [date(2026, 9, 8)]}
        out = apply_broker_costs(result)
        charged = trade_charges(FakeTrade())["total"]
        self.assertAlmostEqual(out["trades"][0].pnl, 350.0 - charged, places=6)
        self.assertAlmostEqual(out["daily_equity"][0][1], 100_350.0 - charged, places=6)
        self.assertAlmostEqual(out["broker_charges"]["total"], charged, places=6)

    def test_the_input_result_is_not_mutated(self):
        result = {"trades": [FakeTrade()], "daily_equity": [(date(2026, 9, 8), 100_350.0)]}
        apply_broker_costs(result)
        self.assertEqual(result["trades"][0].pnl, 350.0)
        self.assertEqual(result["daily_equity"][0][1], 100_350.0)

    def test_everything_else_in_the_result_survives(self):
        result = {"trades": [], "daily_equity": [], "trading_calendar": [1, 2, 3], "something": "else"}
        out = apply_broker_costs(result)
        self.assertEqual(out["trading_calendar"], [1, 2, 3])
        self.assertEqual(out["something"], "else")


class TestRatesAreNotRestatedHere(unittest.TestCase):
    def test_the_numbers_come_from_the_reporting_model_the_dashboard_uses(self):
        """If research keeps its own copy of the rates, research and reporting drift apart -- which
        is exactly how a 0.2% backtest came to sit beside 0.68% books."""
        with open("swing_research/broker_costs.py", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("from reporting.equity_costs import", source)
        for rate in ("0.001", "13.5", "0.00015", "0.0000297"):
            body = "\n".join(line for line in source.splitlines()
                             if not line.strip().startswith("#"))
            self.assertNotIn(f"= {rate}", body, f"rate {rate} restated instead of imported")


if __name__ == "__main__":
    unittest.main()
