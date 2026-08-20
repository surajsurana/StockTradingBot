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

from reporting.telegram_templates import format_execution_notification, format_strategy_notification
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

    def test_many_decimal_prices_display_with_max_two_decimal_places(self):
        # Regression test for the 2026-08-17 fix -- real fill prices from
        # this program's own float arithmetic (e.g. entry_price=
        # 203.55999755859375) were previously shown raw/unformatted in
        # Telegram messages. See reporting/format_utils.py.
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test",
            new_entries=[{"symbol": "SYM", "entry_price": 203.55999755859375, "quantity": 614,
                         "stop_loss": 187.27519775390627}],
            new_exits=[{"symbol": "SYM2", "exit_price": 1329229.2412109375, "pnl": -100.333333333,
                       "reason": "stop_loss"}],
            open_positions=[], daily_pnl=-100.0, total_equity=99000, drawdown_pct=14.254612,
            win_rate=0.503217, expectancy=1.032178,
        )
        self.assertIn("203.56", text)
        self.assertIn("187.28", text)
        self.assertIn("1,329,229.24", text)
        self.assertIn("-100.33", text)
        self.assertIn("14.25", text)
        self.assertIn("0.50", text)
        self.assertIn("1.03", text)
        # None of the raw many-decimal originals should survive into the message.
        self.assertNotIn("203.55999755859375", text)
        self.assertNotIn("187.27519775390627", text)

    def test_open_positions_show_current_value_and_pnl_not_entry_price(self):
        # Per direct user feedback 2026-08-18: open positions should show
        # what a position is worth NOW, not its entry price/stop-loss
        # (which the user has to see every single day even though it
        # never changes).
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[],
            open_positions=[{"symbol": "SYM", "quantity": 10, "entry_price": 100.0,
                             "current_price": 110.0, "current_value": 1100.0,
                             "unrealized_pnl": 100.0, "unrealized_pnl_pct": 10.0}],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
        )
        self.assertIn("SYM", text)
        self.assertIn("1,100.00", text)   # current value
        self.assertIn("100.00", text)     # unrealized P&L
        self.assertIn("10.00%", text)     # unrealized P&L %

    def test_pending_entries_and_exits_shown_as_queued_not_invisible(self):
        # Per direct user feedback 2026-08-18: a signal detected today but
        # deferred to next_day_open previously looked IDENTICAL to "no
        # signal at all" -- this must be visible and distinct.
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            pending_entries=[{"symbol": "NEW.NS", "stop_loss": 90.0, "signal_date": "2026-08-17"}],
            pending_exits=[{"symbol": "OLD.NS", "exit_reason": "stop_loss", "signal_date": "2026-08-17"}],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
        )
        self.assertIn("Queued for Next Open", text)
        self.assertIn("NEW.NS", text)
        self.assertIn("OLD.NS", text)
        self.assertNotIn("No qualifying setups found today.", text)

    def test_daily_pnl_none_shows_n_a_not_a_crash(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            daily_pnl=None, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
        )
        self.assertIn("Daily P&L: n/a", text)

    def test_open_positions_listed_and_empty_case(self):
        text_with = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[],
            open_positions=[{"symbol": "SYM", "quantity": 10, "current_value": 1000.0,
                             "unrealized_pnl": 0.0, "unrealized_pnl_pct": 0.0}],
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


class TestExecutionNotification(unittest.TestCase):
    """format_execution_notification() -- the lean, near-real-time
    execution confirmation sent by --resolve-at-open (added 2026-08-18,
    see run_paper_trading.py's _resolve_at_open_one())."""

    def test_bought_and_sold_lines_shown_with_signal_price(self):
        # Per direct user feedback 2026-08-19: show the signal price
        # (the close price the signal was detected at) alongside the
        # actual fill price, and drop the verbose date note to keep the
        # message short -- each stock gets its own short, blank-line-
        # separated block, not one crammed line.
        text = format_execution_notification(
            mode="PAPER", strategy_display_name="Test Strategy", strategy_id="SW-003",
            new_entries=[{"symbol": "SYM.NS", "entry_price": 105.25, "quantity": 100,
                         "stop_loss": 95.0, "signal_date": "2026-08-17", "signal_price": 103.10}],
            new_exits=[{"symbol": "OLD.NS", "exit_price": 200.5, "pnl": 50.0,
                       "reason": "stop_loss", "signal_date": "2026-08-17"}],
        )
        self.assertIn("EXECUTED", text)
        self.assertIn("SW-003", text)
        self.assertIn("BOUGHT", text)
        self.assertIn("SYM.NS", text)
        self.assertIn("103.10", text)   # signal price
        self.assertIn("105.25", text)   # actual fill price
        self.assertIn("SOLD", text)
        self.assertIn("OLD.NS", text)
        self.assertIn("200.50", text)

    def test_entries_and_exits_are_separated_by_a_blank_line_per_stock(self):
        text = format_execution_notification(
            mode="PAPER", strategy_display_name="Test Strategy",
            new_entries=[
                {"symbol": "AAA.NS", "entry_price": 100.0, "quantity": 10, "stop_loss": 90.0},
                {"symbol": "BBB.NS", "entry_price": 200.0, "quantity": 5, "stop_loss": 180.0},
            ],
            new_exits=[],
        )
        self.assertIn("AAA.NS\n", text)
        self.assertIn("\n\n*BOUGHT* BBB.NS", text)   # a blank line separates the two stock blocks

    def test_entry_without_signal_price_falls_back_gracefully(self):
        # Backward compatibility -- pending entries queued before
        # signal_price existed resolve with signal_price=None; must not
        # crash or show a broken "Signal: None" line.
        text = format_execution_notification(
            mode="PAPER", strategy_display_name="Test Strategy",
            new_entries=[{"symbol": "OLD.NS", "entry_price": 50.0, "quantity": 20, "stop_loss": 45.0}],
            new_exits=[],
        )
        self.assertIn("Bought: 20 x 50.00 = 1,000.00", text)
        self.assertNotIn("Signal:", text)
        self.assertNotIn("None", text)

    def test_bought_sold_are_bold_qty_price_total_and_no_target(self):
        # Per direct user feedback 2026-08-19: *BOUGHT*/*SOLD* bold; qty
        # moved onto the fill line as "qty x price = total"; Stop is the
        # final line -- no Target (these strategies have no actual
        # profit-target rule, so one wasn't invented, per explicit
        # direction to skip it rather than fabricate a number).
        text = format_execution_notification(
            mode="PAPER", strategy_display_name="Test Strategy",
            new_entries=[{"symbol": "SYM.NS", "entry_price": 10.0, "quantity": 100, "stop_loss": 9.0,
                         "signal_price": 9.8}],
            new_exits=[{"symbol": "OLD.NS", "exit_price": 20.0, "quantity": 50, "pnl": 100.0,
                       "reason": "stop_loss"}],
        )
        self.assertIn("*BOUGHT* SYM.NS", text)
        self.assertIn("Signal: 9.80", text)
        self.assertIn("Bought: 100 x 10.00 = 1,000.00", text)
        self.assertIn("Stop: 9.00", text)
        self.assertNotIn("Target", text)
        self.assertIn("*SOLD* OLD.NS", text)
        self.assertIn("Sold: 50 x 20.00 = 1,000.00", text)

    def test_empty_lists_produce_just_the_header(self):
        text = format_execution_notification(
            mode="PAPER", strategy_display_name="Test Strategy", new_entries=[], new_exits=[],
        )
        self.assertIn("EXECUTED", text)
        self.assertNotIn("BOUGHT", text)
        self.assertNotIn("SOLD", text)


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
