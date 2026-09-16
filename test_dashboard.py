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

from dashboard.state_view import AGENTS, DESKS, FLOWS, SCHEDULE, build_dashboard_state
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
    _write(os.path.join(state, "paper_trading_legacy", "old_big", "portfolio.json"), {
        "cash": 20000.0, "starting_capital": 1000000.0, "last_processed_date": "2026-09-10",
        "positions": {"L.NS": {"entry_price": 200.0, "quantity": 100, "stop_loss": 184.0, "entry_date": "2026-09-10"}},
    })
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
             "exit_price": 1005, "quantity": 100, "reason": "stop_loss",
             "entry_timestamp": "2026-09-10T09:40:00", "exit_timestamp": "2026-09-10T10:05:00"},
            # a record written before fills were time-stamped: both legs must still appear
            {"symbol": "OLDREC", "pnl": 40.0, "exit_date": "2026-09-10", "direction": "BUY", "entry_price": 10,
             "exit_price": 10.4, "quantity": 100, "reason": "target"}], jsonl=True)
    _write(os.path.join(state, "pool_e", "crypto_trend_timing", "portfolio.json"), {
        "cash": 800.0, "starting_capital": 1000.0, "last_processed_date": "2026-09-10",
        "positions": {"BTC": {"entry_price": 80000.0, "quantity": 0.0025, "stop_loss": 64000.0,
                              "entry_date": "2026-09-10"}},
    })
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, "paper_trading.log"), "w", encoding="utf-8") as f:
        f.write("[alpha] processed\n")
    return state, logs


def _record(key, name, sid, status="DeploymentStatus.PAPER_TRADING", verdict="ResearchVerdict.PASS",
            family="swing_research published strategy"):
    return SimpleNamespace(strategy_key=key, display_name=name, strategy_id=sid, deployment_status=status,
                           research_verdict=verdict, primary_experiment_id="EXP-001", strategy_family=family)


