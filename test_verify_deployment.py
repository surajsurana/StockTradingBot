"""
Tests for verify_deployment.py -- the automated PAPER_TRADING promotion
checker (deployment/PROMOTION_CHECKLIST.md, items 2-11). Mocks every
underlying data source so these tests don't depend on real registry/git/
filesystem state.
"""

import unittest
from unittest.mock import MagicMock, mock_open, patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
import verify_deployment as vd


def _fake_record(status=DeploymentStatus.PAPER_TRADING, verdict=ResearchVerdict.PASS):
    return StrategyRecord(
        strategy_key="fake_strategy", display_name="Fake Strategy", strategy_family="swing_research published strategy",
        research_verdict=verdict, research_verdict_source="EXP-000",
        deployment_status=status, deployment_status_history=[],
        strategy_id="SW-000", primary_experiment_id="EXP-000",
    )


class TestIndividualChecks(unittest.TestCase):
    def test_registry_check_passes_for_pass_and_paper_trading(self):
        with patch("verify_deployment.get_strategy", return_value=_fake_record()):
            passed, detail = vd._check_registry("fake_strategy")
        self.assertTrue(passed)

    def test_registry_check_fails_when_not_registered(self):
        with patch("verify_deployment.get_strategy", return_value=None):
            passed, detail = vd._check_registry("fake_strategy")
        self.assertFalse(passed)
        self.assertIn("not registered", detail)

    def test_registry_check_fails_on_wrong_verdict_or_status(self):
        with patch("verify_deployment.get_strategy",
                    return_value=_fake_record(status=DeploymentStatus.ARCHIVED, verdict=ResearchVerdict.REJECT)):
            passed, detail = vd._check_registry("fake_strategy")
        self.assertFalse(passed)
        self.assertIn("REJECT", detail)
        self.assertIn("ARCHIVED", detail)

    def test_strategy_file_check(self):
        with patch("os.path.isfile", return_value=True):
            passed, _ = vd._check_strategy_file_exists("fake_strategy")
        self.assertTrue(passed)
        with patch("os.path.isfile", return_value=False):
            passed, _ = vd._check_strategy_file_exists("fake_strategy")
        self.assertFalse(passed)

    def test_factory_check_fails_when_key_missing(self):
        fake_module = MagicMock()
        fake_module._STRATEGY_FACTORIES = {}
        with patch("importlib.import_module", return_value=fake_module):
            passed, detail = vd._check_factory("fake_strategy")
        self.assertFalse(passed)
        self.assertIn("_STRATEGY_FACTORIES", detail)

    def test_factory_check_passes_and_constructs(self):
        fake_module = MagicMock()
        fake_module._STRATEGY_FACTORIES = {"fake_strategy": {"strategy_factory": lambda: object()}}
        with patch("importlib.import_module", return_value=fake_module):
            passed, _ = vd._check_factory("fake_strategy")
        self.assertTrue(passed)

    def test_factory_check_fails_when_construction_raises(self):
        def broken():
            raise RuntimeError("bad import")
        fake_module = MagicMock()
        fake_module._STRATEGY_FACTORIES = {"fake_strategy": {"strategy_factory": broken}}
        with patch("importlib.import_module", return_value=fake_module):
            passed, detail = vd._check_factory("fake_strategy")
        self.assertFalse(passed)
        self.assertIn("RuntimeError", detail)

    def test_scheduler_check_reports_due_result(self):
        with patch("verify_deployment.get_strategy", return_value=_fake_record()), \
             patch("deployment.scheduler.is_due_now", return_value=(True, "market closed")):
            passed, detail = vd._check_scheduler("fake_strategy")
        self.assertTrue(passed)
        self.assertIn("due=True", detail)

    def test_scheduler_check_fails_when_not_registered(self):
        with patch("verify_deployment.get_strategy", return_value=None):
            passed, detail = vd._check_scheduler("fake_strategy")
        self.assertFalse(passed)

    def test_first_run_check_fails_when_no_portfolio_file(self):
        with patch("os.path.isfile", return_value=False):
            passed, detail = vd._check_first_run("fake_strategy")
        self.assertFalse(passed)
        self.assertIn("no paper-trading run", detail)

    def test_first_run_check_passes_with_last_processed_date(self):
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data='{"last_processed_date": "2026-08-06"}')):
            passed, detail = vd._check_first_run("fake_strategy")
        self.assertTrue(passed)
        self.assertIn("2026-08-06", detail)

    def test_telegram_check(self):
        with patch("verify_deployment.TELEGRAM_BOT_TOKEN", "abc"), patch("verify_deployment.TELEGRAM_CHAT_ID", "123"):
            passed, _ = vd._check_telegram_configured()
        self.assertTrue(passed)
        with patch("verify_deployment.TELEGRAM_BOT_TOKEN", ""), patch("verify_deployment.TELEGRAM_CHAT_ID", "123"):
            passed, _ = vd._check_telegram_configured()
        self.assertFalse(passed)

    def test_report_check(self):
        with patch("os.path.isfile", return_value=True):
            passed, _ = vd._check_report("fake_strategy")
        self.assertTrue(passed)
        with patch("os.path.isfile", return_value=False):
            passed, _ = vd._check_report("fake_strategy")
        self.assertFalse(passed)


class TestVerifyOverallResult(unittest.TestCase):
    def test_all_checks_passing_returns_true(self):
        with patch.object(vd, "CHECKS", [("A", lambda key: (True, "ok")), ("B", lambda key: (True, "ok"))]):
            self.assertTrue(vd.verify("fake_strategy"))

    def test_one_check_failing_returns_false(self):
        with patch.object(vd, "CHECKS", [("A", lambda key: (True, "ok")), ("B", lambda key: (False, "broken"))]):
            self.assertFalse(vd.verify("fake_strategy"))

    def test_a_check_raising_counts_as_a_failure_not_a_crash(self):
        def _raises(key):
            raise RuntimeError("boom")
        with patch.object(vd, "CHECKS", [("A", _raises)]):
            self.assertFalse(vd.verify("fake_strategy"))   # must not propagate


if __name__ == "__main__":
    unittest.main()
