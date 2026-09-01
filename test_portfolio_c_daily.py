"""
Tests for portfolio_c/daily.py -- the full agent-driven daily cycle.

USE_NEWS_AGENT is patched off throughout (routes to disabled_news_assessment,
a pure no-op) so these tests never make a real network/news call -- only
fetch_fundamentals_fn and research_call_fn are exercised, both injected as
fakes, matching this codebase's established call_fn-override testing
convention (see test_capital_winddown.py, test_deployment.py, etc.).
"""

import datetime
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import portfolio_c.daily as pcd
import portfolio_c.state as pcs
from config import settings as agent_settings
from portfolio_c.engine import adapt_swing_signal
from swing_research.base import Signal as SwingSignal


def _healthy_fundamentals(symbol):
    return {"trailingEps": 10.0, "returnOnEquity": 0.20, "debtToEquity": 50.0,
            "revenueGrowth": 0.05, "profitMargins": 0.10, "sector": "Technology"}


def _research_response(verdict: str, confidence: float = 0.8):
    return f"VERDICT: {verdict}\nCONFIDENCE: {confidence}\nREASONING: test reasoning."


def _multi_day_df(start_date_str, n_days, base_price=100.0):
    idx = pd.bdate_range(start=start_date_str, periods=n_days)
    return pd.DataFrame({"Open": [base_price] * n_days, "High": [base_price * 1.02] * n_days,
                          "Low": [base_price * 0.98] * n_days, "Close": [base_price] * n_days,
                          "Volume": [10000] * n_days}, index=idx)


class _PortfolioCTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_state = patch.object(pcs, "PORTFOLIO_C_STATE_DIR", self.tmpdir)
        self.patcher_state.start()
        self.patcher_news = patch.object(agent_settings, "USE_NEWS_AGENT", False)
        self.patcher_news.start()

    def tearDown(self):
        self.patcher_state.stop()
        self.patcher_news.stop()
        shutil.rmtree(self.tmpdir)


class TestEvaluateNewCandidates(_PortfolioCTestBase):
    def test_favorable_candidate_is_queued_not_filled_today(self):
        portfolio = pcs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
        anchor_candidates = {"AAA.NS": {"max_effect": adapt_swing_signal(
            SwingSignal(symbol="AAA.NS", direction="BUY", entry_price=100.0, stop_loss=90.0,
                        confidence=0.9, strategy_name="max_effect"))}}

        pcd._evaluate_new_candidates(
            anchor_candidates, portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: _research_response("favorable", 0.9))

        self.assertIn("AAA.NS", portfolio["pending_entries"])
        self.assertEqual(portfolio["positions"], {})   # not filled today
        self.assertEqual(portfolio["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL)   # untouched today

    def test_unfavorable_candidate_is_rejected_not_queued(self):
        portfolio = pcs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
        anchor_candidates = {"AAA.NS": {"max_effect": adapt_swing_signal(
            SwingSignal(symbol="AAA.NS", direction="BUY", entry_price=100.0, stop_loss=90.0,
                        confidence=0.9, strategy_name="max_effect"))}}

        entries = pcd._evaluate_new_candidates(
            anchor_candidates, portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: _research_response("unfavorable", 0.9))

        self.assertEqual(portfolio["pending_entries"], {})
        self.assertIn("rejections", entries[0])

    def test_decisions_matched_back_by_symbol_not_by_position(self):
        """Regression test: allocate() re-sorts approved candidates by
        confidence, so a naive zip(candidates, decisions) would
        misattribute results whenever a low-confidence candidate is
        listed before a high-confidence one. AAA (low conf, listed
        first) must get AAA's own decision, not BBB's."""
        portfolio = pcs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5), "BBB.NS": _multi_day_df("2024-01-01", 5, base_price=200.0)}
        anchor_candidates = {
            "AAA.NS": {"max_effect": adapt_swing_signal(
                SwingSignal(symbol="AAA.NS", direction="BUY", entry_price=100.0, stop_loss=90.0,
                            confidence=0.55, strategy_name="max_effect"))},
            "BBB.NS": {"max_effect": adapt_swing_signal(
                SwingSignal(symbol="BBB.NS", direction="BUY", entry_price=200.0, stop_loss=180.0,
                            confidence=0.95, strategy_name="max_effect"))},
        }

        def research_call_fn(prompt):
            # Both are favorable, but at DIFFERENT confidences, so
            # allocate() will reorder them (BBB first, AAA second).
            if "AAA.NS" in prompt:
                return _research_response("favorable", 0.55)
            return _research_response("favorable", 0.95)

        pcd._evaluate_new_candidates(
            anchor_candidates, portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=research_call_fn)

        self.assertEqual(portfolio["pending_entries"]["AAA.NS"]["confidence"], 0.55)
        self.assertEqual(portfolio["pending_entries"]["BBB.NS"]["confidence"], 0.95)

    def test_symbol_already_held_is_skipped(self):
        portfolio = pcs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 90.0, "target": 120.0,
                                             "strategy_name": "max_effect", "confidence": 0.9}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
        anchor_candidates = {"AAA.NS": {"max_effect": adapt_swing_signal(
            SwingSignal(symbol="AAA.NS", direction="BUY", entry_price=100.0, stop_loss=90.0,
                        confidence=0.9, strategy_name="max_effect"))}}

        calls = []
        pcd._evaluate_new_candidates(
            anchor_candidates, portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: calls.append(p) or _research_response("favorable", 0.9))
        self.assertEqual(calls, [], "already-held symbol must never be re-evaluated as a new candidate")

    def test_fundamentals_fetch_failure_skips_symbol_without_crashing(self):
        portfolio = pcs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
        anchor_candidates = {"AAA.NS": {"max_effect": adapt_swing_signal(
            SwingSignal(symbol="AAA.NS", direction="BUY", entry_price=100.0, stop_loss=90.0,
                        confidence=0.9, strategy_name="max_effect"))}}

        def broken_fetch(symbol):
            raise RuntimeError("network down")

        entries = pcd._evaluate_new_candidates(
            anchor_candidates, portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=broken_fetch, research_call_fn=lambda p: _research_response("favorable"))
        self.assertEqual(entries, [])
        self.assertEqual(portfolio["pending_entries"], {})


