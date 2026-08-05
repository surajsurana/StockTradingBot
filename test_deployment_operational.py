"""
Unit tests for the operational additions to the Research Deployment
system (2026-08-04): the shared Telegram formatter (reporting/telegram_templates.py),
the metadata-driven scheduler (deployment/scheduler.py), strategy metadata
round-tripping (deployment/base.py, deployment_manager.py), and the
growing drift-history mechanism (deployment/drift_report.py). No real
Telegram calls are made anywhere in these tests. Run with:

    python test_deployment_operational.py
"""

import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from reporting.telegram_templates import format_strategy_notification
from deployment import deployment_manager
from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.drift_report import compute_drift, generate_drift_report, load_drift_history
from deployment.scheduler import is_due_now, is_market_open, strategies_due_now


class TestTelegramTemplates(unittest.TestCase):
    def test_paper_header_is_test_tube(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test Strategy", new_entries=[], new_exits=[],
            open_positions=[], daily_pnl=0.0, total_equity=100000, drawdown_pct=None,
            win_rate=None, expectancy=None,
        )
        self.assertIn("\U0001F9EA", text)   # test tube emoji
        self.assertIn("Test Strategy", text)

    def test_live_header_is_rocket(self):
        text = format_strategy_notification(
            mode="LIVE", strategy_display_name="Test Strategy", new_entries=[], new_exits=[],
            open_positions=[], daily_pnl=0.0, total_equity=100000, drawdown_pct=None,
            win_rate=None, expectancy=None,
        )
        self.assertIn("\U0001F680", text)   # rocket emoji

    def test_no_signals_message(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[],
            open_positions=[], daily_pnl=0.0, total_equity=100000, drawdown_pct=None,
            win_rate=None, expectancy=None,
        )
        self.assertIn("No qualifying setups found today.", text)

    def test_entries_and_exits_are_listed(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test",
            new_entries=[{"symbol": "SYM", "entry_price": 100.0, "quantity": 10, "stop_loss": 90.0}],
            new_exits=[{"symbol": "SYM2", "exit_price": 50.0, "pnl": -100.0, "reason": "stop_loss"}],
            open_positions=[], daily_pnl=-100.0, total_equity=99000, drawdown_pct=1.0,
            win_rate=0.5, expectancy=10.0,
        )
        self.assertIn("SYM", text)
        self.assertIn("SYM2", text)
        self.assertIn("stop\\_loss", text)   # escaped -- see test_deployment_operational_v2's Markdown tests

    def test_open_positions_listed_and_empty_case(self):
        text_with = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[],
            open_positions=[{"symbol": "SYM", "entry_price": 100.0, "quantity": 10, "stop_loss": 90.0}],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
        )
        self.assertIn("SYM", text_with)
        text_without = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
        )
        self.assertIn("(none)", text_without)

    def test_one_strategy_per_message_no_combining(self):
        # Structural check: the function only accepts ONE strategy_display_name,
        # not a list -- there's no way to combine multiple strategies into
        # one call.
        import inspect
        sig = inspect.signature(format_strategy_notification)
        self.assertIn("strategy_display_name", sig.parameters)
        self.assertEqual(sig.parameters["strategy_display_name"].annotation, str)


class TestScheduler(unittest.TestCase):
    def test_market_open_during_hours_on_weekday(self):
        dt = datetime.datetime(2026, 8, 4, 11, 0)   # Tuesday, 11:00
        self.assertTrue(is_market_open(dt))

    def test_market_closed_before_open(self):
        dt = datetime.datetime(2026, 8, 4, 8, 0)   # Tuesday, 08:00
        self.assertFalse(is_market_open(dt))

    def test_market_closed_after_close(self):
        dt = datetime.datetime(2026, 8, 4, 16, 0)   # Tuesday, 16:00
        self.assertFalse(is_market_open(dt))

    def test_market_closed_on_weekend(self):
        dt = datetime.datetime(2026, 8, 8, 11, 0)   # Saturday
        self.assertFalse(is_market_open(dt))

    def _record(self, status=DeploymentStatus.PAPER_TRADING, execution_frequency="End-of-Day"):
        return StrategyRecord(strategy_key="s1", display_name="S1", strategy_family="fam",
                               deployment_status=status, execution_frequency=execution_frequency)

    def test_eod_strategy_not_due_during_market_hours(self):
        due, reason = is_due_now(self._record(), now=datetime.datetime(2026, 8, 4, 11, 0))
        self.assertFalse(due)

    def test_eod_strategy_due_after_close(self):
        due, reason = is_due_now(self._record(), now=datetime.datetime(2026, 8, 4, 16, 0))
        self.assertTrue(due)

    def test_intraday_strategy_due_during_market_hours(self):
        due, reason = is_due_now(self._record(execution_frequency="Intraday"),
                                  now=datetime.datetime(2026, 8, 4, 11, 0))
        self.assertTrue(due)

    def test_intraday_strategy_not_due_after_close(self):
        due, reason = is_due_now(self._record(execution_frequency="Intraday"),
                                  now=datetime.datetime(2026, 8, 4, 16, 0))
        self.assertFalse(due)

    def test_research_status_strategy_never_due(self):
        due, reason = is_due_now(self._record(status=DeploymentStatus.RESEARCH),
                                  now=datetime.datetime(2026, 8, 4, 16, 0))
        self.assertFalse(due)

    def test_archived_status_strategy_never_due(self):
        due, reason = is_due_now(self._record(status=DeploymentStatus.ARCHIVED),
                                  now=datetime.datetime(2026, 8, 4, 16, 0))
        self.assertFalse(due)

    def test_strategies_due_now_filters_a_list(self):
        due_record = self._record()
        not_due_record = self._record(status=DeploymentStatus.RESEARCH)
        result = strategies_due_now([due_record, not_due_record], now=datetime.datetime(2026, 8, 4, 16, 0))
        self.assertEqual(result, [due_record])


