"""
Unit tests for the operational hardening additions (2026-08-04, round 2):
permanent strategy_id assignment/backfill, primary_experiment_id, the
daily paper-trading summary formatter, and report links in Telegram
messages. No real Telegram calls made anywhere. Run with:

    python test_deployment_operational_v2.py
"""

import os
import shutil
import tempfile
import unittest

from deployment import deployment_manager
from reporting.telegram_templates import format_daily_summary, format_strategy_notification


class TestStrategyIdAssignment(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "registry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_first_registered_strategy_gets_sw_001(self):
        record = deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        self.assertEqual(record.strategy_id, "SW-001")

    def test_ids_increment_sequentially_across_strategies(self):
        r1 = deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        r2 = deployment_manager.register_strategy("s2", "S2", "fam", registry_path=self.registry_path)
        r3 = deployment_manager.register_strategy("s3", "S3", "fam", registry_path=self.registry_path)
        self.assertEqual([r1.strategy_id, r2.strategy_id, r3.strategy_id], ["SW-001", "SW-002", "SW-003"])

    def test_id_is_permanent_across_reregistration_attempts(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.register_strategy("s2", "S2", "fam", registry_path=self.registry_path)
        # re-registering s1 (idempotent) must return its ORIGINAL id, not a new one
        record = deployment_manager.register_strategy("s1", "S1 (renamed)", "fam", registry_path=self.registry_path)
        self.assertEqual(record.strategy_id, "SW-001")

    def test_backfill_assigns_ids_to_records_missing_one(self):
        # Simulate a pre-existing registry written before strategy_id existed
        # (strategy_id="" is the from_dict() default for a missing key).
        import json
        raw = {
            "old1": {"strategy_key": "old1", "display_name": "Old 1", "strategy_family": "fam"},
            "old2": {"strategy_key": "old2", "display_name": "Old 2", "strategy_family": "fam"},
        }
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(raw, f)

        ids = deployment_manager.backfill_missing_strategy_ids(registry_path=self.registry_path)
        self.assertEqual(ids["old1"], "SW-001")
        self.assertEqual(ids["old2"], "SW-002")

    def test_backfill_is_safely_rerunnable_never_reassigns(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        ids_first = deployment_manager.backfill_missing_strategy_ids(registry_path=self.registry_path)
        ids_second = deployment_manager.backfill_missing_strategy_ids(registry_path=self.registry_path)
        self.assertEqual(ids_first, ids_second)

    def test_set_primary_experiment_id(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_primary_experiment_id("s1", "EXP-013", registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.primary_experiment_id, "EXP-013")

    def test_set_primary_experiment_id_never_touches_verdict_or_status(self):
        from deployment.base import ResearchVerdict
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_research_verdict("s1", ResearchVerdict.PASS, "EXP-1",
                                                 registry_path=self.registry_path)
        deployment_manager.set_primary_experiment_id("s1", "EXP-013", registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.research_verdict, ResearchVerdict.PASS)


class TestTelegramStrategyIdAndLinks(unittest.TestCase):
    def test_strategy_id_appears_in_message(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
            strategy_id="SW-003",
        )
        self.assertIn("SW-003", text)

    def test_compact_header_format_matches_spec(self):
        # Per explicit direction 2026-08-05: "🧪 SW-003 | 52-Week High Momentum"
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="52-Week High Momentum", new_entries=[], new_exits=[],
            open_positions=[], daily_pnl=0.0, total_equity=100000, drawdown_pct=None,
            win_rate=None, expectancy=None, strategy_id="SW-003",
        )
        first_line = text.splitlines()[0]
        self.assertIn("SW-003 | 52-Week High Momentum", first_line)
        self.assertIn("\U0001F9EA", first_line)

    def test_live_compact_header_uses_rocket(self):
        text = format_strategy_notification(
            mode="LIVE", strategy_display_name="MA Crossover", new_entries=[], new_exits=[],
            open_positions=[], daily_pnl=0.0, total_equity=100000, drawdown_pct=None,
            win_rate=None, expectancy=None, strategy_id="SW-004",
        )
        first_line = text.splitlines()[0]
        self.assertIn("\U0001F680", first_line)
        self.assertIn("SW-004", first_line)

    def test_no_strategy_id_omits_the_line_gracefully(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
        )
        self.assertNotIn("Strategy: ", text)

    def test_underscore_heavy_exit_reason_is_escaped(self):
        # Real bug found 2026-08-04: Telegram's legacy Markdown parser
        # treats unpaired underscores as italic delimiters and rejects the
        # WHOLE message with a 400 error -- exit_reason values like
        # "signal_exit"/"stop_loss" triggered this in production use.
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[],
            new_exits=[{"symbol": "TCS.NS", "exit_price": 3800.0, "pnl": 100.0, "reason": "signal_exit"}],
            open_positions=[], daily_pnl=100.0, total_equity=100000, drawdown_pct=None,
            win_rate=None, expectancy=None,
        )
        self.assertIn("signal\\_exit", text)
        self.assertNotIn("signal_exit", text)   # the raw unescaped form must never appear

    def test_underscore_heavy_report_path_is_escaped(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
            report_links={"Daily Report": "deployment/reports/fifty_two_week_high_momentum/2026-08-04.md"},
        )
        self.assertIn("fifty\\_two\\_week\\_high\\_momentum", text)

    def test_report_links_appear_in_message(self):
        text = format_strategy_notification(
            mode="PAPER", strategy_display_name="Test", new_entries=[], new_exits=[], open_positions=[],
            daily_pnl=0.0, total_equity=100000, drawdown_pct=None, win_rate=None, expectancy=None,
            report_links={"Experiment": "EXP-013", "Daily Report": "deployment/reports/x/2026-08-04.md"},
        )
        self.assertIn("EXP-013", text)
        self.assertIn("deployment/reports/x/2026-08-04.md", text)


class TestDailySummary(unittest.TestCase):
    def test_includes_all_strategies_executed(self):
        text = format_daily_summary(
            strategy_results=[
                {"display_name": "52-Week High Momentum", "new_entries": [{"symbol": "A"}], "new_exits": []},
                {"display_name": "Minervini", "new_entries": [], "new_exits": []},
            ],
            closed_trades_today=2, open_positions_total=14, daily_pnl_total=1234.56,
            portfolio_equity_total=2000000.0, blended_win_rate=0.45,
        )
        self.assertIn("52-Week High Momentum", text)
        self.assertIn("Minervini", text)
        self.assertIn("DAILY PAPER TRADING SUMMARY", text)

    def test_signal_counts_reflect_entries_and_exits(self):
        text = format_daily_summary(
            strategy_results=[
                {"display_name": "S1", "new_entries": [{"symbol": "A"}, {"symbol": "B"}], "new_exits": []},
                {"display_name": "S2", "new_entries": [], "new_exits": []},
            ],
            closed_trades_today=0, open_positions_total=2, daily_pnl_total=0.0,
            portfolio_equity_total=1000000.0, blended_win_rate=None,
        )
        self.assertIn("S1: 2 BUY", text)
        self.assertIn("S2: No Setup", text)

    def test_exit_only_day_shows_sell_not_no_setup(self):
        # Regression test for a real bug found 2026-08-18: a strategy that
        # only closed a position today (no new entry) previously showed
        # "No Setup", identical to a day with zero activity at all.
        text = format_daily_summary(
            strategy_results=[
                {"display_name": "S1", "new_entries": [], "new_exits": [{"symbol": "A", "pnl": 50.0}]},
                {"display_name": "S2", "new_entries": [{"symbol": "B"}],
                 "new_exits": [{"symbol": "C", "pnl": -20.0}]},
            ],
            closed_trades_today=2, open_positions_total=1, daily_pnl_total=30.0,
            portfolio_equity_total=1000000.0, blended_win_rate=None,
        )
        self.assertIn("S1: 1 SELL", text)
        self.assertNotIn("S1: No Setup", text)
        self.assertIn("S2: 1 BUY, 1 SELL", text)

    def test_booked_pnl_shown_distinctly_from_todays_pnl(self):
        text = format_daily_summary(
            strategy_results=[], closed_trades_today=1, open_positions_total=5,
            daily_pnl_total=1234.56, portfolio_equity_total=2000000.0, blended_win_rate=0.45,
            booked_pnl_today=500.0,
        )
        self.assertIn("Booked P&L (closed trades today)", text)
        self.assertIn("500.00", text)
        self.assertIn("Today's P&L", text)
        self.assertNotIn("unrealised", text.lower())
        self.assertIn("1,234.56", text)

    def test_total_pnl_shown_when_provided(self):
        # Per direct user feedback 2026-08-19: a cumulative "Total P&L"
        # figure (equity minus starting capital), replacing a prior
        # separate "unrealised" line that was confusing jargon.
        text = format_daily_summary(
            strategy_results=[], closed_trades_today=0, open_positions_total=5,
            daily_pnl_total=100.0, portfolio_equity_total=2000000.0, blended_win_rate=None,
            total_pnl=50000.0,
        )
        self.assertIn("*Total P&L*: 50,000.00", text)
        self.assertNotIn("Unrealized", text)
        self.assertNotIn("unrealised", text.lower())

    def test_booked_pnl_omitted_when_not_passed(self):
        text = format_daily_summary(
            strategy_results=[], closed_trades_today=0, open_positions_total=0,
            daily_pnl_total=0.0, portfolio_equity_total=1000000.0, blended_win_rate=None,
        )
        self.assertNotIn("Booked P&L", text)

    def test_summary_shows_closed_trades_positions_pnl_equity(self):
        text = format_daily_summary(
            strategy_results=[], closed_trades_today=2, open_positions_total=14,
            daily_pnl_total=1234.56, portfolio_equity_total=2000000.0, blended_win_rate=0.45,
        )
        self.assertIn("2", text)
        self.assertIn("14", text)
        self.assertIn("1,234.56", text)
        self.assertIn("2,000,000.00", text)

    def test_invested_amount_shown_when_provided(self):
        # Added 2026-08-26, per direct feedback that Portfolio Equity
        # alone doesn't say how much of it is actually deployed.
        text = format_daily_summary(
            strategy_results=[], closed_trades_today=0, open_positions_total=5,
            daily_pnl_total=0.0, portfolio_equity_total=2000000.0, blended_win_rate=None,
            invested_amount_total=1500000.0,
        )
        self.assertIn("*Invested Amount*: 1,500,000.00", text)

    def test_invested_amount_omitted_when_not_passed(self):
        text = format_daily_summary(
            strategy_results=[], closed_trades_today=0, open_positions_total=0,
            daily_pnl_total=0.0, portfolio_equity_total=1000000.0, blended_win_rate=None,
        )
        self.assertNotIn("Invested Amount", text)


if __name__ == "__main__":
    unittest.main()
