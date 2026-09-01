"""
Tests for portfolio_b/daily.py -- the full agent-driven daily cycle.
Mirrors test_portfolio_c_daily.py's structure; USE_NEWS_AGENT patched
off throughout so these never make a real network/news call.
"""

import datetime
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import portfolio_b.daily as pbd
import portfolio_b.state as pbs
from config import settings as agent_settings


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


class _PortfolioBTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_state = patch.object(pbs, "PORTFOLIO_B_STATE_DIR", self.tmpdir)
        self.patcher_state.start()
        self.patcher_news = patch.object(agent_settings, "USE_NEWS_AGENT", False)
        self.patcher_news.start()

    def tearDown(self):
        self.patcher_state.stop()
        self.patcher_news.stop()
        shutil.rmtree(self.tmpdir)


class TestEvaluateNewCandidates(_PortfolioBTestBase):
    def test_favorable_watchlist_symbol_is_queued_not_filled_today(self):
        portfolio = pbs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}

        pbd._evaluate_new_candidates(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake", watchlist=["AAA.NS"],
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: _research_response("favorable", 0.9))

        self.assertIn("AAA.NS", portfolio["pending_entries"])
        self.assertEqual(portfolio["positions"], {})
        self.assertEqual(portfolio["cash"], pbs.PORTFOLIO_B_STARTING_CAPITAL)

    def test_unfavorable_watchlist_symbol_is_rejected_not_queued(self):
        portfolio = pbs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}

        entries = pbd._evaluate_new_candidates(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake", watchlist=["AAA.NS"],
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: _research_response("unfavorable", 0.9))

        self.assertEqual(portfolio["pending_entries"], {})
        self.assertIn("rejections", entries[0])

    def test_queued_entry_uses_the_synthetic_signal_stop_loss(self):
        portfolio = pbs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5, base_price=100.0)}

        pbd._evaluate_new_candidates(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake", watchlist=["AAA.NS"],
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: _research_response("favorable", 0.9))

        self.assertAlmostEqual(portfolio["pending_entries"]["AAA.NS"]["stop_loss"], 92.0)

    def test_symbol_already_held_is_skipped(self):
        portfolio = pbs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 92.0, "target": 116.0,
                                             "strategy_name": "portfolio_b_watchlist", "confidence": 1.0}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5)}

        calls = []
        pbd._evaluate_new_candidates(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake", watchlist=["AAA.NS"],
            fetch_fundamentals_fn=_healthy_fundamentals,
            research_call_fn=lambda p: calls.append(p) or _research_response("favorable", 0.9))
        self.assertEqual(calls, [])

    def test_decisions_matched_back_by_symbol_not_by_position(self):
        portfolio = pbs.load_portfolio()
        data = {"AAA.NS": _multi_day_df("2024-01-01", 5, base_price=100.0),
                "BBB.NS": _multi_day_df("2024-01-01", 5, base_price=200.0)}

        def research_call_fn(prompt):
            if "AAA.NS" in prompt:
                return _research_response("favorable", 0.55)
            return _research_response("favorable", 0.95)

        pbd._evaluate_new_candidates(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake", watchlist=["AAA.NS", "BBB.NS"],
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=research_call_fn)

        self.assertEqual(portfolio["pending_entries"]["AAA.NS"]["confidence"], 0.55)
        self.assertEqual(portfolio["pending_entries"]["BBB.NS"]["confidence"], 0.95)


class TestProcessExistingPositions(_PortfolioBTestBase):
    def test_stop_loss_hit_closes_position_same_day(self):
        portfolio = pbs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 90.0, "target": 120.0,
                                             "strategy_name": "portfolio_b_watchlist", "confidence": 1.0}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=85.0)}

        realized_exits, decision_entries = pbd._process_existing_positions(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=lambda p: _research_response("neutral"))

        self.assertEqual(len(realized_exits), 1)
        self.assertEqual(realized_exits[0]["exit_reason"], "stop_loss")
        self.assertNotIn("AAA.NS", portfolio["positions"])

    def test_unfavorable_recheck_queues_exit_not_immediate(self):
        portfolio = pbs.load_portfolio()
        portfolio["positions"]["AAA.NS"] = {"direction": "BUY", "entry_price": 100.0,
                                             "entry_date": "2024-01-01", "quantity": 10,
                                             "stop_loss": 50.0, "target": 120.0,
                                             "strategy_name": "portfolio_b_watchlist", "confidence": 1.0}
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=100.0)}

        realized_exits, decision_entries = pbd._process_existing_positions(
            portfolio, data, datetime.date(2024, 1, 5), api_key="fake",
            fetch_fundamentals_fn=_healthy_fundamentals, research_call_fn=lambda p: _research_response("unfavorable"))

        self.assertEqual(realized_exits, [])
        self.assertIn("AAA.NS", portfolio["positions"])
        self.assertIn("AAA.NS", portfolio["pending_exits"])