class TestStrategyMetadata(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "registry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_register_with_custom_metadata(self):
        record = deployment_manager.register_strategy(
            "s1", "S1", "fam", timeframe="5 Minute", execution_frequency="Intraday",
            registry_path=self.registry_path,
        )
        self.assertEqual(record.timeframe, "5 Minute")
        self.assertEqual(record.execution_frequency, "Intraday")

    def test_default_metadata_is_daily_eod(self):
        record = deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        self.assertEqual(record.timeframe, "Daily")
        self.assertEqual(record.execution_frequency, "End-of-Day")

    def test_metadata_survives_roundtrip_through_json(self):
        deployment_manager.register_strategy("s1", "S1", "fam", timeframe="5 Minute",
                                              execution_frequency="Intraday", registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.timeframe, "5 Minute")
        self.assertEqual(record.execution_frequency, "Intraday")

    def test_update_strategy_metadata_never_touches_verdict_or_status(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_research_verdict("s1", ResearchVerdict.PASS, "EXP-1",
                                                 registry_path=self.registry_path)
        deployment_manager.update_strategy_metadata("s1", timeframe="5 Minute",
                                                      registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.timeframe, "5 Minute")
        self.assertEqual(record.research_verdict, ResearchVerdict.PASS)


class TestDriftHistoryGrows(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.tmpdir, "reports")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _fake_historical_experiment(self):
        return {
            "metrics": {"win_rate": 0.5, "expectancy": 100.0, "cagr": 20.0, "sharpe_ratio": 1.0,
                       "max_drawdown_pct": 10.0, "avg_holding_period_days": 30.0, "total_trades": 300},
            "parameters": {"data_period": "2016-01-01 to 2026-01-01"},
        }

    def test_history_file_grows_across_multiple_calls(self):
        with patch("deployment.drift_report.load_experiment", return_value=self._fake_historical_experiment()), \
             patch("deployment.drift_report.compute_live_metrics",
                   return_value={"win_rate": 0.5, "expectancy": 100.0, "cagr": 20.0, "sharpe_ratio": 1.0,
                                "max_drawdown_pct": 10.0, "avg_holding_period_days": 30.0, "total_trades": 5}), \
             patch("deployment.drift_report._live_trading_calendar_days", return_value=10):
            generate_drift_report("s1", "S1", "EXP-999", "/fake/dir", output_dir=self.output_dir)
            generate_drift_report("s1", "S1", "EXP-999", "/fake/dir", output_dir=self.output_dir)
            history = load_drift_history("s1", output_dir=self.output_dir)
            self.assertEqual(len(history), 2)   # never overwrites -- appends each call

    def test_trade_frequency_is_computed_and_compared(self):
        with patch("deployment.drift_report.load_experiment", return_value=self._fake_historical_experiment()), \
             patch("deployment.drift_report.compute_live_metrics",
                   return_value={"win_rate": 0.5, "expectancy": 100.0, "cagr": 20.0, "sharpe_ratio": 1.0,
                                "max_drawdown_pct": 10.0, "avg_holding_period_days": 30.0, "total_trades": 5}), \
             patch("deployment.drift_report._live_trading_calendar_days", return_value=10):
            drift = compute_drift("s1", "EXP-999", "/fake/dir")
            self.assertIn("trade_frequency", drift["historical"])
            self.assertIn("trade_frequency", drift["live"])


if __name__ == "__main__":
    unittest.main()
