"""
Tests for deployment/auto_paper_promotion.py -- a research verdict walking itself into paper
trading.

    python test_auto_paper_promotion.py
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deployment.auto_paper_promotion import PROMOTING_VERDICTS, line, paper_runner_for, promote
from deployment.base import DeploymentStatus, ResearchVerdict


def _record(key="alpha", status=DeploymentStatus.RESEARCH, verdict=ResearchVerdict.NOT_YET_EVALUATED):
    return SimpleNamespace(strategy_key=key, display_name=key, deployment_status=status,
                           research_verdict=verdict)


class TestWhichVerdictsGetAPaperBook(unittest.TestCase):
    def test_a_pass_is_promoted(self):
        self.assertIn("PASS", PROMOTING_VERDICTS)

    def test_an_inconclusive_is_promoted_too(self):
        """The criteria could not call it either way, and forward evidence is what settles that --
        which is the whole job of a paper book."""
        self.assertIn("INCONCLUSIVE", PROMOTING_VERDICTS)

    def test_a_reject_is_not(self):
        """The criteria did call this one."""
        self.assertNotIn("REJECT", PROMOTING_VERDICTS)
        with patch("deployment.auto_paper_promotion.paper_runner_for", return_value="run_x.py"):
            r = promote("alpha", "REJECT")
        self.assertFalse(r["promoted"])
        self.assertIn("only", r["reason"])

    def test_a_run_with_no_verdict_at_all_is_not(self):
        r = promote("alpha", "")
        self.assertFalse(r["promoted"])


class TestItRefusesToPromoteWhatNothingCanRun(unittest.TestCase):
    """THE SW-008 FAILURE, which deployment/PROMOTION_CHECKLIST.md exists because of: a strategy
    whose status said PAPER_TRADING while no runner had an entry for it did nothing at all, for a
    whole day, with no error anywhere. Each pool's strategy map is CODE -- a factory and its extra
    columns -- and a research PR adds the BACKTEST runner, not that."""

    def test_no_runner_means_no_promotion(self):
        with patch("deployment.auto_paper_promotion.paper_runner_for", return_value=None):
            r = promote("orphan", "PASS")
        self.assertFalse(r["promoted"])
        self.assertIn("no paper-trading runner", r["reason"])

    def test_and_it_says_so_out_loud(self):
        with patch("deployment.auto_paper_promotion.paper_runner_for", return_value=None):
            said = line(promote("orphan", "PASS"))
        self.assertIn("not promoted", said)
        self.assertIn("orphan", said)

    def test_the_runner_lookup_covers_every_paper_pool(self):
        """Read from each pool's own map, so a pool that gains a strategy needs nothing here."""
        from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS
        india = PAPER_TRADING_STRATEGY_SPECS[0].strategy_key
        self.assertIsNotNone(paper_runner_for(india))
        self.assertIsNotNone(paper_runner_for("minervini_trend_template_filter_us"))   # Pool I
        self.assertIsNotNone(paper_runner_for("crypto_tsmom"))                         # Pool E
        self.assertIsNone(paper_runner_for("no_such_strategy_anywhere"))