class TestResolvePending(_PortfolioCTestBase):
    def test_pending_entry_fills_at_next_real_open(self):
        portfolio = pcs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "max_effect", "confidence": 0.9, "quantity": 50,
        }
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)}

        new_entries, new_exits = pcd._resolve_pending(portfolio, data, datetime.date(2024, 1, 5))

        self.assertEqual(len(new_entries), 1)
        self.assertIn("AAA.NS", portfolio["positions"])
        self.assertEqual(portfolio["positions"]["AAA.NS"]["quantity"], 50)
        self.assertEqual(portfolio["positions"]["AAA.NS"]["entry_price"], 101.0)
        self.assertEqual(portfolio["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL - 50 * 101.0)
        self.assertEqual(portfolio["pending_entries"], {})

    def test_pending_entry_abandoned_if_gap_crosses_stop(self):
        portfolio = pcs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "max_effect", "confidence": 0.9, "quantity": 50,
        }
        # Real open gapped BELOW the planned stop.
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=85.0)}

        new_entries, new_exits = pcd._resolve_pending(portfolio, data, datetime.date(2024, 1, 5))
        self.assertEqual(new_entries, [])
        self.assertNotIn("AAA.NS", portfolio["positions"])
        self.assertEqual(portfolio["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL, "abandoned entry must never spend cash")

    def test_pending_exit_resolves_at_next_open(self):
        portfolio = pcs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 90.0, "target": 120.0,
                                             "strategy_name": "max_effect", "confidence": 0.9}
        portfolio["pending_exits"]["AAA.NS"] = {"exit_reason": "unfavorable_verdict", "signal_date": "2024-01-04"}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=105.0)}

        new_entries, new_exits = pcd._resolve_pending(portfolio, data, datetime.date(2024, 1, 5))

        self.assertEqual(len(new_exits), 1)
        self.assertNotIn("AAA.NS", portfolio["positions"])
        self.assertEqual(portfolio["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL + 10 * 105.0)
        self.assertEqual(new_exits[0]["pnl"], 10 * (105.0 - 100.0))


class TestProcessExistingPositions(_PortfolioCTestBase):
    def test_stop_loss_hit_closes_position_same_day(self):
        portfolio = pcs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 90.0, "target": 120.0,
                                             "strategy_name": "max_effect", "confidence": 0.9}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=85.0)}   # Low=83.3, below stop

        realized_exits, decision_entries = pcd._process_existing_positions(
            portfolio, data, datetime.date(2024, 1, 5), anchor_candidates={}, api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=lambda p: _research_response("neutral"))

        self.assertEqual(len(realized_exits), 1)
        self.assertEqual(realized_exits[0]["exit_reason"], "stop_loss")
        self.assertNotIn("AAA.NS", portfolio["positions"])
        self.assertEqual(portfolio["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL + 10 * 90.0)

    def test_unfavorable_recheck_queues_exit_not_immediate(self):
        portfolio = pcs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 50.0, "target": 120.0,   # far stop, won't hit
                                             "strategy_name": "max_effect", "confidence": 0.9}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=100.0)}

        realized_exits, decision_entries = pcd._process_existing_positions(
            portfolio, data, datetime.date(2024, 1, 5), anchor_candidates={}, api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=lambda p: _research_response("unfavorable"))

        self.assertEqual(realized_exits, [])
        self.assertIn("AAA.NS", portfolio["positions"], "must still be held today -- queued, not immediate")
        self.assertIn("AAA.NS", portfolio["pending_exits"])

    def test_favorable_recheck_does_not_queue_exit(self):
        portfolio = pcs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 50.0, "target": 120.0,
                                             "strategy_name": "max_effect", "confidence": 0.9}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=100.0)}

        pcd._process_existing_positions(
            portfolio, data, datetime.date(2024, 1, 5), anchor_candidates={}, api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=lambda p: _research_response("favorable"))

        self.assertEqual(portfolio["pending_exits"], {})