class TestResolvePending(_PortfolioBTestBase):
    def test_pending_entry_fills_at_next_real_open(self):
        portfolio = pbs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "portfolio_b_watchlist", "confidence": 1.0, "quantity": 50,
        }
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)}

        new_entries, new_exits = pbd._resolve_pending(portfolio, data, datetime.date(2024, 1, 5))

        self.assertEqual(len(new_entries), 1)
        self.assertIn("AAA.NS", portfolio["positions"])
        self.assertEqual(portfolio["cash"], pbs.PORTFOLIO_B_STARTING_CAPITAL - 50 * 101.0)

    def test_pending_entry_abandoned_if_gap_crosses_stop(self):
        portfolio = pbs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "portfolio_b_watchlist", "confidence": 1.0, "quantity": 50,
        }
        data = {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=85.0)}

        new_entries, new_exits = pbd._resolve_pending(portfolio, data, datetime.date(2024, 1, 5))
        self.assertEqual(new_entries, [])
        self.assertNotIn("AAA.NS", portfolio["positions"])
        self.assertEqual(portfolio["cash"], pbs.PORTFOLIO_B_STARTING_CAPITAL)


class TestRunPortfolioBDaily(_PortfolioBTestBase):
    def test_end_to_end_no_watchlist_activity_produces_clean_result(self):
        data = {}
        result = pbd.run_portfolio_b_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["open_positions"], 0)
        self.assertEqual(result["cash"], pbs.PORTFOLIO_B_STARTING_CAPITAL)

        import os
        self.assertTrue(os.path.exists(pbs._portfolio_path()))
        self.assertTrue(os.path.exists(pbs._daily_equity_path()))

    def test_calling_again_for_the_same_date_is_a_safe_no_op(self):
        data = {}
        pbd.run_portfolio_b_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")

        calls = []
        with patch.object(pbd, "fetch_fundamentals", side_effect=lambda s: calls.append(s)):
            result = pbd.run_portfolio_b_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")

        self.assertEqual(result["status"], "skipped_already_processed")
        self.assertEqual(calls, [])

    def test_force_reprocesses_an_already_processed_date(self):
        data = {}
        pbd.run_portfolio_b_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake")
        result = pbd.run_portfolio_b_daily(data, as_of_date=datetime.date(2024, 1, 5), api_key="fake", force=True)
        self.assertEqual(result["status"], "processed")


class TestResolvePortfolioBAtOpen(_PortfolioBTestBase):
    def test_nothing_pending_is_a_fast_no_op(self):
        result = pbd.resolve_portfolio_b_at_open(fetch_open_data_fn=lambda: {},
                                                  as_of_date=datetime.date(2024, 1, 5))
        self.assertEqual(result, {"status": "processed", "as_of_date": "2024-01-05",
                                   "new_entries": [], "new_exits": []})

    def test_pending_entry_fills_using_the_light_fetch(self):
        portfolio = pbs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "portfolio_b_watchlist", "confidence": 1.0, "quantity": 50,
        }
        pbs.save_portfolio(portfolio)

        result = pbd.resolve_portfolio_b_at_open(
            fetch_open_data_fn=lambda: {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)},
            as_of_date=datetime.date(2024, 1, 5))

        self.assertEqual(len(result["new_entries"]), 1)
        self.assertIn("AAA.NS", pbs.load_portfolio()["positions"])

    def test_does_not_touch_last_processed_date(self):
        portfolio = pbs.load_portfolio()
        portfolio["pending_entries"]["AAA.NS"] = {
            "direction": "BUY", "stop_loss": 90.0, "target": 120.0,
            "signal_date": "2024-01-04", "signal_price": 100.0,
            "strategy_name": "portfolio_b_watchlist", "confidence": 1.0, "quantity": 50,
        }
        pbs.save_portfolio(portfolio)

        pbd.resolve_portfolio_b_at_open(
            fetch_open_data_fn=lambda: {"AAA.NS": _multi_day_df("2024-01-01", 10, base_price=101.0)},
            as_of_date=datetime.date(2024, 1, 5))

        self.assertIsNone(pbs.load_portfolio()["last_processed_date"])


if __name__ == "__main__":
    unittest.main()
