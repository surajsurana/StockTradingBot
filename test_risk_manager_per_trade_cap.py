"""
Mock-based unit tests for RiskManager's max_capital_per_trade_pct -- caps
how much of the account a SINGLE trade can consume, independent of the
risk-based sizing math. Without this, a trade with a tight stop-loss (a
small risk_per_share) can size up to a very large quantity before the
total-deployed cap even kicks in, potentially consuming most of a day's
entire deployment budget on one position and crowding out every other
candidate for as long as it's held. Run with:

    python test_risk_manager_per_trade_cap.py
"""

import unittest

from risk.risk_manager import RiskManager
from strategies.base import Signal


def _signal(entry=1000.0, stop=970.0):
    # 3% stop distance by default -- deliberately tight, like a real
    # mean_reversion signal (entry_price * 0.97 floor)
    return Signal(symbol="TEST.NS", direction="BUY", entry_price=entry, stop_loss=stop,
                   target=1100.0, confidence=0.8, strategy_name="test", reason="test signal")


class TestPerTradeCapDefault(unittest.TestCase):
    def test_default_is_deployed_pct_divided_by_max_positions(self):
        rm = RiskManager(capital=100_000, risk_per_trade_pct=0.02, max_open_positions=5,
                          max_deployed_capital_pct=0.60, daily_loss_circuit_breaker_pct=0.03)
        self.assertAlmostEqual(rm.max_capital_per_trade_pct, 0.60 / 5)

    def test_explicit_override_respected(self):
        rm = RiskManager(capital=100_000, risk_per_trade_pct=0.02, max_open_positions=5,
                          max_deployed_capital_pct=0.60, daily_loss_circuit_breaker_pct=0.03,
                          max_capital_per_trade_pct=0.05)
        self.assertEqual(rm.max_capital_per_trade_pct, 0.05)


class TestPerTradeCapShrinksOversizedTrades(unittest.TestCase):
    def test_tight_stop_loss_trade_gets_shrunk_to_per_trade_cap(self):
        # 2% risk / 3% stop distance -> naive sizing wants ~66.7% of capital
        # on this ONE trade. max_capital_per_trade_pct = 60%/5 = 12% should
        # cap it well below that, long before the 60% total-deployed check
        # would even be reached.
        rm = RiskManager(capital=100_000, risk_per_trade_pct=0.02, max_open_positions=5,
                          max_deployed_capital_pct=0.60, daily_loss_circuit_breaker_pct=0.03)

        approved = rm.evaluate(_signal(entry=1000.0, stop=970.0))

        self.assertIsNotNone(approved)
        self.assertLessEqual(approved.capital_deployed, rm.capital * rm.max_capital_per_trade_pct + 1e-6)

    def test_five_such_trades_fit_within_the_total_deployed_cap(self):
        # The whole point: if every trade respects the per-trade cap, five
        # of them (max_open_positions) should fit within max_deployed_capital_pct
        # without the LAST one being rejected purely for lack of room.
        rm = RiskManager(capital=100_000, risk_per_trade_pct=0.02, max_open_positions=5,
                          max_deployed_capital_pct=0.60, daily_loss_circuit_breaker_pct=0.03)

        for _ in range(5):
            approved = rm.evaluate(_signal(entry=1000.0, stop=970.0))
            self.assertIsNotNone(approved, "expected all 5 trades to be approved")
            rm.on_trade_opened(approved)

        self.assertLessEqual(rm.capital_deployed, rm.capital * 0.60 + 1e-6)

    def test_wide_stop_loss_trade_unaffected_by_per_trade_cap(self):
        # A wide stop (small risk_per_share sizing pressure) naturally sizes
        # a smaller position -- the per-trade cap shouldn't change anything
        # here since the naive risk-based quantity is already well under it.
        rm = RiskManager(capital=100_000, risk_per_trade_pct=0.02, max_open_positions=5,
                          max_deployed_capital_pct=0.60, daily_loss_circuit_breaker_pct=0.03)

        approved = rm.evaluate(_signal(entry=1000.0, stop=800.0))  # 20% stop distance

        self.assertIsNotNone(approved)
        # risk_amount = 100_000 * 0.02 = 2000; risk_per_share = 200 -> qty=10 -> capital=10,000
        self.assertEqual(approved.quantity, 10)
        self.assertEqual(approved.capital_deployed, 10_000.0)

    def test_stock_too_expensive_for_per_trade_cap_is_rejected(self):
        # per-trade cap = 12% of 100_000 = 12,000. A stock priced above that
        # (even 1 share) can't be bought within the per-trade cap at all.
        rm = RiskManager(capital=100_000, risk_per_trade_pct=0.02, max_open_positions=5,
                          max_deployed_capital_pct=0.60, daily_loss_circuit_breaker_pct=0.03)

        approved = rm.evaluate(_signal(entry=15_000.0, stop=14_550.0))  # 3% stop, 1 share = 15,000 > 12,000 cap

        self.assertIsNone(approved)


if __name__ == "__main__":
    unittest.main()
