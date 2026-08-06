"""
Tests for run_paper_trading.py's exception-isolation behaviour -- a
strategy failure inside _run_one() must never propagate, so --all-due's
loop can keep going and still send the daily summary for whatever DID
succeed. See deployment/PROMOTION_CHECKLIST.md for the incident that
motivated this (SW-008 wasn't the cause, but the missing isolation was
flagged as a gap during that deployment's verification).
"""

import unittest
from unittest.mock import MagicMock, patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
import run_paper_trading as rpt


def _fake_record(strategy_key, display_name):
    return StrategyRecord(
        strategy_key=strategy_key, display_name=display_name, strategy_family="swing_research published strategy",
        research_verdict=ResearchVerdict.PASS, research_verdict_source="EXP-000",
        deployment_status=DeploymentStatus.PAPER_TRADING, deployment_status_history=[],
        strategy_id="SW-000", primary_experiment_id="EXP-000",
    )


class TestRunOneExceptionIsolation(unittest.TestCase):
    def setUp(self):
        self.record = _fake_record("fake_strategy", "Fake Strategy")
        rpt._STRATEGY_FACTORIES["fake_strategy"] = {
            "display_name": "Fake Strategy",
            "strategy_factory": MagicMock(),
        }

    def tearDown(self):
        rpt._STRATEGY_FACTORIES.pop("fake_strategy", None)

    @patch("run_paper_trading.send_telegram_message")
    @patch("run_paper_trading.get_swing_universe", side_effect=RuntimeError("data provider outage"))
    @patch("run_paper_trading.is_due_now", return_value=(True, "due"))
    @patch("run_paper_trading.get_strategy")
    def test_data_fetch_exception_is_caught_not_raised(self, mock_get_strategy, _mock_due, _mock_universe, mock_send):
        mock_get_strategy.return_value = self.record

        result = rpt._run_one("fake_strategy")   # must not raise

        self.assertIsNone(result)
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("FAILED", sent_text)
        self.assertIn("Fake Strategy", sent_text)
        self.assertIn("RuntimeError", sent_text)

    @patch("run_paper_trading.send_telegram_message", side_effect=ConnectionError("network down"))
    @patch("run_paper_trading.get_swing_universe", side_effect=RuntimeError("data provider outage"))
    @patch("run_paper_trading.is_due_now", return_value=(True, "due"))
    @patch("run_paper_trading.get_strategy")
    def test_failure_notification_itself_failing_does_not_raise(self, mock_get_strategy, _mock_due, _mock_universe,
                                                                  _mock_send):
        mock_get_strategy.return_value = self.record

        result = rpt._run_one("fake_strategy")   # even a broken Telegram send must not raise

        self.assertIsNone(result)

    @patch("run_paper_trading._send_notification", side_effect=ValueError("bad template data"))
    @patch("run_paper_trading.generate_report", return_value="deployment/reports/fake_strategy/2026-01-01.md")
    @patch("run_paper_trading.run_daily")
    @patch("run_paper_trading.fetch_all", return_value={})
    @patch("run_paper_trading.get_swing_universe", return_value=[])
    @patch("run_paper_trading.is_due_now", return_value=(True, "due"))
    @patch("run_paper_trading.get_strategy")
    @patch("run_paper_trading.send_telegram_message")
    def test_notification_exception_is_also_caught(self, mock_send, mock_get_strategy, _mock_due, _mock_universe,
                                                     _mock_fetch, mock_run_daily, _mock_report, _mock_notify):
        mock_get_strategy.return_value = self.record
        mock_run_daily.return_value = {
            "status": "processed", "as_of_date": "2026-01-01", "new_entries": [], "new_exits": [],
            "open_positions": 0, "cash": 1000000, "mark_to_market_equity": 1000000,
        }

        result = rpt._run_one("fake_strategy")   # _send_notification raising must still be caught

        self.assertIsNone(result)
        # the failure-notification path (a separate call) should still have fired
        mock_send.assert_called_once()
        self.assertIn("FAILED", mock_send.call_args[0][0])


class TestAllDueLoopContinuesPastOneFailure(unittest.TestCase):
    def setUp(self):
        rpt._STRATEGY_FACTORIES["fake_a"] = {"display_name": "A", "strategy_factory": MagicMock()}
        rpt._STRATEGY_FACTORIES["fake_b"] = {"display_name": "B", "strategy_factory": MagicMock()}

    def tearDown(self):
        rpt._STRATEGY_FACTORIES.pop("fake_a", None)
        rpt._STRATEGY_FACTORIES.pop("fake_b", None)

    @patch("run_paper_trading._send_daily_summary")
    @patch("run_paper_trading._run_one")
    @patch("run_paper_trading.strategies_due_now")
    @patch("run_paper_trading.list_strategies", return_value=[])
    def test_one_strategy_failing_does_not_stop_the_other_or_the_summary(self, _mock_list, mock_due_now,
                                                                          mock_run_one, mock_summary):
        mock_due_now.return_value = [_fake_record("fake_a", "A"), _fake_record("fake_b", "B")]

        # fake_a "fails" inside _run_one (already caught there -> returns None),
        # fake_b succeeds and returns a normal result dict.
        def side_effect(strategy_key, force=False):
            if strategy_key == "fake_a":
                return None
            return {"strategy_key": "fake_b", "display_name": "B",
                     "result": {"new_entries": [], "new_exits": [], "mark_to_market_equity": 1000000}}

        mock_run_one.side_effect = side_effect

        with patch("sys.argv", ["run_paper_trading.py", "--all-due"]):
            rpt.main()

        self.assertEqual(mock_run_one.call_count, 2)
        mock_summary.assert_called_once()
        summary_arg = mock_summary.call_args[0][0]
        self.assertEqual(len(summary_arg), 1)
        self.assertEqual(summary_arg[0]["strategy_key"], "fake_b")

    @patch("run_paper_trading.send_telegram_message")
    @patch("run_paper_trading._run_one")
    @patch("run_paper_trading.strategies_due_now")
    @patch("run_paper_trading.list_strategies", return_value=[])
    def test_daily_summary_exception_does_not_raise_out_of_main(self, _mock_list, mock_due_now, mock_run_one,
                                                                  _mock_send):
        mock_due_now.return_value = [_fake_record("fake_a", "A")]
        mock_run_one.return_value = {"strategy_key": "fake_a", "display_name": "A",
                                      "result": {"new_entries": [], "new_exits": [], "mark_to_market_equity": 1000000}}

        with patch("run_paper_trading._send_daily_summary", side_effect=ValueError("boom")), \
             patch("sys.argv", ["run_paper_trading.py", "--all-due"]):
            rpt.main()   # must not raise despite the summary blowing up


if __name__ == "__main__":
    unittest.main()
