"""
Unit tests for dashboard/state_view.py and dashboard/server.py's access check
-- synthetic state tree, fake registry records, no network. Run with:

    python test_dashboard.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

from dashboard.state_view import AGENTS, FLOWS, SCHEDULE, build_dashboard_state
from dashboard.server import is_authorized


def _write(path, payload, jsonl=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if jsonl:
            for row in payload:
                f.write(json.dumps(row) + "\n")
        else:
            json.dump(payload, f)


def _tree():
    root = tempfile.mkdtemp()
    state, logs = os.path.join(root, "state"), os.path.join(root, "logs")
    _write(os.path.join(state, "paper_trading", "alpha", "portfolio.json"), {
        "cash": 40000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-10",
        "positions": {"X.NS": {"entry_price": 100.0, "quantity": 500, "stop_loss": 92.0, "entry_date": "2026-09-01"}},
    })
    _write(os.path.join(state, "paper_trading", "alpha", "trades.jsonl"),
           [{"symbol": "Y.NS", "pnl": -1000.0, "exit_date": "2026-09-09", "entry_price": 50, "exit_price": 45,
             "quantity": 200, "exit_reason": "stop_loss"}], jsonl=True)
    _write(os.path.join(state, "portfolio_b", "portfolio.json"),
           {"cash": 100000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-10", "positions": {}})
    _write(os.path.join(state, "portfolio_c", "portfolio.json"),
           {"cash": 100000.0, "starting_capital": 100000.0, "last_processed_date": "2026-09-09", "positions": {}})
    _write(os.path.join(state, "pool_d", "portfolio.json"), {
        "last_processed_date": "2026-09-10", "starting_capital": 100000.0, "cash": 99500.0,
        "positions": {"LT": {"direction": "BUY", "entry_price": 3900.0, "stop_loss": 3880.0, "target": 3940.0,
                             "quantity": 5, "entry_timestamp": "2026-09-10T10:35:00"}},
        "realized_pnl_today": -500.0, "trades_today_by_symbol": {"SBIN": 1}, "context_by_symbol": {"LT": {}, "SBIN": {}},
    })
    _write(os.path.join(state, "pool_d", "trades.jsonl"),
           [{"symbol": "SBIN", "pnl": -500.0, "exit_date": "2026-09-10", "direction": "SELL", "entry_price": 1000,
             "exit_price": 1005, "quantity": 100, "reason": "stop_loss"}], jsonl=True)
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, "paper_trading.log"), "w", encoding="utf-8") as f:
        f.write("[alpha] processed\n")
    return state, logs


def _record(key, name, sid, status="DeploymentStatus.PAPER_TRADING", verdict="ResearchVerdict.PASS"):
    return SimpleNamespace(strategy_key=key, display_name=name, strategy_id=sid, deployment_status=status,
                           research_verdict=verdict, primary_experiment_id="EXP-001", strategy_family="swing")


class TestBuildDashboardState(unittest.TestCase):
    def setUp(self):
        self.state_dir, self.logs_dir = _tree()
        self.records = [_record("alpha", "Alpha", "SW-001"),
                        _record("old", "Old", "SW-000", status="DeploymentStatus.ARCHIVED", verdict="ResearchVerdict.REJECT")]
        self.now = datetime(2026, 9, 10, 11, 0)
        self.s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {"X.NS": 104.0},
                                       "2026-09-10T10:55", now=self.now)

    def test_only_paper_trading_strategies_become_pool_a_books(self):
        keys = [b["key"] for b in self.s["books"] if b["pool"] == "A"]
        self.assertEqual(keys, ["alpha"])

    def test_positions_detail_and_book_totals(self):
        alpha = next(b for b in self.s["books"] if b["key"] == "alpha")
        self.assertEqual(alpha["sid"], "SW-001")
        self.assertEqual(alpha["positions_detail"][0]["symbol"], "X")
        self.assertAlmostEqual(alpha["positions_detail"][0]["unbooked"], 2000.0)
        self.assertAlmostEqual(alpha["positions_detail"][0]["pct"], 4.0)
        self.assertTrue(alpha["positions_detail"][0]["priced"])
        self.assertEqual(alpha["recent_trades"][0]["symbol"], "Y")
        self.assertAlmostEqual(alpha["realised"], -1000.0)

    def test_pool_d_live_view(self):
        d = self.s["pool_d"]
        self.assertEqual(d["open_positions"][0]["symbol"], "LT")
        self.assertEqual(len(d["todays_trades"]), 1)
        self.assertAlmostEqual(d["realised_today"], -500.0)
        self.assertEqual(d["symbols_with_context"], 2)
        self.assertAlmostEqual(d["cash"], 99500.0)

    def test_schedule_marks_todays_log_and_registry_and_static_content(self):
        sched = {j["id"]: j for j in self.s["schedule"]}
        self.assertTrue(sched["eod_a"]["last_log_write"].startswith(datetime.now().date().isoformat()))
        self.assertIsNone(sched["summary"]["last_log_write"])
        self.assertEqual(sched["eod_a"]["tail"], ["[alpha] processed"])
        self.assertEqual([r["sid"] for r in self.s["registry"]], ["SW-001", "SW-000"])
        self.assertEqual(self.s["agents"], AGENTS)
        self.assertEqual(self.s["flows"], FLOWS)
        self.assertEqual(len(SCHEDULE), len(self.s["schedule"]))
        self.assertTrue(self.s["market_open"])   # Thursday 11:00

    def test_serialisable(self):
        json.dumps(self.s)


class TestAccessKey(unittest.TestCase):
    def test_open_when_no_key_configured(self):
        self.assertTrue(is_authorized({}, "", ""))

    def test_query_param_or_cookie_required_when_configured(self):
        self.assertFalse(is_authorized({}, "", "s3cret"))
        self.assertFalse(is_authorized({"key": ["wrong"]}, "", "s3cret"))
        self.assertTrue(is_authorized({"key": ["s3cret"]}, "", "s3cret"))
        self.assertTrue(is_authorized({}, "other=1; dash_key=s3cret", "s3cret"))
        self.assertFalse(is_authorized({}, "dash_key=nope", "s3cret"))


if __name__ == "__main__":
    unittest.main()