class TestWhatItDoesWhenItDoesPromote(unittest.TestCase):
    def _run(self, record, verdict="PASS"):
        calls = {}
        with patch("deployment.auto_paper_promotion.paper_runner_for", return_value="run_x.py"), \
             patch("deployment.auto_paper_promotion.get_strategy", side_effect=lambda k: record), \
             patch("deployment.auto_paper_promotion.register_strategy",
                   side_effect=lambda **kw: calls.setdefault("registered", kw)), \
             patch("deployment.auto_paper_promotion.set_research_verdict",
                   side_effect=lambda k, v, source="": calls.setdefault("verdict", (k, v, source))), \
             patch("deployment.auto_paper_promotion.set_deployment_status",
                   side_effect=lambda k, s, reason="": calls.setdefault("status", (k, s, reason))):
            calls["result"] = promote("alpha", verdict, display_name="Alpha", experiment_id="EXP-096")
        return calls

    def test_it_records_the_verdict_and_starts_paper_trading(self):
        calls = self._run(_record())
        self.assertTrue(calls["result"]["promoted"])
        self.assertEqual(calls["verdict"][1], ResearchVerdict.PASS)
        self.assertEqual(calls["status"][1], DeploymentStatus.PAPER_TRADING)
        self.assertIn("EXP-096", calls["status"][2])

    def test_it_registers_a_strategy_that_has_no_record_yet(self):
        """A research candidate has never been in the registry -- the PR that implements it adds
        an experiment runner, not a deployment record."""
        calls = {}
        with patch("deployment.auto_paper_promotion.paper_runner_for", return_value="run_x.py"), \
             patch("deployment.auto_paper_promotion.get_strategy",
                   side_effect=[None, _record(), _record()]), \
             patch("deployment.auto_paper_promotion.register_strategy",
                   side_effect=lambda **kw: calls.setdefault("registered", kw)), \
             patch("deployment.auto_paper_promotion.set_research_verdict"), \
             patch("deployment.auto_paper_promotion.set_deployment_status"):
            promote("alpha", "PASS", display_name="Alpha")
        self.assertEqual(calls["registered"]["strategy_key"], "alpha")
        self.assertEqual(calls["registered"]["display_name"], "Alpha")

    def test_an_inconclusive_gets_its_own_verdict_recorded(self):
        calls = self._run(_record(), verdict="INCONCLUSIVE")
        self.assertEqual(calls["verdict"][1], ResearchVerdict.INCONCLUSIVE)
        self.assertEqual(calls["status"][1], DeploymentStatus.PAPER_TRADING)

    def test_a_strategy_already_paper_trading_is_left_alone(self):
        calls = self._run(_record(status=DeploymentStatus.PAPER_TRADING, verdict=ResearchVerdict.PASS))
        self.assertTrue(calls["result"]["promoted"])
        self.assertNotIn("status", calls)        # no pointless transition
        self.assertIn("already paper trading", calls["result"]["reason"])

    def test_it_never_touches_live(self):
        """Promotion to live keeps every one of its gates and stays a human decision."""
        import deployment.auto_paper_promotion as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = " ".join(l.split("#")[0] for l in f.read().splitlines())
        for forbidden in ("PILOT_LIVE", "PRODUCTION", "set_allocation", "place_order"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_it_seeds_no_money(self):
        """A fresh book is created by its own runner at PAPER_TRADING_WINDDOWN_TARGET_CAPITAL --
        Rs1,00,000, or that pool's currency equivalent. Writing capital here would be a second
        answer to a question that already has one."""
        import deployment.auto_paper_promotion as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = " ".join(l.split("#")[0] for l in f.read().splitlines())
        for forbidden in ("starting_capital", "json.dump", "open(", "100000", "100_000"):
            self.assertNotIn(forbidden, code, forbidden)


class TestAPromotionProblemNeverCostsAVerdict(unittest.TestCase):
    def test_a_raising_registry_is_reported_not_raised(self):
        with patch("deployment.auto_paper_promotion.paper_runner_for", return_value="run_x.py"), \
             patch("deployment.auto_paper_promotion.get_strategy", side_effect=RuntimeError("boom")):
            r = promote("alpha", "PASS")
        self.assertFalse(r["promoted"])
        self.assertIn("could not promote", r["reason"])

    def test_the_backtest_job_reports_it_in_the_same_message_as_the_verdict(self):
        import run_queued_backtest as q
        with open(q.__file__, encoding="utf-8") as f:
            code = f.read()
        self.assertIn("from deployment.auto_paper_promotion import line as promo_line, promote", code)
        self.assertIn("promo_line(promo)", code)
        # and only AFTER the queue has recorded the verdict
        self.assertLess(code.index("research_queue.resolve("), code.index("promote(key, verdict"))


if __name__ == "__main__":
    unittest.main()
