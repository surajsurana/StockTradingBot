"""
deployment/status_overlay.py -- deployment status, kept where a deploy cannot overwrite it.

THE BUG THIS EXISTS TO FIX cost a real promotion, more than once, silently.
deployment/state/strategy_registry.json is tracked by git on purpose: registering a strategy and
recording a verdict are repo facts, and the research routines commit them. But the same file also
held `deployment_status`, which is not a repo fact -- it is what this machine is doing. Every deploy
ran `git reset --hard origin/main`, resetting the file and reverting every promotion made from the
dashboard, with nothing in any log to say why.
"""
import json
import os
import tempfile
import unittest

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.status_overlay import apply_to, load, overlay_path, record_status


def _registry(status=DeploymentStatus.PAPER_TRADING):
    return {"portfolio_g": StrategyRecord(
        strategy_key="portfolio_g", display_name="Pool G", strategy_family="AI judgment book (crypto)",
        research_verdict=ResearchVerdict.NOT_YET_EVALUATED, deployment_status=status,
        deployment_status_history=[{"from_status": "RESEARCH", "to_status": "PAPER_TRADING",
                                    "timestamp": 1.0, "reason": "seeded"}])}


class TestItSurvivesWhatADeployDoes(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_recorded_promotion_overrides_the_registry_on_read(self):
        record_status(self.d, "portfolio_g", "PAPER_TRADING", "PILOT_LIVE", "promoted")
        registry = apply_to(_registry(), self.d)
        self.assertEqual(registry["portfolio_g"].deployment_status, DeploymentStatus.PILOT_LIVE)

    def test_resetting_the_registry_file_does_not_revert_it(self):
        # this is literally what `git reset --hard` does to the tracked registry
        record_status(self.d, "portfolio_g", "PAPER_TRADING", "PILOT_LIVE", "promoted")
        fresh_from_git = _registry(DeploymentStatus.PAPER_TRADING)
        self.assertEqual(apply_to(fresh_from_git, self.d)["portfolio_g"].deployment_status,
                         DeploymentStatus.PILOT_LIVE)

    def test_the_override_marker_survives_too(self):
        # the per-order guard reads [MANUAL OVERRIDE] out of the history to decide whether to honour
        # a promotion past the gates; a status that survived without its reason would break that
        record_status(self.d, "portfolio_g", "PAPER_TRADING", "PILOT_LIVE",
                      "[MANUAL OVERRIDE] promoted in spite of the gates")
        registry = apply_to(_registry(), self.d)
        history = registry["portfolio_g"].deployment_status_history
        self.assertIn("[MANUAL OVERRIDE]", history[-1]["reason"])

        from deployment.pilot_live import promotion_override
        self.assertIsNotNone(promotion_override(registry["portfolio_g"]))

    def test_history_accumulates_across_calls(self):
        record_status(self.d, "portfolio_g", "PAPER_TRADING", "PILOT_LIVE", "up")
        record_status(self.d, "portfolio_g", "PILOT_LIVE", "PAPER_TRADING", "back down")
        entry = load(self.d)["portfolio_g"]
        self.assertEqual(entry["status"], "PAPER_TRADING")
        self.assertEqual([h["to_status"] for h in entry["history"]], ["PILOT_LIVE", "PAPER_TRADING"])


class TestItFailsSafely(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_no_overlay_leaves_the_registry_exactly_as_it_was(self):
        # nothing had to be migrated, and a fresh checkout still works
        registry = apply_to(_registry(), self.d)
        self.assertEqual(registry["portfolio_g"].deployment_status, DeploymentStatus.PAPER_TRADING)

    def test_a_corrupt_overlay_is_ignored_rather_than_raising(self):
        with open(overlay_path(self.d), "w", encoding="utf-8") as f:
            f.write("{ truncated")
        self.assertEqual(load(self.d), {})
        self.assertEqual(apply_to(_registry(), self.d)["portfolio_g"].deployment_status,
                         DeploymentStatus.PAPER_TRADING)

    def test_an_unknown_status_leaves_the_strategy_where_the_registry_had_it(self):
        # a typo or a value from a newer version must not land a strategy somewhere it was never put
        with open(overlay_path(self.d), "w", encoding="utf-8") as f:
            json.dump({"portfolio_g": {"status": "going_bananas", "history": []}}, f)
        self.assertEqual(apply_to(_registry(), self.d)["portfolio_g"].deployment_status,
                         DeploymentStatus.PAPER_TRADING)

    def test_an_entry_for_a_strategy_that_no_longer_exists_is_skipped(self):
        record_status(self.d, "deleted_strategy", "PAPER_TRADING", "PILOT_LIVE", "x")
        apply_to(_registry(), self.d)          # must not raise


class TestEndToEndThroughTheManager(unittest.TestCase):
    def test_set_deployment_status_writes_the_overlay_not_just_the_registry(self):
        from deployment.deployment_manager import _load_registry, set_deployment_status
        d = tempfile.mkdtemp()
        path = os.path.join(d, "strategy_registry.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in _registry().items()}, f)

        set_deployment_status("portfolio_g", DeploymentStatus.PILOT_LIVE,
                              reason="promoted from the dashboard", registry_path=path)
        self.assertEqual(load(d)["portfolio_g"]["status"], "PILOT_LIVE")

        # now simulate the deploy: put the tracked file back as git has it
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in _registry().items()}, f)
        reloaded = _load_registry(path)
        self.assertEqual(reloaded["portfolio_g"].deployment_status, DeploymentStatus.PILOT_LIVE)


if __name__ == "__main__":
    unittest.main()
