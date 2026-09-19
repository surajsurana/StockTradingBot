"""Unit tests for reporting/equity_costs.py -- hand-checked against Zerodha's published equity schedule."""
import unittest

from reporting.equity_costs import STCG_RATE, book_costs, leg_charges


class TestLegCharges(unittest.TestCase):
    def test_delivery_buy_and_sell(self):
        buy, sell = leg_charges(100000, "BUY", False), leg_charges(110000, "SELL", False)
        self.assertAlmostEqual(buy["stt"] + sell["stt"], 210.0)                 # 0.1% both sides
        self.assertAlmostEqual(buy["stamp"], 15.0)                              # 0.015% on buy only
        self.assertEqual(sell["stamp"], 0.0)
        self.assertEqual(buy["dp"], 0.0)
        self.assertAlmostEqual(sell["dp"], 13.5)                                # per scrip on each sell
        self.assertEqual(buy["brokerage"], 0.0)                                 # delivery is free
        self.assertAlmostEqual(buy["exchange"] + sell["exchange"], 0.0000297 * 210000)
        gst = buy["gst"] + sell["gst"]
        self.assertAlmostEqual(gst, 0.18 * (0.0000297 * 210000 + 0.000001 * 210000 + 13.5))

    def test_intraday_brokerage_is_capped_at_20_and_stt_is_sell_side_only(self):
        big = leg_charges(1_000_000, "BUY", True)
        self.assertAlmostEqual(big["brokerage"], 20.0)                          # 0.03% would be 300
        small = leg_charges(10_000, "SELL", True)
        self.assertAlmostEqual(small["brokerage"], 3.0)
        self.assertAlmostEqual(small["stt"], 2.5)                               # 0.025% of the sell leg
        self.assertEqual(leg_charges(10_000, "BUY", True)["stt"], 0.0)
        self.assertAlmostEqual(leg_charges(100_000, "BUY", True)["stamp"], 3.0)  # 0.003% on buy


class TestBookCosts(unittest.TestCase):
    def test_tax_is_on_the_gain_after_charges_and_a_loss_pays_none(self):
        trades = [{"entry_price": 100.0, "exit_price": 110.0, "quantity": 1000}]
        c = book_costs(trades, [], False, gross=10000.0)
        self.assertAlmostEqual(c["tax"], (10000.0 - c["charges"] - c["gst"]) * STCG_RATE)
        loss = book_costs([{"entry_price": 100.0, "exit_price": 90.0, "quantity": 1000}], [], False, gross=-10000.0)
        self.assertEqual(loss["tax"], 0.0)

    def test_gains_and_losses_inside_one_book_offset(self):
        # +10,000 and -10,000 gross in the same book -> net gross 0 -> no tax
        trades = [{"entry_price": 100.0, "exit_price": 110.0, "quantity": 1000}, {"entry_price": 100.0, "exit_price": 90.0, "quantity": 1000}]
        self.assertEqual(book_costs(trades, [], False, gross=0.0)["tax"], 0.0)

    def test_open_position_is_charged_as_if_sold_now_and_a_short_reverses_the_legs(self):
        opened = book_costs([], [{"entry_price": 100.0, "price": 120.0, "quantity": 10}], False, gross=200.0)
        self.assertGreater(opened["detail"]["dp"], 0)                            # the assumed sale carries a DP charge
        short = book_costs([{"entry_price": 100.0, "exit_price": 95.0, "quantity": 10, "direction": "SELL"}], [], True, gross=50.0)
        # intraday short: STT applies to the ENTRY (sell) leg, stamp to the closing (buy) leg
        self.assertAlmostEqual(short["detail"]["stt"], 0.00025 * 1000)
        self.assertAlmostEqual(short["detail"]["stamp"], 0.00003 * 950)


if __name__ == "__main__":
    unittest.main()
