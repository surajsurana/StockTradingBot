"""
Tests for deployment/retirement.py -- taking a strategy out of paper trading.

    python test_retirement.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.retirement import MIN_PAPER_DAYS_BEFORE_RETIRING, RULE_A, RULE_B, candidates, retire


def _record(key="alpha", sid="SW-999", status=DeploymentStatus.PAPER_TRADING,
            verdict=ResearchVerdict.PASS):
    return StrategyRecord(strategy_key=key, display_name=key, strategy_family="fam",
                          strategy_id=sid, research_verdict=verdict, deployment_status=status)


def _book(root, key, trades, pnl_each, first="2026-01-05"):
    d = os.path.join(root, "paper_trading", key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "trades.jsonl"), "w", encoding="utf-8") as f:
        for _ in range(trades):
            f.write(json.dumps({"entry_date": first, "pnl": pnl_each}) + "\n")
    return root


class TestRuleA(unittest.TestCase):
    """Paper, 120 days, the gate says not qualified, and it has not made money."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.now = datetime(2026, 6, 1)          # ~147 days after the book's first trade

    def _find(self, record):
        return candidates(self.root, records=[record], now=self.now)

    def test_a_long_losing_book_is_retired(self):
        _book(self.root, "alpha", 30, -50.0)
        found = self._find(_record())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["rule"], RULE_A)
        self.assertLess(found[0]["pnl"], 0)
        self.assertIn("not qualified", found[0]["reason"])

    def test_a_profitable_book_is_left_alone(self):
        """However old it is. Making money is the whole point."""
        _book(self.root, "alpha", 30, 50.0)
        self.assertEqual(self._find(_record()), [])

    def test_a_young_losing_book_is_left_alone(self):
        """THE POINT OF THE 120 DAYS. A bad fortnight is not a verdict, and auto-promotion adds new
        strategies every week -- retiring on a short record would kill them on arrival."""
        _book(self.root, "alpha", 30, -50.0, first="2026-05-20")      # 12 days old
        self.assertEqual(self._find(_record()), [])

    def test_the_floor_is_double_the_live_gates_own(self):
        from deployment.pilot_live import MIN_PAPER_TRADING_DAYS_ELAPSED
        self.assertEqual(MIN_PAPER_DAYS_BEFORE_RETIRING, 2 * MIN_PAPER_TRADING_DAYS_ELAPSED)

    def test_a_book_with_too_few_trades_is_not_judged(self):
        """Old but barely traded: the gate reads "waiting", not "not qualified", so nothing is
        decided on three trades."""
        _book(self.root, "alpha", 3, -50.0)
        self.assertEqual(self._find(_record()), [])


class TestLiveIsNeverTouched(unittest.TestCase):
    """Real money moves when a person says so, whatever the numbers say."""

    def test_neither_live_status_is_ever_a_candidate(self):
        root = tempfile.mkdtemp()
        _book(root, "alpha", 40, -100.0)
        for status in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
            with self.subTest(status=status):
                found = candidates(root, records=[_record(status=status)], now=datetime(2026, 6, 1))
                self.assertEqual(found, [])

    def test_it_sells_nothing_and_places_nothing(self):
        """An archived book is frozen exactly as it stands, open positions included. Checked against
        the CODE rather than the whole file -- the module's own prose says the word "sells"."""
        import ast
        import deployment.retirement as mod
        with open(mod.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr)
                                                  and isinstance(n.value, ast.Constant))]
        code = ast.dump(tree)
        for forbidden in ("place_order", "square_off", "close_position", "append_trade"):
            self.assertNotIn(forbidden, code, forbidden)


class TestRuleB(unittest.TestCase):
    """Not trading at all, and the research verdict is REJECT -- housekeeping."""

    def test_a_rejected_candidate_that_never_deployed_is_archived(self):
        found = candidates(tempfile.mkdtemp(),
                           records=[_record(status=DeploymentStatus.RESEARCH,
                                            verdict=ResearchVerdict.REJECT)],
                           now=datetime(2026, 6, 1))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["rule"], RULE_B)
        self.assertIn("nothing was running to stop", found[0]["reason"])

    def test_a_rejected_candidate_still_paper_trading_goes_through_rule_a_instead(self):
        """It IS running, so it is judged on its forward record, not on the old verdict."""
        root = tempfile.mkdtemp()
        _book(root, "alpha", 30, -50.0, first="2026-05-20")      # young: Rule A does not fire yet
        found = candidates(root, records=[_record(verdict=ResearchVerdict.REJECT)],
                           now=datetime(2026, 6, 1))
        self.assertEqual(found, [])

    def test_an_unevaluated_candidate_that_never_deployed_is_left_alone(self):
        """Only a REJECT is housekeeping. Nothing has been decided about the others."""
        for verdict in (ResearchVerdict.PASS, ResearchVerdict.INCONCLUSIVE,
                        ResearchVerdict.NOT_YET_EVALUATED):
            with self.subTest(verdict=verdict):
                found = candidates(tempfile.mkdtemp(),
                                   records=[_record(status=DeploymentStatus.RESEARCH, verdict=verdict)],
                                   now=datetime(2026, 6, 1))
                self.assertEqual(found, [])

    def test_something_already_archived_is_not_archived_again(self):
        found = candidates(tempfile.mkdtemp(),
                           records=[_record(status=DeploymentStatus.ARCHIVED,
                                            verdict=ResearchVerdict.REJECT)],
                           now=datetime(2026, 6, 1))
        self.assertEqual(found, [])


class TestRetiring(unittest.TestCase):
    def test_it_archives_and_writes_down_why(self):
        calls = {}
        with patch("deployment.deployment_manager.set_deployment_status",
                   side_effect=lambda k, s, reason="": calls.setdefault("c", (k, s, reason))):
            r = retire({"key": "alpha", "sid": "SW-999", "name": "Alpha", "rule": RULE_A,
                        "reason": "147 days, P&L -1,500 before charges"})
        self.assertTrue(r["retired"])
        self.assertEqual(calls["c"][1], DeploymentStatus.ARCHIVED)
        self.assertIn("Retired automatically", calls["c"][2])
        self.assertIn("147 days", calls["c"][2])

    def test_one_failure_does_not_stop_the_rest(self):
        with patch("deployment.deployment_manager.set_deployment_status",
                   side_effect=RuntimeError("boom")):
            r = retire({"key": "alpha", "rule": RULE_A, "reason": "x"})
        self.assertFalse(r["retired"])
        self.assertIn("RuntimeError", r["error"])


if __name__ == "__main__":
    unittest.main()
