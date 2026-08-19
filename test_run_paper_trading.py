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

import pandas as pd

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
import run_paper_trading as rpt


def _synthetic_price_data(symbols, n=280):
    """Minimal OHLCV fixture, long enough (>=~252 bars) to satisfy every
    cross-sectional strategy's own lookback -- the longest being 52-Week
    High Momentum's and Minervini RS's ~12-month (252 trading day) window."""
    dates = pd.bdate_range("2024-01-01", periods=n)
    data = {}
    for i, sym in enumerate(symbols):
        close = pd.Series([100.0 + i + j * 0.1 for j in range(n)], index=dates)
        data[sym] = pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.98, "Close": close, "Volume": 100000,
        }, index=dates)
    return data


class TestStrategyFactoryExtraColumnNaming(unittest.TestCase):
    """Regression test for the 2026-08-17 bug: compute_*_percentile_ranks()
    returns {symbol: Series} where each Series' own .name is the SYMBOL
    (an artifact of column-wise slicing a wide-format DataFrame), not the
    feature name each strategy's precompute() looks for. Without an
    explicit rename, deployment/paper_trading_engine.py's
    df.join(extra_columns[symbol]) joins a column named after the symbol
    instead of e.g. "rs_percentile" -- precompute() then never finds its
    expected column and the strategy can never signal. Found after
    fifty_two_week_high_momentum/short_term_reversal/
    minervini_trend_template_filter had been running in PAPER_TRADING for
    days/weeks with this bug, structurally unable to ever generate an
    entry signal despite showing no errors. This test would have caught it."""

    EXPECTED_COLUMN_BY_STRATEGY = {
        "fifty_two_week_high_momentum": "nearness_percentile",
        "short_term_reversal": "reversal_percentile",
        "minervini_trend_template_filter": "rs_percentile",
        "cross_sectional_momentum": "momentum_percentile",
    }

    def test_extra_columns_are_named_what_precompute_expects(self):
        symbols = ["AAA.NS", "BBB.NS", "CCC.NS"]
        data = _synthetic_price_data(symbols)

        for strategy_key, expected_column in self.EXPECTED_COLUMN_BY_STRATEGY.items():
            with self.subTest(strategy_key=strategy_key):
                config = rpt._STRATEGY_FACTORIES[strategy_key]
                extra = config["compute_extra_columns_fn"](data)
                self.assertTrue(extra, f"{strategy_key}: compute_extra_columns_fn returned nothing for the fixture")
                sample_series = next(iter(extra.values()))
                self.assertEqual(
                    sample_series.name, expected_column,
                    f"{strategy_key}: extra-column Series is named {sample_series.name!r}, "
                    f"but its own strategy's precompute() looks for {expected_column!r} -- "
                    f"df.join() would silently create the wrong column and the strategy could never signal.",
                )

    def test_joined_column_is_actually_present_and_usable(self):
        """End-to-end version of the same check: exactly mimics
        deployment/paper_trading_engine.py's df.join(extra_columns[symbol])
        call and confirms the resulting DataFrame has real (non-NaN)
        values under the exact column name precompute() reads."""
        symbols = ["AAA.NS", "BBB.NS", "CCC.NS"]
        data = _synthetic_price_data(symbols)

        for strategy_key, expected_column in self.EXPECTED_COLUMN_BY_STRATEGY.items():
            with self.subTest(strategy_key=strategy_key):
                config = rpt._STRATEGY_FACTORIES[strategy_key]
                extra = config["compute_extra_columns_fn"](data)
                symbol = "AAA.NS"
                df = data[symbol].sort_index().join(extra[symbol])
                self.assertIn(expected_column, df.columns)
                self.assertTrue(df[expected_column].notna().any(),
                                 f"{strategy_key}: {expected_column} column exists but is entirely NaN")


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


