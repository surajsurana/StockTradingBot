"""
Mock-based unit tests for portfolio_manager.py's rejection-reason logic --
covers a real bug hit on the first live run at low capital: a single
candidate too expensive to size at all (TIMKEN.NS at Rs.5,470 capital) was
misreported as "capital already allocated to higher-confidence trades",
even though nothing had been approved yet. Run with:

    python test_portfolio_manager_rejection_reasons.py
"""

import unittest

from portfolio.portfolio_manager import allocate
from research.research_analyst import ResearchAssessment
from risk.risk_manager import RiskManager
from strategies.base import Signal
from portfolio.portfolio_manager import TradeCandidate


def _risk_manager(capital=100000, max_positions=5, max_deployed_pct=0.5):
    return RiskManager(capital=capital, risk_per_trade_pct=0.01, max_open_positions=max_positions,
                        max_deployed_capital_pct=max_deployed_pct, daily_loss_circuit_breaker_pct=0.03)


def _candidate(symbol, entry, stop, confidence=0.65):
    signal = Signal(symbol=symbol, direction="BUY", entry_price=entry, stop_loss=stop,
                     target=entry * 1.1, confidence=confidence, strategy_name="test", reason="test")
    assessment = ResearchAssessment(symbol=symbol, verdict="favorable", confidence=confidence,
                                     reasoning="test")
    return TradeCandidate(symbol=symbol, signal=signal, research_assessment=assessment)


class TestTooExpensiveForRiskBudget(unittest.TestCase):
    def test_single_expensive_candidate_gets_the_real_reason_not_capital_already_spent(self):
        """Reproduces today's real run: Rs.5,470.30 capital, TIMKEN.NS at
        Rs.3,239.50 entry / Rs.97.18 stop distance, 65% confidence -- risk
        budget (~Rs.43.76) can't buy even 1 share within the stop distance.
        This is the ONLY candidate, so nothing was approved before it."""
        risk_manager = _risk_manager(capital=5470.30)
        candidate = _candidate("TIMKEN.NS", entry=3239.5, stop=3142.315, confidence=0.65)

        decisions = allocate([candidate], risk_manager)

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertFalse(decision.approved)
        self.assertNotIn("already approved today", decision.reason)
        self.assertIn("too expensive for the available capital", decision.reason)


class TestGenuineCapitalExhaustion(unittest.TestCase):
    def test_second_candidate_rejected_because_first_used_the_budget(self):
        """STRONG.NS is sized first (higher confidence) and consumes most of
        the 30% deployed-capital cap, leaving too little budget for even 1
        share of WEAK.NS -- this is the genuine capital-conflict case and
        must still report the "already approved today" message correctly."""
        risk_manager = _risk_manager(capital=100000, max_deployed_pct=0.3)
        strong = _candidate("STRONG.NS", entry=1000, stop=950, confidence=0.95)
        weak = _candidate("WEAK.NS", entry=5000, stop=4750, confidence=0.55)

        decisions = allocate([strong, weak], risk_manager)

        approved = {d.symbol: d for d in decisions if d.approved}
        rejected = {d.symbol: d for d in decisions if not d.approved}
        self.assertIn("STRONG.NS", approved)
        self.assertIn("WEAK.NS", rejected)
        self.assertIn("already approved today", rejected["WEAK.NS"].reason)


class TestOtherRejectionReasons(unittest.TestCase):
    def test_daily_loss_circuit_breaker(self):
        risk_manager = _risk_manager(capital=100000)
        risk_manager.realized_pnl_today = -5000  # breaches the 3% circuit breaker
        candidate = _candidate("ANY.NS", entry=100, stop=95)

        decisions = allocate([candidate], risk_manager)

        self.assertIn("circuit breaker", decisions[0].reason)

    def test_max_open_positions(self):
        risk_manager = _risk_manager(capital=100000, max_positions=1)
        risk_manager.open_positions_count = 1  # already at the cap
        candidate = _candidate("ANY.NS", entry=100, stop=95)

        decisions = allocate([candidate], risk_manager)

        self.assertIn("maximum number of open positions", decisions[0].reason)


if __name__ == "__main__":
    unittest.main()