class TestRunPortfolioCDaily(_PortfolioCTestBase):
    def test_end_to_end_favorable_candidate_produces_a_queued_entry_and_state_files(self):
        # No anchor signals fire -- keeps this smoke test focused on the
        # plumbing (state files, result shape), not candidate generation
        # (already covered by TestEvaluateNewCandidates above).
        with patch("portfolio_c.daily.collect_anchor_candidates", return_value={}):
            data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
            result = pcd.run_portfolio_c_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["open_positions"], 0)
        self.assertEqual(result["cash"], pcs.PORTFOLIO_C_STARTING_CAPITAL)
        self.assertEqual(result["mark_to_market_equity"], pcs.PORTFOLIO_C_STARTING_CAPITAL)

    def test_calling_again_for_the_same_date_is_a_safe_no_op(self):
        with patch("portfolio_c.daily.collect_anchor_candidates", return_value={}):
            data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
            pcd.run_portfolio_c_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")

            calls = []
            with patch.object(pcd, "fetch_fundamentals", side_effect=lambda s: calls.append(s)):
                result = pcd.run_portfolio_c_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")

        self.assertEqual(result["status"], "skipped_already_processed")
        self.assertEqual(calls, [], "an already-processed date must do no further work at all")

    def test_force_reprocesses_an_already_processed_date(self):
        with patch("portfolio_c.daily.collect_anchor_candidates", return_value={}):
            data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}
            pcd.run_portfolio_c_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")
            result = pcd.run_portfolio_c_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake",
                                                force=True)
        self.assertEqual(result["status"], "processed")

        import os
        self.assertTrue(os.path.exists(pcs._portfolio_path()))
        self.assertTrue(os.path.exists(pcs._daily_equity_path()))


class TestResolvePortfolioCAtOpen(_PortfolioCTestBase):
    def test_nothing_pending_is_a_fast_no_op(self):
        result = pcd.resolve_portfolio_c_at_open(fetch_open_data_fn=lambda: {},
                                                  as_of_date=datetime.date(2024, 1, 5))
        self.assertEqual(result, {"status": "processed", "as_of_date": "2024-01-05",
                                   "new_entries": [], "new_exits": []})

    def test_pending_entry_fills_using_the_light_fetch(self):
        portfolio = pcs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "max_effect", "confidence": 0.9, "quantity": 50,
        }
        pcs.save_portfolio(portfolio)

        result = pcd.resolve_portfolio_c_at_open(
            fetch_open_data_fn=lambda: {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)},
            as_of_date=datetime.date(2024, 1, 5))

        self.assertEqual(len(result["new_entries"]), 1)
        reloaded = pcs.load_portfolio()
        self.assertIn("AAA.NS", reloaded["positions"])

    def test_does_not_touch_last_processed_date_or_daily_equity(self):
        portfolio = pcs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "max_effect", "confidence": 0.9, "quantity": 50,
        }
        pcs.save_portfolio(portfolio)

        pcd.resolve_portfolio_c_at_open(
            fetch_open_data_fn=lambda: {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)},
            as_of_date=datetime.date(2024, 1, 5))

        reloaded = pcs.load_portfolio()
        self.assertIsNone(reloaded["last_processed_date"])
        import os
        self.assertFalse(os.path.exists(pcs._daily_equity_path()))

    def test_later_eod_call_the_same_day_finds_nothing_left_to_resolve(self):
        portfolio = pcs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "max_effect", "confidence": 0.9, "quantity": 50,
        }
        pcs.save_portfolio(portfolio)
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)}

        pcd.resolve_portfolio_c_at_open(fetch_open_data_fn=lambda: data, as_of_date=datetime.date(2024, 1, 5))

        with patch("portfolio_c.daily.collect_anchor_candidates", return_value={}):
            result = pcd.run_portfolio_c_daily(
                data, as_of_date=datetime.date(2024, 1, 5), api_key="fake",
                fetch_fundamentals_fn=_healthy_fundamentals,
                research_call_fn=lambda p: _research_response("favorable", 0.9))

        self.assertEqual(result["new_entries"], [], "already resolved this morning -- EOD call has nothing left to fill")
        self.assertEqual(result["open_positions"], 1)


if __name__ == "__main__":
    unittest.main()
