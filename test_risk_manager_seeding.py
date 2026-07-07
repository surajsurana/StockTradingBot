"""
Mock-based unit tests for RiskManager.seed_existing_positions -- the fix for
the cross-day position-count/capital gap (a fresh RiskManager used to start
at zero every run, regardless of what was already held). Run with:

    python test_risk_manager_seeding.py
"""

import unittest

from risk.risk_manager import RiskManager
from execution.positions import Holding
from strategies.base import Signal


def _signal(entry=100.0, stop=95.0):
    return Signal(symbol="TCS.NS", direction="BUY", entry_price=entry, stop_loss=stop,
                   target=110.0, confidence=0.8, strategy_name="test", reason="test signal")


class TestSeedExistingPositions(unittest.TestCase):
    def test_seeds_count_and_deployed_capital(self):
        rm = RiskManager(capital=100000, risk_per_trade_pct=0.01, max_open_positions=5,
                          max_deployed_capital_pct=0.5, daily_loss_circuit_breaker_pct=0.03)
        holdings = [
            Holding(symbol="INFY.NS", quantity=10, average_price=1500.0),
            Holding(symbol="TCS.NS", quantity=5, average_price=3200.0),
        ]
        rm.seed_existing_positions(holdings)

        self.assertEqual(rm.open_positions_count, 2)
        self.assertEqual(rm.capital_deployed, 10 * 1500.0 + 5 * 3200.0)

    def test_empty_holdings_leaves_state_at_zero(self):
        rm = RiskManager(capital=100000, risk_per_trade_pct=0.01, max_open_positions=5,
                          max_deployed_capital_pct=0.5, daily_loss_circuit_breaker_pct=0.03)
        rm.seed_existing_positions([])
        self.assertEqual(rm.open_positions_count, 0)
        self.assertEqual(rm.capital_deployed, 0.0)

    def test_seeded_count_blocks_new_trades_at_the_limit(self):
        """This is the actual bug being fixed: without seeding, a fresh
        RiskManager would allow MAX_OPEN_POSITIONS new trades even though
        that many (or more) are already open from previous days."""
        rm = RiskManager(capital=100000, risk_per_trade_pct=0.01, max_open_positions=2,
                          max_deployed_capital_pct=0.9, daily_loss_circuit_breaker_pct=0.03)
        holdings = [
            Holding(symbol="INFY.NS", quantity=10, average_price=1500.0),
            Holding(symbol="TCS.NS", quantity=5, average_price=3200.0),
        ]
        rm.seed_existing_positions(holdings)

        # Already at max_open_positions=2 from real holdings -- a brand new
        # candidate must be rejected, not sized as if the account were empty.
        self.assertIsNone(rm.evaluate(_signal()))

    def test_seeded_deployed_capital_shrinks_room_for_new_trades(self):
        rm = RiskManager(capital=100000, risk_per_trade_pct=0.01, max_open_positions=10,
                          max_deployed_capital_pct=0.10, daily_loss_circuit_breaker_pct=0.03)
        # Already 9% of capital deployed via real holdings; max allowed is 10%.
        rm.seed_existing_positions([Holding(symbol="INFY.NS", quantity=6, average_price=1500.0)])

        approved = rm.evaluate(_signal(entry=100.0, stop=95.0))
        # Room left is only 1% of capital (~Rs.1000) -- quantity should be
        # shrunk to fit, not sized as if the full 10% were still available.
        self.assertIsNotNone(approved)
        self.assertLessEqual(rm.capital_deployed, rm.capital * 0.10 + 1e-6)


if __name__ == "__main__":
    unittest.main()
