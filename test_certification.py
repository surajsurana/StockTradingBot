"""
Unit tests for deployment/certification.py -- the walk-forward wrapper
around simulate_portfolio_single_unit() and the certification experiment
save path. Uses a tiny synthetic universe and mocked
simulate_portfolio_single_unit()/audit() calls to stay fast and
deterministic (a full MA Crossover backtest isn't needed to verify the
wiring). Run with:

    python test_certification.py
"""

import datetime
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from deployment.certification import run_certification_experiment, run_walk_forward_single_unit
from research_lab.experiment_manager import load_experiment


class _FakeVerdict:
    def __init__(self, decision, reasoning, checks):
        self.decision = decision
        self.reasoning = reasoning
        self.checks = checks


class TestRunWalkForwardSingleUnit(unittest.TestCase):
    def test_calls_simulate_and_audit_with_expected_shapes(self):
        fake_trades_result = {"trades": [], "trading_calendar": [], "daily_equity": {}, "starting_capital": 1000}
        fake_verdict = _FakeVerdict("PASS", "looks fine", {"total_trades": 50, "out_of_sample_trades": 10})

        with patch("deployment.certification.simulate_portfolio_single_unit",
                   return_value=fake_trades_result) as mock_sim, \
             patch("deployment.certification.statistical_auditor.audit", return_value=fake_verdict) as mock_audit:
            result = run_walk_forward_single_unit(
                strategy_factory=lambda: object(), data={"SYM": _empty_df()},
                starting_capital=1000, start_date=datetime.date(2020, 1, 1),
                end_date=datetime.date(2023, 1, 1), n_walk_forward_windows=3,
            )
            self.assertEqual(mock_sim.call_count, 3)   # once per window
            mock_audit.assert_called_once()
            self.assertEqual(result["verdict"].decision, "PASS")


def _empty_df():
    import pandas as pd
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    return pd.DataFrame({"Open": [1] * 5, "High": [1] * 5, "Low": [1] * 5, "Close": [1] * 5,
                          "Volume": [1] * 5}, index=idx)


class TestRunCertificationExperiment(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.experiments_dir = os.path.join(self.tmpdir, "experiments")
        self.kb_path = os.path.join(self.tmpdir, "kb.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_saves_experiment_with_evidence_quality_and_never_touches_deployment(self):
        fake_trades_result = {"trades": [], "trading_calendar": [], "daily_equity": {}, "starting_capital": 1000}
        fake_verdict = _FakeVerdict("PASS", "looks fine", {"total_trades": 50, "out_of_sample_trades": 10})

        with patch("deployment.certification.simulate_portfolio_single_unit",
                   return_value=fake_trades_result), \
             patch("deployment.certification.statistical_auditor.audit", return_value=fake_verdict), \
             patch("deployment.certification.CERTIFICATION_KNOWLEDGE_BASE_PATH", self.kb_path):
            result = run_certification_experiment(
                strategy_key="ma_crossover", display_name="MA Crossover", strategy_factory=lambda: object(),
                data={"SYM": _empty_df()}, start_date=datetime.date(2020, 1, 1),
                end_date=datetime.date(2023, 1, 1), starting_capital=1000, n_walk_forward_windows=3,
                min_lookback_days=50, experiments_dir=self.experiments_dir,
            )

        self.assertEqual(result["verdict"].decision, "PASS")
        self.assertIn("score", result["evidence_quality"])
        self.assertIn("label", result["evidence_quality"])

        loaded = load_experiment(result["exp_id"], self.experiments_dir)
        self.assertIn("PASS", loaded["verdict"])
        self.assertIn("Production Strategy Certification", loaded["hypothesis"])
        self.assertIn("evidence_quality", loaded["metrics"])

    def test_does_not_import_deployment_manager(self):
        import deployment.certification as cert
        self.assertNotIn("deployment_manager", dir(cert))


if __name__ == "__main__":
    unittest.main()