class TestResolveAtOpenOne(unittest.TestCase):
    """_resolve_at_open_one() -- the near-market-open pass added
    2026-08-18 (--resolve-at-open), per direct user feedback ("I should
    be getting a Telegram message at the live time when something is
    bought or sold"). Uses deployment.paper_trading_engine.
    resolve_pending_fills_at_open() internally -- mocked here so these
    tests stay fast/isolated; that function's own real behavior is
    covered in test_deployment.py's TestResolvePendingFillsAtOpen."""

    def setUp(self):
        self.record = _fake_record("fake_strategy", "Fake Strategy")
        rpt._STRATEGY_FACTORIES["fake_strategy"] = {
            "display_name": "Fake Strategy",
            "strategy_factory": MagicMock(),
        }

    def tearDown(self):
        rpt._STRATEGY_FACTORIES.pop("fake_strategy", None)

    @patch("run_paper_trading.send_telegram_message")
    @patch("run_paper_trading.resolve_pending_fills_at_open")
    @patch("run_paper_trading.load_portfolio")
    @patch("run_paper_trading.get_strategy")
    def test_nothing_pending_skips_resolution_and_sends_no_message(self, mock_get_strategy, mock_load_portfolio,
                                                                     mock_resolve, mock_send):
        mock_get_strategy.return_value = self.record
        mock_load_portfolio.return_value = {"pending_entries": {}, "pending_exits": {}}

        rpt._resolve_at_open_one("fake_strategy")

        mock_resolve.assert_not_called()
        mock_send.assert_not_called()

    @patch("run_paper_trading.send_telegram_message")
    @patch("run_paper_trading.resolve_pending_fills_at_open")
    @patch("run_paper_trading.load_portfolio")
    @patch("run_paper_trading.get_strategy")
    def test_resolved_fill_sends_an_execution_notification(self, mock_get_strategy, mock_load_portfolio,
                                                             mock_resolve, mock_send):
        mock_get_strategy.return_value = self.record
        mock_load_portfolio.return_value = {"pending_entries": {"SYM.NS": {"stop_loss": 90.0}},
                                             "pending_exits": {}}
        mock_resolve.return_value = {
            "status": "processed", "as_of_date": "2026-08-18",
            "new_entries": [{"symbol": "SYM.NS", "entry_price": 105.0, "quantity": 10, "stop_loss": 90.0}],
            "new_exits": [],
        }

        rpt._resolve_at_open_one("fake_strategy")

        mock_resolve.assert_called_once()
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("SYM.NS", sent_text)
        self.assertIn("EXECUTED", sent_text)

    @patch("run_paper_trading.send_telegram_message")
    @patch("run_paper_trading.resolve_pending_fills_at_open")
    @patch("run_paper_trading.load_portfolio")
    @patch("run_paper_trading.get_strategy")
    def test_nothing_actually_filled_sends_no_message(self, mock_get_strategy, mock_load_portfolio,
                                                        mock_resolve, mock_send):
        # Pending items existed, but today's Open wasn't available yet --
        # resolve_pending_fills_at_open() returns empty lists, not an
        # error. Must not send a message for "nothing happened."
        mock_get_strategy.return_value = self.record
        mock_load_portfolio.return_value = {"pending_entries": {"SYM.NS": {"stop_loss": 90.0}},
                                             "pending_exits": {}}
        mock_resolve.return_value = {"status": "processed", "as_of_date": "2026-08-18",
                                      "new_entries": [], "new_exits": []}

        rpt._resolve_at_open_one("fake_strategy")

        mock_resolve.assert_called_once()
        mock_send.assert_not_called()

    @patch("run_paper_trading.send_telegram_message")
    @patch("run_paper_trading.resolve_pending_fills_at_open", side_effect=RuntimeError("data provider outage"))
    @patch("run_paper_trading.load_portfolio")
    @patch("run_paper_trading.get_strategy")
    def test_exception_is_caught_not_raised(self, mock_get_strategy, mock_load_portfolio, _mock_resolve, mock_send):
        mock_get_strategy.return_value = self.record
        mock_load_portfolio.return_value = {"pending_entries": {"SYM.NS": {"stop_loss": 90.0}},
                                             "pending_exits": {}}

        rpt._resolve_at_open_one("fake_strategy")   # must not raise

        mock_send.assert_not_called()

    @patch("run_paper_trading._resolve_at_open_one")
    @patch("run_paper_trading.list_strategies")
    def test_cli_flag_iterates_only_active_strategies(self, mock_list, mock_resolve_one):
        rpt._STRATEGY_FACTORIES["fake_strategy_research"] = {
            "display_name": "Y", "strategy_factory": MagicMock(),
        }
        self.addCleanup(rpt._STRATEGY_FACTORIES.pop, "fake_strategy_research", None)

        mock_list.return_value = [
            _fake_record("fake_strategy", "Fake Strategy"),   # PAPER_TRADING, in _STRATEGY_FACTORIES
            StrategyRecord(strategy_key="not_registered", display_name="X", strategy_family="fam",
                            research_verdict=ResearchVerdict.PASS, research_verdict_source="EXP-000",
                            deployment_status=DeploymentStatus.PAPER_TRADING, deployment_status_history=[],
                            strategy_id="SW-999", primary_experiment_id=""),   # not in _STRATEGY_FACTORIES
            StrategyRecord(strategy_key="fake_strategy_research", display_name="Y", strategy_family="fam",
                            research_verdict=ResearchVerdict.PASS, research_verdict_source="EXP-000",
                            deployment_status=DeploymentStatus.RESEARCH, deployment_status_history=[],
                            strategy_id="SW-998", primary_experiment_id=""),   # inactive status
        ]

        with patch("sys.argv", ["run_paper_trading.py", "--resolve-at-open"]):
            rpt.main()

        mock_resolve_one.assert_called_once_with("fake_strategy")


if __name__ == "__main__":
    unittest.main()
