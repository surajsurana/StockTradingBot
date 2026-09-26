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
            family="swing_research published strategy", deployment_status_history=None):
    return SimpleNamespace(strategy_key=key, display_name=name, strategy_id=sid, deployment_status=status,
                           research_verdict=verdict, primary_experiment_id="EXP-001", strategy_family=family,
                           deployment_status_history=deployment_status_history or [])


class TestBuildDashboardState(unittest.TestCase):
    def setUp(self):
        self.state_dir, self.logs_dir = _tree()
        self.alpha_paper_trading_since = datetime(2026, 8, 15, 12, 0).timestamp()
        self.records = [_record("alpha", "Alpha", "SW-001", deployment_status_history=[
                            {"from_status": "RESEARCH", "to_status": "PAPER_TRADING", "timestamp": self.alpha_paper_trading_since, "reason": "test"}]),
                        _record("old", "Old", "SW-000", status="DeploymentStatus.ARCHIVED", verdict="ResearchVerdict.REJECT"),
                        _record("crypto_trend_timing", "Crypto Trend Timing", "SW-020",
                                family="crypto research published strategy"),
                        _record("portfolio_g", "Portfolio G (AI judgment book, crypto)", "SW-030",
                                family="AI judgment book (crypto, Pool G)")]   # the real registry's family string -- doesn't start with "crypto" or "swing_research"
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
        # portfolio_g is a registry strategy like B, C and D's, not a generic Pool A/E one --
        # it must show as Pool G even though it comes through the registry, not the synthetic fallback row.
        self.assertEqual(rows["portfolio_g"]["pool"], "Pool G")
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

    def test_strategies_tab_shows_capital_and_total_pnl_per_strategy(self):
        rows = {r["key"]: r for r in self.s["strategies"]}
        # alpha: cash 40,000 + deployed 50,000 (500 X.NS @ 100) - realised -1,000 = 91,000 capital;
        # P&L = realised -1,000 + unrealised 2,000 (500 @ +4) = 1,000.
        self.assertAlmostEqual(rows["alpha"]["capital"], 91000.0)
        self.assertAlmostEqual(rows["alpha"]["pnl"], 1000.0)
        # crypto_trend_timing: Pool E book, USDT converted to rupees at the fixture's 100 rate --
        # capital 1,000 USDT x 100, GROSS P&L 10 USDT x 100 (every dashboard tab shows gross; fees and
        # tax live on the P&L tab, see test_statement_*).
        self.assertAlmostEqual(rows["crypto_trend_timing"]["capital"], 100000.0)
        self.assertAlmostEqual(rows["crypto_trend_timing"]["pnl"], 1000.0)
        # old: ARCHIVED, no book anywhere in the fixture -- never allocated, not zero.
        self.assertIsNone(rows["old"]["capital"])
        self.assertIsNone(rows["old"]["pnl"])

    def test_strategies_tab_shows_closed_trades(self):
        rows = {r["key"]: r for r in self.s["strategies"]}
        self.assertEqual(rows["alpha"]["closed_trades"], 1)   # one closed trade, Y.NS, a loss
        # crypto_trend_timing: a Pool E book exists (one open BTC position) but no trades.jsonl
        # file at all -- 0 closed trades, not None (None means "never had a book").
        self.assertEqual(rows["crypto_trend_timing"]["closed_trades"], 0)
        # old: ARCHIVED, no book anywhere -- genuinely never traded, not zero.
        self.assertIsNone(rows["old"]["closed_trades"])

    def test_pool_breakdown_lists_each_pool_separately_for_a_twin_strategy(self):
        from dashboard.state_view import _strategy_pool_breakdown
        root = tempfile.mkdtemp()
        # Pool A has been trading since 2026-09-07; Pool F is a twin book added much later,
        # 2026-09-16 -- each book's OWN started date must reflect that, not a shared date
        # copied from the registry's single strategy-level PAPER_TRADING transition.
        _write(os.path.join(root, "paper_trading", "beta", "trades.jsonl"),
              [{"pnl": 100.0, "entry_date": "2026-09-07"}], jsonl=True)
        _write(os.path.join(root, "pool_f", "beta", "trades.jsonl"),
              [{"pnl": -50.0, "entry_date": "2026-09-16"}, {"pnl": 20.0, "entry_date": "2026-09-16"}], jsonl=True)
        books = [{"key": "beta", "pool": "A", "capital": 91000.0, "realised": 100.0, "unrealised": 0.0},
                {"key": "beta", "pool": "F", "capital": 199000.0, "realised": -30.0, "unrealised": 500.0}]
        breakdown = _strategy_pool_breakdown("beta", books, root, [], {}, {}, {})
        self.assertEqual(len(breakdown), 2)
        a, f = breakdown[0], breakdown[1]
        self.assertEqual((a["pool"], a["capital"], a["pnl"], a["closed_trades"], a["started"]),
                         ("Pool A", 91000.0, 100.0, 1, "2026-09-07"))
        self.assertEqual((f["pool"], f["capital"], f["pnl"], f["closed_trades"], f["started"]),
                         ("Pool F", 199000.0, 470.0, 2, "2026-09-16"))

    def test_book_started_falls_back_to_an_open_positions_entry_date(self):
        from dashboard.state_view import _book_started
        root = tempfile.mkdtemp()
        book_dir = os.path.join(root, "pool_f", "gamma")
        _write(os.path.join(book_dir, "portfolio.json"),
              {"cash": 1000.0, "positions": {"X.NS": {"entry_date": "2026-09-16", "entry_price": 100, "quantity": 1}}})
        self.assertEqual(_book_started(book_dir, []), "2026-09-16")
        self.assertIsNone(_book_started(os.path.join(root, "nonexistent"), []))

    def test_statement_lines_carry_gross_charges_gst_tax_and_tds_per_book(self):
        lines = {(l["pool"], l["key"]): l for l in self.s["statement"]}
        alpha = lines[("Pool A", "alpha")]
        self.assertAlmostEqual(alpha["realised"], -1000.0)
        self.assertAlmostEqual(alpha["unrealised"], 2000.0)
        self.assertGreater(alpha["charges"], 0)
        self.assertGreater(alpha["gst"], 0)
        # book gain is +1,000 gross; tax is 20.8% of what is left after charges and GST
        self.assertAlmostEqual(alpha["tax"], round(max(0.0, 1000.0 - alpha["charges"] - alpha["gst"]) * 0.208, 2), places=1)
        self.assertIsNone(alpha["tds"])          # no TDS on a resident's equity gains
        self.assertAlmostEqual(alpha["capital"] + alpha["realised"] + alpha["unrealised"],
                               alpha["cash"] + alpha["deployed"] + alpha["unrealised"])   # the balance sheet balances
        crypto = lines[("Pool E", "crypto_trend_timing")]
        # 10 USDT gross on the open BTC position x 100; tax 31.2% of the gain, fees both sides, 1% TDS on the sale
        self.assertAlmostEqual(crypto["unrealised"], 1000.0)
        self.assertAlmostEqual(crypto["tax"], 312.0)
        self.assertGreater(crypto["charges"], 0)
        self.assertEqual(crypto["gst"], 0.0)
        self.assertAlmostEqual(crypto["tds"], 210.0)   # 1% of the Rs.21,000 sale value (0.0025 BTC at 84,000 USDT x 100)
        self.assertAlmostEqual(crypto["capital"] + crypto["realised"] + crypto["unrealised"],
                               crypto["cash"] + crypto["deployed"] + crypto["unrealised"])
        d = lines[("Pool D", "pool_d_vwap_fade")]
        self.assertEqual(d["type"], "Intraday")
        self.assertAlmostEqual(d["tax_rate"], 0.312)

    def test_partly_sold_position_links_its_legs_and_is_flagged_partial(self):
        from dashboard.state_view import _attach_lifecycles
        base = {"pool": "Pool F", "book": "Alpha", "symbol": "ABC", "symbol_key": "ABC.NS", "bought_on": "2026-09-16", "action": "BUY"}
        rows = [
            {**base, "status": "Open", "date": "2026-09-16", "time": "", "qty": 21, "price": 100.0, "amount": 2100.0, "cost": 2100.0, "pnl": 50.0},
            {**base, "status": "Closed", "date": "2026-09-17", "time": "09:30", "qty": 21, "price": 105.0, "amount": 2205.0,
             "entry_price": 100.0, "cost": 2100.0, "pnl": 105.0, "note": "partial profit", "action": "SELL"},
            # a different purchase of the same stock, fully sold -> its own lifecycle, not partial
            {**base, "bought_on": "2026-09-01", "status": "Closed", "date": "2026-09-05", "time": "", "qty": 10, "price": 90.0, "amount": 900.0,
             "entry_price": 80.0, "cost": 800.0, "pnl": 100.0, "note": "signal exit", "action": "SELL"},
        ]
        life = _attach_lifecycles(rows)
        self.assertEqual(rows[0]["life"], rows[1]["life"])
        self.assertNotEqual(rows[0]["life"], rows[2]["life"])
        self.assertTrue(rows[0]["partial"] and rows[1]["partial"])
        self.assertFalse(rows[2]["partial"])
        L = life[rows[0]["life"]]
        self.assertEqual((L["qty_bought"], L["qty_sold"], L["qty_left"]), (42, 21, 21))
        self.assertAlmostEqual(L["entry"]["value"], 4200.0)
        self.assertEqual((L["realised"], L["unrealised"], L["total"]), (105.0, 50.0, 155.0))
        self.assertEqual(life[rows[2]["life"]]["open"], None)

    def test_strategies_tab_shows_when_paper_trading_started(self):
        from datetime import date
        rows = {r["key"]: r for r in self.s["strategies"]}
        # a strategy "started" when its first book did (its earliest trade or position), not when it was approved:
        # alpha was approved for paper trading on the registry date but its book's first entry (X.NS) is on 2026-09-01
        approved = date.fromtimestamp(self.alpha_paper_trading_since).isoformat()
        book_start = min(p["started"] for p in rows["alpha"]["pools_breakdown"] if p["started"])
        self.assertEqual(rows["alpha"]["started"], book_start)
        self.assertEqual(book_start, "2026-09-01")
        self.assertLess(approved, book_start)
        # crypto_trend_timing's fixture record never went through set_deployment_status (no approval date), but its
        # book has a position entered on 2026-09-10, and that is when it started.
        self.assertEqual(rows["crypto_trend_timing"]["started"], "2026-09-10")

    def test_a_book_started_when_it_began_looking_not_when_it_found_its_first_trade(self):
        import json
        import tempfile
        from dashboard.state_view import _book_started
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "portfolio.json"), "w") as f:
            json.dump({"positions": {"X.NS": {"entry_date": "2026-09-09"}}}, f)
        with open(os.path.join(d, "daily_equity.jsonl"), "w") as f:
            f.write(json.dumps({"date": "2026-09-01", "cash": 1, "equity": 1}) + "\n" + json.dumps({"date": "2026-09-02", "cash": 1, "equity": 1}) + "\n")
        self.assertEqual(_book_started(d, [{"entry_date": "2026-09-12"}]), "2026-09-01")       # first run on the 1st, first trade on the 9th
        os.remove(os.path.join(d, "daily_equity.jsonl"))
        self.assertEqual(_book_started(d, []), "2026-09-09")                                      # no equity log: fall back to the first trade

    def test_only_paper_trading_strategies_become_pool_a_books_and_a1_is_absent(self):
        keys = [b["key"] for b in self.s["books"] if b["pool"] == "A"]
        self.assertEqual(keys, ["alpha"])          # portfolio_g's family doesn't start with "swing_research" -- no phantom Pool A book for it
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
        self.assertAlmostEqual(x["pct_today"], 3.0)   # today's move (1500) as a % of cost (50000), separate from pct's lifetime 4.0
        self.assertIsNone(y["pct_today"])   # y never set pnl_today (a trade closed on an earlier day) -- pct_today follows suit, not a crash
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
        cand = lambda key, name, lane="swing": SimpleNamespace(key=key, name=name, factor_family="Reversal", year=2001,
                                                 authors="A & B", typical_holding_period="1 month",
                                                 direction="Long only", known_strengths="s", known_weaknesses="w",
                                                 horizon_lane=lane, market="India", holding_days_min=30, holding_days_max=30)
        scored = lambda key, name, score, feas="IMPLEMENTABLE", reasons=(), lane="swing": SimpleNamespace(
            candidate=cand(key, name, lane), total_score=score, axis_scores={"academic_evidence": 8.0},
            feasibility_classification=feas, feasibility_reasons=list(reasons))
        roadmap = {"researchable_now": [scored("alpha", "Already built", 9.0), scored("new_idea", "New idea", 7.5, lane="crypto")],
                   "deferred_pending_data": [scored("needs_data", "Needs data", 6.0, "NOT_CURRENTLY_IMPLEMENTABLE",
                                                    ["Requires 'x'"])], "weights": {}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap)
        self.assertEqual([(c["rank"], c["key"]) for c in s["roadmap"]["ready"]], [(1, "new_idea")])
        self.assertEqual(s["roadmap"]["ready"][0]["horizon_lane"], "crypto")   # every research lane, not just swing
        self.assertEqual((s["roadmap"]["ready"][0]["holding_days_min"], s["roadmap"]["ready"][0]["holding_days_max"]), (30, 30))
        self.assertEqual([c["key"] for c in s["roadmap"]["deferred"]], ["needs_data"])
        self.assertEqual(s["roadmap"]["deferred"][0]["blockers"], ["Requires 'x'"])

    def test_roadmap_view_marks_the_currently_queued_and_resolved_candidates(self):
        cand = lambda key, name: SimpleNamespace(key=key, name=name, factor_family="Reversal", year=2001,
                                                 authors="A & B", typical_holding_period="1 month",
                                                 direction="Long only", known_strengths="s", known_weaknesses="w",
                                                 horizon_lane="swing", market="India", holding_days_min=30, holding_days_max=30)
        scored = lambda key, name, score: SimpleNamespace(candidate=cand(key, name), total_score=score,
            axis_scores={"academic_evidence": 8.0}, feasibility_classification="IMPLEMENTABLE", feasibility_reasons=[])
        roadmap = {"researchable_now": [scored("current_one", "In research now", 9.0), scored("resolved_one", "Already tried", 8.0),
                                        scored("untouched", "Not started", 7.0)],
                   "deferred_pending_data": [], "weights": {}}
        queue = {"current": {"key": "current_one"}, "history": [
            {"key": "resolved_one", "resolved": "2026-09-20", "outcome": "researched", "experiment_id": "EXP-050"}]}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap, research_queue=queue)
        rows = {c["key"]: c["queue"] for c in s["roadmap"]["ready"]}
        self.assertEqual(rows["current_one"], {"state": "current", "in_progress": False, "mode": "backtest"})
        self.assertEqual(rows["resolved_one"], {"state": "resolved", "outcome": "researched", "experiment_id": "EXP-050"})
        self.assertIsNone(rows["untouched"])

        # once the research routine has actually started, in_progress flips to True (2026-09-22: locked
        # until resolved -- the only thing that stops the queue from being freely changed).
        queue["current"]["in_progress"] = True
        s2 = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                   roadmap=roadmap, research_queue=queue)
        rows2 = {c["key"]: c["queue"] for c in s2["roadmap"]["ready"]}
        self.assertEqual(rows2["current_one"], {"state": "current", "in_progress": True, "mode": "backtest"})

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
        self.assertEqual([r["sid"] for r in self.s["registry"]], ["SW-001", "SW-000", "SW-020", "SW-030"])
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


class TestNextResearchRun(unittest.TestCase):
    """The Strategy Implementer routine fires every Sunday at 19:00 IST -- the Research tab shows when."""

    def test_saturday_points_at_tomorrow_evening(self):
        from dashboard.state_view import next_research_run
        r = next_research_run(datetime(2026, 9, 26, 12, 30))         # a Saturday
        self.assertEqual((r["iso"], r["label"]), ("2026-09-27T19:00", "Sun 27 Sep, 7:00 pm IST"))

    def test_sunday_before_seven_is_today_and_after_seven_is_next_week(self):
        from dashboard.state_view import next_research_run
        self.assertEqual(next_research_run(datetime(2026, 9, 27, 18, 59))["iso"], "2026-09-27T19:00")
        self.assertEqual(next_research_run(datetime(2026, 9, 27, 19, 1))["iso"], "2026-10-04T19:00")

    def test_midweek_points_at_the_coming_sunday(self):
        from dashboard.state_view import next_research_run
        self.assertEqual(next_research_run(datetime(2026, 9, 30, 9, 0))["label"], "Sun 4 Oct, 7:00 pm IST")


if __name__ == "__main__":
    unittest.main()
