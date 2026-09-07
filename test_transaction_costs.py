"""
Unit tests for research_lab/transaction_costs.py -- hand-calculated
against Zerodha's published intraday charge schedule. Run with:

    python test_transaction_costs.py
"""

import unittest
from datetime import date

from research_lab.backtesting_engineer import Trade
from research_lab.transaction_costs import IntradayCostModel, apply_transaction_costs, trade_cost


def _trade(direction, entry, exit_, qty, pnl):
    return Trade(symbol="X", entry_date=date(2026, 2, 3), exit_date=date(2026, 2, 3), entry_price=entry,
                 exit_price=exit_, quantity=qty, pnl=pnl, exit_reason="eod_square_off", entry_hour=14.9,
                 direction=direction)


class TestRoundTripCost(unittest.TestCase):
    def test_hand_calculated_fifty_thousand_rupee_round_trip_without_spread(self):
        model = IntradayCostModel(spread_bps_per_side=0.0)
        # buy 50,000 / sell 50,000:
        #   brokerage  min(15, 20) x 2          = 30.00
        #   STT        0.025% x 50,000          = 12.50
        #   exchange   0.00307% x 100,000       =  3.07
        #   SEBI       Rs.10/crore x 100,000    =  0.10
        #   stamp      0.003% x 50,000          =  1.50
        #   GST        18% x (30 + 3.07 + 0.10) =  5.9706
        self.assertAlmostEqual(model.round_trip_cost(50000.0, 50000.0), 53.1406, places=4)

    def test_spread_assumption_adds_bps_of_turnover(self):
        model = IntradayCostModel(spread_bps_per_side=2.0)
        # 2 bps x 100,000 turnover = 20.00 on top of 53.1406
        self.assertAlmostEqual(model.round_trip_cost(50000.0, 50000.0), 73.1406, places=4)

    def test_brokerage_is_capped_at_twenty_rupees_per_order(self):
        model = IntradayCostModel(spread_bps_per_side=0.0)
        # 0.03% of 100,000 = 30 -> capped at 20 per order
        cost = model.round_trip_cost(100000.0, 100000.0)
        expected = (20 + 20) + 25.0 + 200000 * 0.0000307 + 200000 * 0.000001 + 3.0
        expected += (40 + 200000 * 0.0000307 + 200000 * 0.000001) * 0.18
        self.assertAlmostEqual(cost, expected, places=4)


class TestTradeCost(unittest.TestCase):
    def test_short_charges_stt_on_the_entry_leg_and_stamp_on_the_exit_leg(self):
        model = IntradayCostModel(spread_bps_per_side=0.0)
        # short 100 @ 500 (sell 50,000), buy back @ 480 (buy 48,000)
        short = _trade("SELL", 500.0, 480.0, 100, 2000.0)
        self.assertAlmostEqual(trade_cost(short, model), model.round_trip_cost(48000.0, 50000.0))
        # the mirror long buys 50,000 and sells 48,000 -- STT/stamp land on different legs
        long = _trade("BUY", 500.0, 480.0, 100, -2000.0)
        self.assertAlmostEqual(trade_cost(long, model), model.round_trip_cost(50000.0, 48000.0))
        self.assertNotAlmostEqual(trade_cost(short, model), trade_cost(long, model))


class TestApplyTransactionCosts(unittest.TestCase):
    def test_returns_new_net_trades_and_the_total_leaving_inputs_untouched(self):
        model = IntradayCostModel(spread_bps_per_side=0.0)
        trades = [_trade("BUY", 500.0, 500.0, 100, 0.0), _trade("BUY", 500.0, 500.0, 100, 100.0)]
        net, total = apply_transaction_costs(trades, model)
        self.assertAlmostEqual(total, 2 * 53.1406, places=4)
        self.assertAlmostEqual(net[0].pnl, -53.1406, places=4)
        self.assertAlmostEqual(net[1].pnl, 100.0 - 53.1406, places=4)
        self.assertEqual(trades[0].pnl, 0.0)   # original untouched
        self.assertEqual(net[0].symbol, "X")

    def test_describe_is_json_friendly(self):
        import json
        json.dumps(IntradayCostModel().describe())


if __name__ == "__main__":
    unittest.main()