class TestBuildDashboardState(unittest.TestCase):
    def setUp(self):
        self.state_dir, self.logs_dir = _tree()
        self.records = [_record("alpha", "Alpha", "SW-001"),
                        _record("old", "Old", "SW-000", status="DeploymentStatus.ARCHIVED", verdict="ResearchVerdict.REJECT"),
                        _record("crypto_trend_timing", "Crypto Trend Timing", "SW-020",
                                family="crypto research published strategy")]
        self.now = datetime(2026, 9, 10, 11, 0)
        self.s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {"X.NS": 104.0, "LT": 3890.0},
                                       "2026-09-10T10:55", now=self.now, crypto_prices={"BTC": 84000.0}, usdinr=100.0,
                                       prev_close={"X.NS": 101.0}, crypto_prev_close={"BTC": 82000.0})

    def test_pool_e_is_its_own_pool_not_a_pool_a_book(self):
        self.assertNotIn("crypto_trend_timing", [b["key"] for b in self.s["books"]])
        f = self.s["pool_e"]
        self.assertTrue(f["exists"])
        self.assertEqual(f["books"][0]["sid"], "SW-020")
        self.assertAlmostEqual(f["usdt"]["unbooked"]["raw"], 10.0)
        self.assertAlmostEqual(f["usdt"]["unbooked"]["post_tax"], 5.24)
        self.assertAlmostEqual(f["inr"]["unbooked"]["post_tax"], 524.0)
        self.assertAlmostEqual(self.s["overall"]["capital"],
                               91000 + 100000 + 100000 + self.s["pool_d"]["capital"] + 100000.0)   # + F: 1,000 USDT x 100
        self.assertAlmostEqual(self.s["overall"]["unrealised"], 2000.0 - 50.0 + 524.0)
        buys = [r for r in self.s["ledger"] if r["pool"] == "Pool E"]
        self.assertEqual(len(buys), 1)
        self.assertEqual((buys[0]["kind"], buys[0]["time"], buys[0]["symbol"]), ("Crypto", "05:30", "BTC"))
        self.assertAlmostEqual(buys[0]["amount"], 20000.0)
        self.assertIn("pool_e", [j["id"] for j in self.s["schedule"]])

    def test_strategies_tab_lists_every_pool_with_briefs(self):
        from dashboard.state_view import STRATEGY_BRIEFS
        rows = {r["key"]: r for r in self.s["strategies"]}
        self.assertEqual(rows["alpha"]["pool"], "Pool A")
        self.assertEqual(rows["crypto_trend_timing"]["pool"], "Pool E")
        self.assertEqual(rows["old"]["pool"], "-")
        self.assertEqual((rows["portfolio_b"]["type"], rows["portfolio_c"]["pool"], rows["pool_d_vwap_fade"]["verdict"]), ("AI", "Pool C", "REJECT"))
        self.assertTrue(all(str(rows[k]["sid"]) for k in ("portfolio_b", "portfolio_c", "pool_d_vwap_fade")))
        self.assertTrue(rows["crypto_trend_timing"]["brief"])
        self.assertTrue(rows["crypto_trend_timing"]["how"]["entry"])
        self.assertEqual([v["pool"] for v in rows["alpha"]["variants"]], [])          # no Pool F twin in this fixture
        self.assertEqual(rows["alpha"]["pool"], "Pool A")
        from dashboard.state_view import STRATEGY_HOW
        self.assertEqual([k for k in STRATEGY_BRIEFS if k not in STRATEGY_HOW], [])   # every brief has a how-it-trades
        from deployment.deployment_manager import list_strategies
        missing = [r.strategy_key for r in list_strategies() if r.strategy_key not in STRATEGY_BRIEFS]
        self.assertEqual(missing, [])   # every real registry strategy has a plain-language brief

    def test_only_paper_trading_strategies_become_pool_a_books_and_a1_is_absent(self):
        keys = [b["key"] for b in self.s["books"] if b["pool"] == "A"]
        self.assertEqual(keys, ["alpha"])
        self.assertEqual({b["pool"] for b in self.s["books"]}, {"A", "B", "C"})
        self.assertNotIn("A1", self.s["pools"])
        self.assertEqual(self.s["overall"]["positions"], 2)   # X.NS + Pool E's BTC; the legacy L.NS position is not counted

    def test_ledger_has_every_position_and_every_trade_newest_first(self):
        acts = self.s["ledger"]
        self.assertEqual([(a["date"], a["time"], a["action"], a["symbol"]) for a in acts],
                         [("2026-09-10", "10:35", "BUY", "LT"), ("2026-09-10", "10:05", "BUY", "SBIN"),
                          ("2026-09-10", "05:30", "BUY", "BTC"), ("2026-09-10", "", "SELL", "OLDREC"),
                          ("2026-09-09", "09:30", "SELL", "Y"),      # alpha's closed trade from the 9th: history, not just today
                          ("2026-09-01", "", "BUY", "X")])
        sbin = acts[1]
        self.assertEqual((sbin["pnl"], sbin["note"], sbin["status"], sbin["held_days"], sbin["fill_today"]), (-500.0, "stop loss", "Closed", 0, True))
        self.assertAlmostEqual(acts[0]["amount"], 3900.0 * 5)
        self.assertEqual((acts[0]["status"], acts[0]["pnl_today"]), ("Open", -50.0))   # intraday: today's move = its P&L
        y = acts[4]
        self.assertEqual((y["pool"], y["status"], y["fill_today"], y["held_days"], y["pnl"]), ("Pool A", "Closed", False, None, -1000.0))
        x = acts[5]
        self.assertEqual((x["pool"], x["status"], x["bought_on"], x["held_days"], x["fill_today"]),
                         ("Pool A", "Open", "2026-09-01", 9, False))
        self.assertAlmostEqual(x["pnl"], (104.0 - 100.0) * 500)   # current P&L at the latest quote
        self.assertAlmostEqual(x["pct"], 4.0)
        self.assertAlmostEqual(x["pnl_today"], (104.0 - 101.0) * 500)   # vs yesterday's close of 101
        self.assertEqual(len(self.s["desks"]), len(DESKS))
        self.assertTrue(all(a["desk"] in {d["id"] for d in DESKS} for a in AGENTS))

    def test_capital_is_cash_plus_deployed_minus_realised(self):
        a = self.s["pools"]["A"]
        self.assertAlmostEqual(a["capital"], 40000 + 50000 - (-1000))       # 91,000 (a 9k wind-down would show here)
        self.assertAlmostEqual(self.s["pool_d"]["capital"], 99500 + 3900 * 5 - (-500 + 40))
        self.assertAlmostEqual(self.s["overall"]["capital"],
                               91000 + 100000 + 100000 + self.s["pool_d"]["capital"] + 100000.0)
        self.assertEqual(self.s["mode"], "paper")

    def test_roadmap_view_drops_candidates_already_in_the_registry(self):
        cand = lambda key, name: SimpleNamespace(key=key, name=name, factor_family="Reversal", year=2001,
                                                 authors="A & B", typical_holding_period="1 month",
                                                 direction="Long only", known_strengths="s", known_weaknesses="w")
        scored = lambda key, name, score, feas="IMPLEMENTABLE", reasons=(): SimpleNamespace(
            candidate=cand(key, name), total_score=score, axis_scores={"academic_evidence": 8.0},
            feasibility_classification=feas, feasibility_reasons=list(reasons))
        roadmap = {"researchable_now": [scored("alpha", "Already built", 9.0), scored("new_idea", "New idea", 7.5)],
                   "deferred_pending_data": [scored("needs_data", "Needs data", 6.0, "NOT_CURRENTLY_IMPLEMENTABLE",
                                                    ["Requires 'x'"])], "weights": {}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap)
        self.assertEqual([(c["rank"], c["key"]) for c in s["roadmap"]["ready"]], [(1, "new_idea")])
        self.assertEqual([c["key"] for c in s["roadmap"]["deferred"]], ["needs_data"])
        self.assertEqual(s["roadmap"]["deferred"][0]["blockers"], ["Requires 'x'"])

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
        self.assertAlmostEqual(d["open_positions"][0]["unbooked"], (3890.0 - 3900.0) * 5)   # long, price down
        self.assertTrue(d["open_positions"][0]["priced"])
        self.assertAlmostEqual(d["unrealised"], -50.0)
        self.assertAlmostEqual(self.s["overall"]["unrealised"], 2000.0 - 50.0 + 524.0)   # X.NS gain + LT loss + BTC post-tax
        self.assertEqual(len(d["todays_trades"]), 2)
        self.assertAlmostEqual(d["realised_today"], -500.0)
        self.assertEqual(d["symbols_with_context"], 2)
        self.assertAlmostEqual(d["cash"], 99500.0)

    def test_schedule_marks_todays_log_and_registry_and_static_content(self):
        sched = {j["id"]: j for j in self.s["schedule"]}
        self.assertTrue(sched["eod_a"]["last_log_write"].startswith(datetime.now().date().isoformat()))
        self.assertIsNone(sched["summary"]["last_log_write"])
        self.assertEqual(sched["eod_a"]["tail"], ["[alpha] processed"])
        self.assertEqual([r["sid"] for r in self.s["registry"]], ["SW-001", "SW-000", "SW-020"])
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
