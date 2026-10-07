"""
Unit tests for per-pool promotion -- WHICH of a strategy's two books goes live.

18 strategies run in two pools at once (Pool A and Pool F, or Pool E and E1) and those are different
methods, not copies: Pool F books half the position at +5% and moves the stop to entry. Their results
differ accordingly -- ma_pullback made -Rs9,845 in Pool A and -Rs5,115 in Pool F.

Until 2026-10-07 one deployment_status per strategy could not say which, so the runner silently
resolved every promotion to Pool A. For ma_pullback that took the WORSE book live, chosen by nobody
and recorded nowhere -- and it broke ARCHITECTURE.md's paper-to-live rule, because a live book whose
variant the system picked has no identifiable paper control.

    python test_per_pool_promotion.py
"""

import os
import tempfile
import unittest

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord


class TestTheRunnerHonoursTheChosenPool(unittest.TestCase):
    def test_pool_f_runs_pool_fs_own_method_not_pool_as(self):
        """The thing that makes Pool F Pool F is partial booking. Running its folder without it would
        be Pool A's method against Pool F's book -- the wrong answer, wearing the right name."""
        import run_pool_live as rpl
        plan = rpl._engine_plan("ma_pullback", "F")
        self.assertEqual(plan["dirname"], "pool_f")
        self.assertIsNotNone(plan["partial_booking"])
        self.assertEqual(plan["partial_booking"].trigger_pct, 0.05)
        self.assertTrue(plan["partial_booking"].move_stop_to_entry)

    def test_pool_a_runs_without_partial_booking(self):
        import run_pool_live as rpl
        plan = rpl._engine_plan("ma_pullback", "A")
        self.assertEqual(plan["dirname"], "paper_trading")
        self.assertIsNone(plan["partial_booking"])

    def test_no_choice_still_means_pool_a_so_nothing_already_live_moves(self):
        import run_pool_live as rpl
        self.assertEqual(rpl._engine_plan("ma_pullback", "")["dirname"], "paper_trading")
        self.assertEqual(rpl._engine_plan("ma_pullback")["dirname"], "paper_trading")

    def test_an_unknown_pool_falls_back_rather_than_inventing_a_folder(self):
        import run_pool_live as rpl
        self.assertEqual(rpl._engine_plan("ma_pullback", "Z")["dirname"], "paper_trading")

    def test_the_pool_name_is_read_case_and_space_tolerantly(self):
        import run_pool_live as rpl
        for spelling in ("f", " F ", "f "):
            self.assertEqual(rpl._engine_plan("ma_pullback", spelling)["dirname"], "pool_f", spelling)

    def test_a_crypto_strategy_is_untouched_by_this(self):
        import run_pool_live as rpl
        plan = rpl._engine_plan("crypto_tsmom", "F")      # Pool E/E1 is not an A/F pair
        self.assertEqual(plan["dirname"], "pool_e")
        self.assertIsNone(plan["partial_booking"])

    def test_the_runner_reads_the_choice_off_the_record(self):
        import inspect

        import run_pool_live as rpl
        source = inspect.getsource(rpl.run_live)
        self.assertIn('_engine_plan(strategy_key, getattr(record, "live_pool", ""))', source)

    def test_partial_booking_reaches_the_engine(self):
        import inspect

        import run_pool_live as rpl
        source = inspect.getsource(rpl.run_live)
        self.assertIn('engine_kwargs["partial_booking"] = plan["partial_booking"]', source)


class TestTheChoiceSurvivesADeploy(unittest.TestCase):
    """live_pool lives in the untracked overlay beside deployment_status, for the same reason: it is
    what the machine is doing, not a fact about the research, and `git reset --hard` must not be able
    to revert it. That bug already cost real promotions once."""

    def test_it_is_written_to_and_read_from_the_overlay(self):
        from deployment.status_overlay import apply_to, load, record_status
        d = tempfile.mkdtemp()
        record_status(d, "ma_pullback", "PAPER_TRADING", "PILOT_LIVE", "promoted", live_pool="F")
        self.assertEqual(load(d)["ma_pullback"]["live_pool"], "F")

        record = StrategyRecord(strategy_key="ma_pullback", display_name="MA Pullback",
                                strategy_family="swing_research published strategy",
                                research_verdict=ResearchVerdict.REJECT,
                                deployment_status=DeploymentStatus.PAPER_TRADING)
        apply_to({"ma_pullback": record}, d)
        self.assertEqual(record.deployment_status, DeploymentStatus.PILOT_LIVE)
        self.assertEqual(record.live_pool, "F")

    def test_a_later_status_change_does_not_silently_clear_the_pool(self):
        """Demoting and re-promoting must not quietly forget which book was chosen -- that would put
        the strategy back to the silent default without saying so."""
        from deployment.status_overlay import load, record_status
        d = tempfile.mkdtemp()
        record_status(d, "ma_pullback", "PAPER_TRADING", "PILOT_LIVE", "promoted", live_pool="F")
        record_status(d, "ma_pullback", "PILOT_LIVE", "PAPER_TRADING", "demoted")   # no pool given
        self.assertEqual(load(d)["ma_pullback"]["live_pool"], "F")

    def test_it_can_be_changed_deliberately(self):
        from deployment.status_overlay import load, record_status
        d = tempfile.mkdtemp()
        record_status(d, "k", "PAPER_TRADING", "PILOT_LIVE", "promoted", live_pool="F")
        record_status(d, "k", "PILOT_LIVE", "PILOT_LIVE", "switched", live_pool="A")
        self.assertEqual(load(d)["k"]["live_pool"], "A")

    def test_a_record_with_no_overlay_entry_has_no_pool(self):
        self.assertEqual(StrategyRecord(strategy_key="k", display_name="n",
                                        strategy_family="f").live_pool, "")


class TestTheEndpointValidatesThePool(unittest.TestCase):
    def test_only_pools_the_runner_knows_are_accepted(self):
        """A pool name the runner cannot resolve would promote the strategy and then fall back to the
        default -- the silent wrong answer this change exists to remove."""
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        block = source[source.index('if parsed.path == "/api/live/promote":'):]
        block = block[:block.index('if parsed.path == "/api/research/start":')]
        self.assertIn("from run_pool_live import POOL_VARIANTS", block)
        self.assertIn("if pool and pool not in POOL_VARIANTS:", block)

    def test_the_chosen_pool_is_written_into_the_audit_trail(self):
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        block = source[source.index('if parsed.path == "/api/live/promote":'):]
        block = block[:block.index('if parsed.path == "/api/research/start":')]
        self.assertIn("Pool {pool} is the live book", block)
        self.assertIn("live_pool=pool if target ==", block)


class TestTheRuleIsWrittenDown(unittest.TestCase):
    def test_architecture_says_which_pool_goes_live(self):
        with open("ARCHITECTURE.md", encoding="utf-8") as f:
            doc = f.read()
        self.assertIn("### WHICH pool goes live", doc)
        self.assertIn("live_pool", doc)

    def test_architecture_says_the_two_modes_are_replicas(self):
        with open("ARCHITECTURE.md", encoding="utf-8") as f:
            doc = f.read()
        self.assertIn("Paper and Live are the same product", doc)
        self.assertIn("exact replicas", doc)


if __name__ == "__main__":
    unittest.main()
