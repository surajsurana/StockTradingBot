"""
Unit tests for dashboard/state_view.py and dashboard/server.py's access check
-- synthetic state tree, fake registry records, no network. Run with:

    python test_dashboard.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch
from types import SimpleNamespace

from deployment.base import DeploymentStatus, ResearchVerdict
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


def _record(key, name, sid, status=DeploymentStatus.PAPER_TRADING, verdict=ResearchVerdict.PASS,
            family="swing_research published strategy", deployment_status_history=None,
            research_verdict_source=""):
    """Uses the REAL enums, not their string reprs. The fixture used strings until 2026-10-06, which
    silently diverged from production -- view code that called deployment/ helpers expecting a real
    StrategyRecord blew up on `.value` only once such a call was added."""
    return SimpleNamespace(strategy_key=key, display_name=name, strategy_id=sid, deployment_status=status,
                           research_verdict=verdict, primary_experiment_id="EXP-001", strategy_family=family,
                           research_verdict_source=research_verdict_source,
                           deployment_status_history=deployment_status_history or [])


# One scored crypto candidate in the shape roadmap_view reads -- shared by the stale-lock tests.
def _scored_crypto(key, name):
    cand = SimpleNamespace(key=key, name=name, factor_family="Momentum", year=2020, authors="A & B",
                           typical_holding_period="1 month", direction="Long only",
                           known_strengths="s", known_weaknesses="w", horizon_lane="crypto",
                           market="Global", holding_days_min=7, holding_days_max=30)
    return SimpleNamespace(candidate=cand, total_score=8.0, axis_scores={"academic_evidence": 8.0},
                           feasibility_classification="IMPLEMENTABLE", feasibility_reasons=[])


class TestBuildDashboardState(unittest.TestCase):
    def test_assignable_is_simply_what_each_broker_holds(self):
        """The global deployment cap was removed on 2026-10-07: how much is deployed is decided
        strategy by strategy, and an allocation cannot exceed its own broker's balance anyway."""
        from dashboard.state_view import live_capital_view
        view = live_capital_view([], kite_balance=(10_500.0, ""), coindcx_balance=(7_384.0, ""))
        self.assertEqual(view["assignable"], 10_500.0)
        self.assertEqual(view["assignable_crypto"], 7_384.0)

    def test_an_unknown_balance_still_assigns_nothing(self):
        from dashboard.state_view import live_capital_view
        view = live_capital_view([], kite_balance=(None, "token expired"), coindcx_balance=(7_384.0, ""))
        self.assertEqual(view["assignable"], 0.0)
        self.assertEqual(view["assignable_crypto"], 7_384.0)

    def setUp(self):
        self.state_dir, self.logs_dir = _tree()
        self.alpha_paper_trading_since = datetime(2026, 8, 15, 12, 0).timestamp()
        self.records = [_record("alpha", "Alpha", "SW-001", deployment_status_history=[
                            {"from_status": "RESEARCH", "to_status": "PAPER_TRADING", "timestamp": self.alpha_paper_trading_since, "reason": "test"}]),
                        _record("old", "Old", "SW-000", status=DeploymentStatus.ARCHIVED, verdict=ResearchVerdict.REJECT),
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
                                  roadmap=roadmap, research_queues={"india": queue})
        rows = {c["key"]: c["queue"] for c in s["roadmap"]["ready"]}
        self.assertEqual(rows["current_one"], {"state": "current", "in_progress": False, "stale": False,
                          "claimed_days_ago": None, "claimed_at": None, "implemented": False,
                          "backtesting": False, "mode": "backtest"})
        self.assertEqual(rows["resolved_one"], {"state": "resolved", "outcome": "researched", "experiment_id": "EXP-050"})
        self.assertIsNone(rows["untouched"])

        # once the research routine has actually started, in_progress flips to True (2026-09-22: locked
        # until resolved -- the only thing that stops the queue from being freely changed).
        queue["current"]["in_progress"] = True
        s2 = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                   roadmap=roadmap, research_queues={"india": queue})
        rows2 = {c["key"]: c["queue"] for c in s2["roadmap"]["ready"]}
        self.assertEqual(rows2["current_one"], {"state": "current", "in_progress": True,
                          "stale": False, "claimed_days_ago": None, "claimed_at": None,
                          "implemented": False, "backtesting": False, "mode": "backtest"})

    def test_each_lane_reads_its_own_queue_not_indias(self):
        # 2026-10-03: the Research tab used to read only research_queue.json (India), so the crypto lane's
        # live candidate rendered a "Start research" button as if nothing was running. Each candidate's
        # queue state must come from ITS OWN lane's file.
        cand = lambda key, name, lane, market: SimpleNamespace(key=key, name=name, factor_family="Reversal",
            year=2001, authors="A & B", typical_holding_period="1 month", direction="Long only",
            known_strengths="s", known_weaknesses="w", horizon_lane=lane, market=market,
            holding_days_min=30, holding_days_max=30)
        scored = lambda key, name, score, lane, market: SimpleNamespace(candidate=cand(key, name, lane, market),
            total_score=score, axis_scores={"academic_evidence": 8.0},
            feasibility_classification="IMPLEMENTABLE", feasibility_reasons=[])
        roadmap = {"researchable_now": [scored("ind", "India one", 9.0, "swing", "India"),
                                        scored("cry", "Crypto one", 8.0, "crypto", "Global"),
                                        scored("usa", "US one", 7.0, "long_term", "US")],
                   "deferred_pending_data": [], "weights": {}}
        queues = {"india": {"current": {"key": "ind", "in_progress": True}, "history": []},
                  "crypto": {"current": {"key": "cry", "in_progress": False}, "history": []},
                  "us": {"current": None, "history": []}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap, research_queues=queues)
        rows = {c["key"]: c for c in s["roadmap"]["ready"]}
        self.assertEqual([rows[k]["lane"] for k in ("ind", "cry", "usa")], ["india", "crypto", "us"])
        self.assertTrue(rows["ind"]["queue"]["in_progress"])
        self.assertEqual(rows["cry"]["queue"], {"state": "current", "in_progress": False, "stale": False,
                          "claimed_days_ago": None, "claimed_at": None, "implemented": False,
                          "backtesting": False, "mode": "backtest"})
        self.assertIsNone(rows["usa"]["queue"])          # its lane's queue is empty -- still startable

    def test_a_candidate_that_was_never_researched_is_startable_again(self):
        """Superseded and abandoned close a row without any research happening, so the queue picks
        those candidates again -- and the page has to offer the button, or it contradicts the queue
        it is reporting on."""
        cand = lambda key: SimpleNamespace(key=key, name=key, factor_family="Reversal", year=2001,
            authors="A", typical_holding_period="1 month", direction="Long only", known_strengths="s",
            known_weaknesses="w", horizon_lane="swing", market="India",
            holding_days_min=30, holding_days_max=30)
        scored = lambda key, sc: SimpleNamespace(candidate=cand(key), total_score=sc,
            axis_scores={"academic_evidence": 8.0}, feasibility_classification="IMPLEMENTABLE",
            feasibility_reasons=[])
        roadmap = {"researchable_now": [scored("bumped", 9.0), scored("died", 8.0), scored("done", 7.0)],
                   "deferred_pending_data": [], "weights": {}}
        queue = {"current": None, "history": [
            {"key": "bumped", "resolved": "2026-09-22", "outcome": "superseded"},
            {"key": "died", "resolved": "2026-10-07", "outcome": "abandoned"},
            {"key": "done", "resolved": "2026-09-20", "outcome": "researched", "experiment_id": "EXP-050"}]}
        st = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                   roadmap=roadmap, research_queues={"india": queue})
        rows = {c["key"]: c["queue"] for c in st["roadmap"]["ready"]}
        self.assertIsNone(rows["bumped"], "nothing was researched -- it must be startable")
        self.assertIsNone(rows["died"], "nothing was researched -- it must be startable")
        self.assertEqual(rows["done"]["state"], "resolved")   # a real verdict stays closed

    def test_a_claim_is_not_a_run_and_the_three_stages_are_told_apart(self):
        """us_short_term_reversal read "Researching now" for a day while it was actually FINISHED and
        waiting to be merged. A claim only means a routine took the candidate; the routine writes the
        strategy (its sandbox cannot reach Yahoo Finance), and the backtest runs here, nightly, only
        once a human has merged the code. Three stages, and the page called all of them research."""
        cand = SimpleNamespace(key="claimed_one", name="Claimed", factor_family="Reversal", year=2001,
                               authors="A", typical_holding_period="1 month", direction="Long only",
                               known_strengths="s", known_weaknesses="w", horizon_lane="swing",
                               market="India", holding_days_min=30, holding_days_max=30)
        roadmap = {"researchable_now": [SimpleNamespace(candidate=cand, total_score=9.0,
                        axis_scores={"academic_evidence": 8.0},
                        feasibility_classification="IMPLEMENTABLE", feasibility_reasons=[])],
                   "deferred_pending_data": [], "weights": {}}
        queues = {"india": {"current": {"key": "claimed_one", "in_progress": True,
                                        "in_progress_since": "2026-10-08T06:00:09"}, "history": []}}
        no_lock = os.path.join(self.state_dir, "no_such_backtest.lock")   # nothing is running

        def q(implemented):
            with patch("run_queued_backtest.implemented_keys",
                       return_value={"claimed_one"} if implemented else set()),                  patch("run_queued_backtest.LOCK_PATH", no_lock):
                st = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None,
                                           now=self.now, roadmap=roadmap, research_queues=queues)
            return st["roadmap"]["ready"][0]["queue"]

        writing = q(implemented=False)
        self.assertEqual((writing["in_progress"], writing["implemented"], writing["backtesting"]),
                         (True, False, False))                   # being written -- not research yet
        self.assertEqual(writing["claimed_at"], "2026-10-08T06:00:09")   # when, so the page can say so

        merged = q(implemented=True)
        self.assertEqual((merged["implemented"], merged["backtesting"]), (True, False))
        # merged but not running = queued for tonight's backtest, which is NOT "researching now"

    def test_a_claim_nobody_came_back_from_reads_as_stalled_not_as_running(self):
        """The crypto lane said "Researching now" for three days about a cloud run that had died on
        2026-10-04. The page was not wrong about the flag -- it was wrong about what the flag meant,
        because nothing measured how long the claim had been held."""
        roadmap = {"researchable_now": [_scored_crypto("cry", "Crypto one")],
                   "deferred_pending_data": [], "weights": {}}
        queues = {"crypto": {"current": {"key": "cry", "in_progress": True,
                                         "in_progress_since": "2026-10-04T19:05:00"}, "history": []}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None,
                                  now=datetime(2026, 10, 7, 9, 0), roadmap=roadmap,
                                  research_queues=queues)
        q = {c["key"]: c["queue"] for c in s["roadmap"]["ready"]}["cry"]
        self.assertTrue(q["stale"])
        self.assertAlmostEqual(q["claimed_days_ago"], 2.58, places=1)

    def test_a_claim_made_today_is_not_stalled(self):
        roadmap = {"researchable_now": [_scored_crypto("cry", "Crypto one")],
                   "deferred_pending_data": [], "weights": {}}
        queues = {"crypto": {"current": {"key": "cry", "in_progress": True,
                                         "in_progress_since": "2026-10-07T07:00:00"}, "history": []}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None,
                                  now=datetime(2026, 10, 7, 9, 0), roadmap=roadmap,
                                  research_queues=queues)
        q = {c["key"]: c["queue"] for c in s["roadmap"]["ready"]}["cry"]
        self.assertFalse(q["stale"])

    def test_an_unknown_kite_balance_assigns_nothing_rather_than_trusting_the_setting(self):
        # a stale access token is routine, so a failed balance fetch must not read as "the account is
        # empty" OR fall back to the configured pool -- capital must never be assigned against a
        # figure nobody could confirm
        from dashboard.state_view import live_capital_view
        lc = live_capital_view(self.records, kite_balance=(None, "TokenException: stale"))
        self.assertIsNone(lc["balance"])
        self.assertEqual(lc["assignable"], 0.0)
        self.assertEqual(lc["free"], 0.0)
        self.assertIn("stale", lc["balance_error"])

    def test_a_real_fill_is_reported_in_rupees_not_as_a_usdt_conversion(self):
        # the trade happened ON an INR market at a known INR price; showing the USDT reference and
        # deriving rupees described a conversion that never took place, at a price we did not pay
        from dashboard.state_view import _live_fill_overrides
        position = {"live_fill": {"inr_price": 8_387_793.3, "quantity": 0.00031,
                                  "fee_inr": 15.34, "inr_value": 2615.56, "order_id": "2910327372"}}
        out = _live_fill_overrides(position, derived_amount=2519.46)
        self.assertEqual(out["amount"], 2615.56)       # not the derived figure
        self.assertEqual(out["price"], 8_387_793.3)    # the INR price paid, not the USDT reference
        self.assertEqual(out["ccy"], "INR")
        self.assertIsNone(out["fx"])                   # nothing was converted
        self.assertTrue(out["actual_fill"])

    def test_the_fee_is_split_into_fee_and_gst_the_way_the_exchange_reports_it(self):
        # CoinDCX charges a fee plus 18% GST on it and reports only the total (Rs15.34); its own
        # order screen shows Rs13.00 + Rs2.34, and the dashboard must agree line for line
        from dashboard.state_view import _live_fill_overrides
        out = _live_fill_overrides({"live_fill": {"inr_price": 8_387_793.3, "quantity": 0.00031,
                                                  "fee_inr": 15.34, "inr_value": 2615.56}}, 0.0)
        self.assertEqual(out["fee_base"], 13.00)
        self.assertEqual(out["fee_gst"], 2.34)
        self.assertAlmostEqual(out["order_value"], 2600.22, places=1)
        self.assertAlmostEqual(out["order_value"] + out["fee"], out["amount"], places=1)

    def test_a_position_with_no_real_fill_still_derives_as_before(self):
        from dashboard.state_view import _live_fill_overrides
        out = _live_fill_overrides({"quantity": 1.0}, derived_amount=2519.46)
        self.assertEqual(out, {"amount": 2519.46})     # paper books are untouched

    def test_the_order_log_is_exposed_so_cron_runs_are_visible(self):
        # every live run is fired by cron, so a page that only knew about runs it had started itself
        # showed "not run yet" while real orders had been attempted hours earlier
        from dashboard.state_view import live_orders_view
        from deployment.live_executor import order_log_path
        d = tempfile.mkdtemp()
        with open(order_log_path(d), "w", encoding="utf-8") as f:
            f.write(json.dumps({"at": "2026-10-07T08:35:01", "stage": "sent", "symbol": "SOL",
                                "side": "BUY", "quantity": 0.21, "value": 2500.0}) + "\n")
            f.write(json.dumps({"at": "2026-10-07T08:35:02", "stage": "accepted", "symbol": "SOL",
                                "side": "BUY", "quantity": 0.21, "order_id": "cd-9"}) + "\n")
        rows = live_orders_view(d)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["stage"], "accepted")        # newest first
        self.assertEqual(rows[1]["stage"], "sent")

    def test_one_unreadable_log_line_does_not_hide_the_rest(self):
        from dashboard.state_view import live_orders_view
        from deployment.live_executor import order_log_path
        d = tempfile.mkdtemp()
        with open(order_log_path(d), "w", encoding="utf-8") as f:
            f.write('{"at": "2026-10-07T08:35:01", "stage": "sent", "symbol": "SOL"}\n')
            f.write("{ truncated\n")
            f.write('{"at": "2026-10-07T08:35:02", "stage": "accepted", "symbol": "SOL"}\n')
        self.assertEqual(len(live_orders_view(d)), 2)

    def test_no_order_log_is_an_empty_list_not_an_error(self):
        from dashboard.state_view import live_orders_view
        self.assertEqual(live_orders_view(tempfile.mkdtemp()), [])

    def test_the_crypto_cash_is_reported_separately_from_the_equity_cash(self):
        # rupees at Kite cannot buy crypto and rupees at CoinDCX cannot buy shares, so summing them
        # into one "available" figure would overstate what any single order can actually draw on
        from dashboard.state_view import live_capital_view
        view = live_capital_view(self.records, kite_balance=(50_000.0, ""),
                                 coindcx_balance=(10_000.0, ""))
        self.assertEqual(view["balance"], 50_000.0)
        self.assertEqual(view["crypto_balance"], 10_000.0)
        self.assertNotEqual(view["balance"], 60_000.0)       # never added together

    def test_what_is_assignable_is_computed_per_venue(self):
        # a crypto strategy funded from the CoinDCX balance must not be capped by the Kite one.
        # This is the bug that refused a Rs10,000 crypto allocation against a Rs500 equity balance.
        from dashboard.state_view import live_capital_view
        with patch("config.settings.LIVE_CAPITAL_POOL_RUPEES", 10_000, create=True):
            view = live_capital_view(self.records, kite_balance=(500.0, ""),
                                     coindcx_balance=(10_000.0, ""))
        self.assertEqual(view["assignable"], 500.0)             # equity capped by Kite
        self.assertEqual(view["assignable_crypto"], 10_000.0)   # crypto capped by CoinDCX

    def test_over_allocation_is_judged_per_account_not_against_a_combined_total(self):
        # Rs10,000 correctly assigned to a crypto strategy was flagged "more is assigned than is
        # assignable" because it was compared against the Rs500 EQUITY balance. Each account must be
        # judged against its own.
        from dashboard.state_view import live_capital_view
        from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
        pool_g = StrategyRecord(strategy_key="portfolio_g", display_name="Pool G",
                                strategy_family="AI judgment book (crypto, Pool G)",
                                research_verdict=ResearchVerdict.NOT_YET_EVALUATED,
                                deployment_status=DeploymentStatus.PILOT_LIVE)
        root = tempfile.mkdtemp()
        with patch("deployment.settings.STATE_DIR", root),              patch("config.settings.LIVE_CAPITAL_POOL_RUPEES", 10_000, create=True),              patch("deployment.live_allocations.load", return_value={"portfolio_g": 10_000.0}):
            view = live_capital_view([pool_g], kite_balance=(500.0, ""),
                                     coindcx_balance=(10_000.0, ""))
        self.assertEqual(view["allocated_crypto"], 10_000.0)
        self.assertEqual(view["allocated_equity"], 0.0)
        self.assertFalse(view["over_allocated"])        # it fits in the account that holds it

    def test_over_allocation_is_still_caught_within_an_account(self):
        from dashboard.state_view import live_capital_view
        from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
        pool_g = StrategyRecord(strategy_key="portfolio_g", display_name="Pool G",
                                strategy_family="AI judgment book (crypto, Pool G)",
                                research_verdict=ResearchVerdict.NOT_YET_EVALUATED,
                                deployment_status=DeploymentStatus.PILOT_LIVE)
        with patch("config.settings.LIVE_CAPITAL_POOL_RUPEES", 10_000, create=True),              patch("deployment.live_allocations.load", return_value={"portfolio_g": 10_000.0}):
            view = live_capital_view([pool_g], kite_balance=(500.0, ""),
                                     coindcx_balance=(200.0, ""))   # the crypto account shrank
        self.assertTrue(view["over_allocated"])

    def test_an_unknown_crypto_balance_assigns_nothing_rather_than_falling_back(self):
        from dashboard.state_view import live_capital_view
        with patch("config.settings.LIVE_CAPITAL_POOL_RUPEES", 10_000, create=True):
            view = live_capital_view(self.records, kite_balance=(500.0, ""),
                                     coindcx_balance=(None, "down"))
        self.assertEqual(view["assignable_crypto"], 0.0)

    def test_an_unreachable_exchange_is_unknown_not_zero(self):
        # zero reads as "the account is empty", which is the difference between refusing to assign
        # capital and appearing to have none -- the same fail-closed rule the Kite balance follows
        from dashboard.state_view import live_capital_view
        view = live_capital_view(self.records, kite_balance=(50_000.0, ""),
                                 coindcx_balance=(None, "ConnectionError: timed out"))
        self.assertIsNone(view["crypto_balance"])
        self.assertIn("timed out", view["crypto_balance_error"])

    def test_the_crypto_balance_defaults_to_unknown_when_not_supplied(self):
        from dashboard.state_view import live_capital_view
        view = live_capital_view(self.records, kite_balance=(50_000.0, ""))
        self.assertIsNone(view["crypto_balance"])

    def test_every_strategy_row_carries_its_live_gate_and_what_is_blocking_it(self):
        # the Strategies tab's "Live gate" column: the AUTOMATED part of LIVE_PROMOTION_CRITERIA.md
        # (verdict, 60 paper days, 20 closed trades) computed per strategy, with the shortfall named.
        rows = {r["key"]: r for r in self.s["strategies"] if r.get("pilot")}
        self.assertTrue(rows, "paper-trading strategies must carry a pilot gate")
        for r in rows.values():
            p = r["pilot"]
            self.assertEqual(set(p), {"eligible", "state", "reasons", "allocation_pct", "days",
                                      "trades", "backtest_impossible", "score", "parts", "blocked"})
            self.assertTrue(0 <= p["score"] <= 100, p)
            self.assertEqual(p["eligible"], not p["reasons"])      # a blocked row always says why
            self.assertIn(p["state"], ("qualified", "waiting", "not_qualified"))
            self.assertEqual(p["eligible"], p["state"] == "qualified")
            if p["eligible"]:
                self.assertEqual(p["allocation_pct"], 5.0)

    def test_waiting_means_time_fixes_it_and_not_qualified_means_a_decision_does(self):
        # the whole point of the three-way split: "waiting" is only short of days or trades, so it
        # resolves on its own; "not_qualified" is a verdict or status problem that never will.
        rows = {r["key"]: r for r in self.s["strategies"] if r.get("pilot")}
        for r in rows.values():
            p = r["pilot"]
            blocked_on_judgement = any("Verdict" in x or "Deployment Status" in x for x in p["reasons"])
            self.assertEqual(p["state"] == "not_qualified", blocked_on_judgement, r["key"])
        self.assertEqual(rows["old"]["pilot"]["state"], "not_qualified")   # REJECT / ARCHIVED

    def test_the_server_side_gate_recheck_reads_the_books_off_disk(self):
        # /api/live/promote must not trust the gate the page displayed -- a tab can be hours stale, and
        # the request body is only text a client chose to send. pilot_gate_for_key() is that recheck:
        # it counts a strategy's own paper books itself, across every pool it runs in.
        from dashboard.state_view import pilot_gate_for_key
        from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
        root = tempfile.mkdtemp()
        record = StrategyRecord(strategy_key="twin", display_name="Twin", strategy_family="fam",
                                research_verdict=ResearchVerdict.PASS,
                                deployment_status=DeploymentStatus.PAPER_TRADING)
        for pool, count, first in (("paper_trading", 12, "2026-01-05"), ("pool_f", 9, "2026-02-01")):
            book = os.path.join(root, pool, "twin")
            os.makedirs(book)
            with open(os.path.join(book, "trades.jsonl"), "w", encoding="utf-8") as f:
                for i in range(count):
                    f.write(json.dumps({"entry_date": first, "pnl": 1.0}) + "\n")
            with open(os.path.join(book, "daily_equity.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"date": first, "equity": 100000}) + "\n")
        gate = pilot_gate_for_key(record, root, now=datetime(2026, 6, 1))
        self.assertEqual(gate["trades"], 21)                    # BOTH books counted, not just Pool A
        self.assertEqual(gate["days"], 147)                     # from the EARLIEST book's own start
        self.assertTrue(gate["eligible"], gate["reasons"])

    def test_the_recheck_ignores_the_live_book_so_the_gate_stays_about_paper(self):
        # the live book sits a level deeper (live/paper_trading/<key>); counting its trades towards the
        # paper bar would let a strategy's own live activity argue for its own promotion
        from dashboard.state_view import pilot_gate_for_key
        from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
        root = tempfile.mkdtemp()
        live_book = os.path.join(root, "live", "paper_trading", "solo")
        os.makedirs(live_book)
        with open(os.path.join(live_book, "trades.jsonl"), "w", encoding="utf-8") as f:
            for _ in range(40):
                f.write(json.dumps({"entry_date": "2026-01-01"}) + "\n")
        record = StrategyRecord(strategy_key="solo", display_name="Solo", strategy_family="fam",
                                research_verdict=ResearchVerdict.PASS,
                                deployment_status=DeploymentStatus.PAPER_TRADING)
        gate = pilot_gate_for_key(record, root, now=datetime(2026, 6, 1))
        self.assertEqual(gate["trades"], 0)
        self.assertFalse(gate["eligible"])

    def test_a_strategy_with_no_books_at_all_is_not_eligible(self):
        from dashboard.state_view import pilot_gate_for_key
        from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
        record = StrategyRecord(strategy_key="ghost", display_name="Ghost", strategy_family="fam",
                                research_verdict=ResearchVerdict.PASS,
                                deployment_status=DeploymentStatus.PAPER_TRADING)
        gate = pilot_gate_for_key(record, tempfile.mkdtemp(), now=datetime(2026, 6, 1))
        self.assertEqual((gate["days"], gate["trades"]), (0, 0))
        self.assertFalse(gate["eligible"])

    def test_every_ledger_row_says_which_currency_its_price_is_quoted_in(self):
        # price is in the market's own currency, amount is in rupees -- the two numbers sit next to
        # each other on the row, so the row has to carry enough for the page to label them
        for a in self.s["ledger"]:
            self.assertIn(a["ccy"], ("INR", "USD", "USDT"), a)
            self.assertEqual(a["ccy"] == "INR", a["fx"] is None)   # a rupee row converts nothing
            if a["fx"] is not None:
                self.assertGreater(a["fx"], 0)

    def test_a_us_row_quotes_price_in_dollars_and_amount_in_rupees(self):
        us = [a for a in self.s["ledger"] if a.get("pool") == "Pool I"]
        if not us:
            self.skipTest("no Pool I rows in this fixture")
        for a in us:
            self.assertEqual(a["ccy"], "USD")
            # the rupee amount must be the dollar value converted, not the dollar value relabelled
            self.assertAlmostEqual(a["amount"], a["price"] * a["qty"] * a["fx"], delta=1.0)

    def test_a_filled_row_keeps_its_rupee_identity_through_the_fx_stamping(self):
        # the fx loop runs after rows are built and would stamp USDT and a rate back over a trade
        # that happened on an INR market -- the exact thing the fill record exists to correct
        for a in self.s["ledger"]:
            if a.get("actual_fill"):
                self.assertEqual(a["ccy"], "INR", a.get("symbol"))
                self.assertIsNone(a["fx"], a.get("symbol"))

    def test_every_row_says_which_days_rate_it_used(self):
        for a in self.s["ledger"]:
            self.assertIn("fx_date", a)
            self.assertIn("fx_is_today", a)
            if a["fx"] is None:
                self.assertIsNone(a["fx_date"])            # a rupee row converts nothing
            else:
                self.assertTrue(a["fx_date"], a)

    def test_a_closed_trade_locks_to_its_own_exit_date_rate_and_an_open_one_tracks_today(self):
        # a finished trade stopped changing when it closed; converting it at today's rate let
        # currency drift move a completed strategy's track record every day
        from dashboard.state_view import _ledger
        import dashboard.state_view as sv
        history = {"2026-10-01": 80.0, "2026-10-02": 90.0}
        pool_i = {"exists": True, "usdinr": 100.0, "books": [{
            "key": "us1", "display_name": "US One",
            "open_positions": [{"symbol": "AAA", "quantity": 2, "entry_price": 10.0,
                                "entry_date": "2026-10-01", "unbooked_raw": 4.0}]}]}
        with patch.object(sv, "history_for", return_value=history, create=True),              patch("data.usdinr_history.history_for", return_value=history),              patch.object(sv, "_read_jsonl", return_value=[
                 {"symbol": "BBB", "quantity": 1, "entry_price": 10.0, "exit_price": 12.0,
                  "entry_date": "2026-10-01", "exit_date": "2026-10-02", "pnl": 2.0}]):
            rows = _ledger("x", [], {}, [], date(2026, 10, 6), pool_i=pool_i,
                           us_prices={"AAA": 12.0}, us_prev_close={}, prices={}, crypto_prices={},
                           crypto_prev_close={})
        closed = next(r for r in rows if r["symbol"] == "BBB")
        open_ = next(r for r in rows if r["symbol"] == "AAA")
        self.assertEqual((closed["fx"], closed["fx_date"], closed["fx_is_today"]), (90.0, "2026-10-02", False))
        self.assertEqual(closed["amount"], round(12.0 * 1 * 90.0, 2))     # restated at ITS rate
        self.assertEqual(closed["pnl"], round(2.0 * 90.0, 2))
        self.assertEqual((open_["fx"], open_["fx_is_today"]), (100.0, True))   # still open: today
        self.assertEqual(open_["fx_date"], "2026-10-06")

    def test_a_candidate_researched_but_never_promoted_still_appears(self):
        """It was skipped on the assumption that every experiment has a registry record. A
        candidate that was researched and NOT promoted has none -- so us_short_term_reversal's
        PASS (EXP-096) vanished off the page the moment its verdict was attached, which is the
        opposite of what recording a verdict should do."""
        queues = {"us": {"current": None, "history": [
            {"key": "us_short_term_reversal", "name": "Short-Term Reversal (US)", "lane": "us",
             "mode": "backtest", "resolved": "2026-10-10", "outcome": "researched",
             "experiment_id": "EXP-096", "branch": "research-us/us_short_term_reversal"}]}}
        with patch("dashboard.state_view._experiment_summary",
                   return_value={"verdict": "PASS", "trades": 768}):
            st = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None,
                                       now=self.now, roadmap={"researchable_now": [],
                                       "deferred_pending_data": [], "weights": {}},
                                       research_queues=queues)
        row = next((r for r in st["roadmap"]["results"] if r["key"] == "us_short_term_reversal"), None)
        self.assertIsNotNone(row, "a researched candidate must be somewhere on the page")
        self.assertEqual((row["outcome"], row["experiment_id"], row["verdict"]),
                         ("researched", "EXP-096", "PASS"))
        self.assertEqual(row["status"], "")          # researched, never promoted

    def test_a_real_fill_is_measured_against_the_rupees_it_actually_cost(self):
        """THE ENTRY SIDE HAS TO COME FROM THE EXCHANGE TOO. The mark was already CoinDCX's, but
        entry_price is the GLOBAL USDT price at decision time and the fill happened on an INR market
        that trades at a premium to it -- about 3% on 2026-10-10. Subtracting one from the other put
        BTC at +Rs32 while CoinDCX's own screen said -Rs43.77: same coin, same quantity, same mark,
        opposite sign.

        Numbers below are the real 2026-10-10 book. CoinDCX showed invested Rs2,600.21, current
        Rs2,556.44, P&L -Rs43.77."""
        from dashboard.state_view import _ledger
        rate = 96.73
        pool_g = {"exists": True, "usdinr": rate, "open_positions": [{
            "symbol": "BTC", "quantity": 0.00031, "entry_price": 84181.5, "price": 85245.1278,
            "entry_date": "2026-10-07", "unbooked_raw": 0.33,
            "live_fill": {"inr_price": 8387793.3, "quantity": 0.00031, "fee_inr": 15.34,
                          "inr_value": 2615.56, "order_id": "2910327372"}}]}
        rows = _ledger("x", [], {}, [], date(2026, 10, 10), pool_g=pool_g, prices={},
                       crypto_prices={}, crypto_prev_close={})
        btc = next(r for r in rows if r["symbol"] == "BTC" and r["status"] == "Open")
        self.assertEqual(btc["order_value"], 2600.22)            # what CoinDCX calls Invested
        self.assertAlmostEqual(btc["pnl"], -44.03, places=1)     # their screen: -43.77
        self.assertAlmostEqual(btc["pct"], -1.69, places=1)      # their screen: -1.68%

    def test_a_coin_bought_today_books_its_whole_move_today(self):
        """CoinDCX's own convention, and the only one that can agree with it: a position opened
        today has no earlier reference to measure from."""
        from dashboard.state_view import _ledger
        pool_g = {"exists": True, "usdinr": 96.73, "open_positions": [{
            "symbol": "BNB", "quantity": 0.026, "entry_price": 745.53, "price": 773.2854,
            "entry_date": "2026-10-10", "unbooked_raw": 0.72,
            "live_fill": {"inr_price": 74069.3, "quantity": 0.026, "fee_inr": 11.36,
                          "inr_value": 1937.16, "order_id": "2974391022"}}]}
        rows = _ledger("x", [], {}, [], date(2026, 10, 10), pool_g=pool_g, prices={},
                       crypto_prices={}, crypto_prev_close={})
        bnb = next(r for r in rows if r["symbol"] == "BNB" and r["status"] == "Open")
        self.assertEqual(bnb["pnl_today"], bnb["pnl"])
        self.assertAlmostEqual(bnb["pnl"], 19.0, places=0)       # their screen: +20.69

    def test_a_paper_position_with_no_fill_is_left_on_its_own_terms(self):
        """Pool G's PAPER book has no exchange fill to defer to, and re-denominating it would
        restart its record -- a separate decision, deliberately not taken here."""
        from dashboard.state_view import _ledger
        pool_g = {"exists": True, "usdinr": 96.73, "open_positions": [{
            "symbol": "ETH", "quantity": 0.5, "entry_price": 2000.0, "price": 2100.0,
            "entry_date": "2026-10-07", "unbooked_raw": 50.0}]}
        rows = _ledger("x", [], {}, [], date(2026, 10, 10), pool_g=pool_g, prices={},
                       crypto_prices={}, crypto_prev_close={})
        eth = next(r for r in rows if r["symbol"] == "ETH")
        self.assertEqual(eth["pnl"], round(50.0 * 96.73, 2))     # unchanged: the USD book's own P&L

    def test_a_trade_closed_today_books_its_whole_pnl_today(self):
        """The Today's P&L column must add up to the figure in the top right, and that figure
        includes what was booked today. Closed rows carried pnl_today=None, so the one trade that
        actually moved the day read "-" -- BNB, -Rs79.92 booked on 2026-10-09, missing from the day
        altogether because Pool G has no pool summary to carry realised_today.

        Same convention as reporting/pool_summary.py: a trade whose exit date is today booked its
        whole realised P&L today. A trade closed on any earlier day books nothing today."""
        from dashboard.state_view import _ledger
        import dashboard.state_view as sv
        rate = {"2026-10-08": 100.0, "2026-10-09": 100.0}
        pool_i = {"exists": True, "usdinr": 100.0, "books": [{"key": "us1", "display_name": "US One",
                  "open_positions": []}]}
        with patch.object(sv, "history_for", return_value=rate, create=True),              patch("data.usdinr_history.history_for", return_value=rate),              patch.object(sv, "_read_jsonl", return_value=[
                 {"symbol": "TODAY", "quantity": 1, "entry_price": 10.0, "exit_price": 12.0,
                  "entry_date": "2026-10-08", "exit_date": "2026-10-09", "pnl": 2.0},
                 {"symbol": "YESTERDAY", "quantity": 1, "entry_price": 10.0, "exit_price": 9.0,
                  "entry_date": "2026-10-07", "exit_date": "2026-10-08", "pnl": -1.0}]):
            rows = _ledger("x", [], {}, [], date(2026, 10, 9), pool_i=pool_i,
                           us_prices={}, us_prev_close={}, prices={}, crypto_prices={},
                           crypto_prev_close={})
        booked = next(r for r in rows if r["symbol"] == "TODAY")
        older = next(r for r in rows if r["symbol"] == "YESTERDAY")
        self.assertEqual(booked["pnl_today"], booked["pnl"])   # all of it, booked today
        self.assertIsNone(older["pnl_today"])                  # closed yesterday: nothing today

    def test_net_pnl_is_summed_from_the_pnl_tabs_own_lines_across_every_pool(self):
        # the Strategies tab's new net breakdown must not be a second calculation: two views
        # disagreeing about a strategy's profit is worse than not showing net at all
        from dashboard.state_view import attach_net_pnl
        rows = [{"key": "twin", "pnl": 900.0}, {"key": "lonely", "pnl": 10.0}, {"key": "nobook"}]
        statement = [
            {"key": "twin", "pool": "Pool A", "realised": 500.0, "unrealised": 100.0,
             "charges": 60.0, "gst": 9.0, "tax": 30.0, "detail": {"stt": 40.0, "dp": 20.0}},
            {"key": "twin", "pool": "Pool F", "realised": 300.0, "unrealised": 0.0,
             "charges": 40.0, "gst": 7.0, "tax": 20.0, "detail": {"stt": 25.0, "dp": 15.0}},
            {"key": "lonely", "pool": "Pool E", "realised": 10.0, "unrealised": 0.0,
             "charges": 2.0, "gst": 0.0, "tax": 3.0, "detail": {"crypto_fee": 2.0}},
        ]
        attach_net_pnl(rows, statement)
        twin = rows[0]["net"]
        self.assertEqual(twin["gross"], 900.0)                 # matches the Total P&L column
        self.assertEqual((twin["charges"], twin["gst"], twin["tax"]), (100.0, 16.0, 50.0))
        self.assertEqual(twin["net"], 900.0 - 166.0)
        self.assertEqual(twin["detail"], {"stt": 65.0, "dp": 35.0})   # components summed, not listed twice
        self.assertEqual(sorted(twin["pools"]), ["Pool A", "Pool F"])
        self.assertEqual(rows[1]["net"]["net"], 5.0)
        # a strategy with no book gets NO block: "nothing to show" is not the same claim as "zero"
        self.assertNotIn("net", rows[2])

    def test_each_pool_of_a_strategy_gets_its_own_net(self):
        # the Strategies row shows net; its expanded per-pool sub-rows must too, or the same
        # strategy reports two different profits depending on whether you expanded it
        from dashboard.state_view import attach_net_pnl
        rows = [{"key": "twin", "pnl": 900.0,
                 "pools_breakdown": [{"pool": "Pool A", "pnl": 600.0}, {"pool": "Pool F", "pnl": 300.0}]}]
        attach_net_pnl(rows, [
            {"key": "twin", "pool": "Pool A", "realised": 600.0, "unrealised": 0.0,
             "charges": 60.0, "gst": 9.0, "tax": 31.0, "detail": {}},
            {"key": "twin", "pool": "Pool F", "realised": 300.0, "unrealised": 0.0,
             "charges": 40.0, "gst": 7.0, "tax": 13.0, "detail": {}}])
        self.assertEqual([p["net"] for p in rows[0]["pools_breakdown"]], [500.0, 240.0])
        self.assertEqual(rows[0]["net"]["net"], 740.0)        # the row equals the sum of its pools

    def test_a_pool_with_no_statement_line_gets_no_net_rather_than_a_zero(self):
        from dashboard.state_view import attach_net_pnl
        rows = [{"key": "k", "pnl": 10.0, "pools_breakdown": [{"pool": "Pool Z", "pnl": 10.0}]}]
        attach_net_pnl(rows, [])
        self.assertNotIn("net", rows[0]["pools_breakdown"][0])

    def test_on_real_state_a_strategys_net_equals_the_sum_of_its_pool_nets(self):
        checked = 0
        for r in self.s["strategies"]:
            pools = [p for p in (r.get("pools_breakdown") or []) if "net" in p]
            if not r.get("net") or not pools:
                continue
            self.assertAlmostEqual(sum(p["net"] for p in pools), r["net"]["net"], places=1, msg=r["key"])
            checked += 1
        self.assertTrue(checked, "no strategy had per-pool nets to reconcile")

    def test_the_charge_breakdown_excludes_gst_and_total(self):
        # equity_costs.book_costs() returns detail with "gst" and "total" mixed in among the six real
        # components. Both are carried as their own fields, so letting them through the breakdown
        # counts GST twice and draws a row labelled "total" inside the itemisation.
        from dashboard.state_view import CHARGE_COMPONENTS, attach_net_pnl
        rows = [{"key": "k"}]
        attach_net_pnl(rows, [{"key": "k", "pool": "Pool A", "realised": 100.0, "unrealised": 0.0,
                               "charges": 30.0, "gst": 5.0, "tax": 0.0,
                               "detail": {"stt": 20.0, "dp": 10.0, "gst": 5.0, "total": 35.0}}])
        self.assertEqual(rows[0]["net"]["detail"], {"stt": 20.0, "dp": 10.0})
        self.assertEqual(rows[0]["net"]["gst"], 5.0)           # still reported, once, on its own
        self.assertEqual(rows[0]["net"]["net"], 65.0)

    def test_the_itemised_charges_add_up_to_the_charges_total_on_real_state(self):
        from dashboard.state_view import CHARGE_COMPONENTS
        checked = 0
        for r in self.s["strategies"]:
            n = r.get("net")
            if not n or not n["detail"]:
                continue
            for component in n["detail"]:
                self.assertIn(component, CHARGE_COMPONENTS, r["key"])
            self.assertAlmostEqual(sum(n["detail"].values()), n["charges"], places=0, msg=r["key"])
            checked += 1
        self.assertTrue(checked, "no strategy carried an itemised charge breakdown")

    def test_net_pnl_on_the_real_state_always_reconciles_with_the_pnl_tab(self):
        by_key = {}
        for line in self.s["statement"]:
            agg = by_key.setdefault(line["key"], [0.0, 0.0])
            agg[0] += line["realised"] + line["unrealised"]
            agg[1] += line["charges"] + line["gst"] + line["tax"]
        checked = 0
        for r in self.s["strategies"]:
            if not r.get("net"):
                continue
            gross, costs = by_key[r["key"]]
            self.assertAlmostEqual(r["net"]["gross"], gross, places=1, msg=r["key"])
            self.assertAlmostEqual(r["net"]["net"], gross - costs, places=1, msg=r["key"])
            checked += 1
        self.assertTrue(checked, "no strategy carried a net block")

    def test_the_promotion_score_is_dominated_by_whether_it_actually_makes_money(self):
        # a strategy can have a PASS verdict, every paper day and every trade and still lose money
        # after charges (SW-016 does). A score that weighted time heavily would call that nearly
        # ready, which is the single most misleading thing this column could do.
        from dashboard.state_view import promotion_score
        full = {"days": 90, "trades": 40}
        winner = promotion_score(full, {"net": 500.0}, "PASS")
        loser = promotion_score(full, {"net": -500.0}, "PASS")
        self.assertEqual(winner["score"], 100)
        self.assertEqual(loser["score"], 60)                 # everything but profitability
        self.assertEqual(loser["parts"]["net"], 0)

    def test_a_rejected_verdict_scores_zero_however_good_the_paper_run(self):
        from dashboard.state_view import promotion_score
        got = promotion_score({"days": 900, "trades": 900}, {"net": 99999.0}, "REJECT")
        self.assertEqual(got["score"], 0)
        self.assertEqual(set(got["parts"].values()), {0})
        self.assertIn("REJECT", got["blocked"])

    def test_the_score_credits_time_and_trades_only_up_to_the_floor(self):
        from dashboard.state_view import promotion_score
        at_floor = promotion_score({"days": 60, "trades": 20}, {"net": 1.0}, "PASS")
        far_past = promotion_score({"days": 600, "trades": 200}, {"net": 1.0}, "PASS")
        self.assertEqual(at_floor["score"], far_past["score"])   # the floor is a floor, not a race
        self.assertEqual(at_floor["score"], 100)
        half = promotion_score({"days": 30, "trades": 10}, {"net": 1.0}, "PASS")
        self.assertEqual((half["parts"]["days"], half["parts"]["trades"]), (7.5, 7.5))

    def test_an_unevaluated_strategy_scores_on_the_verdict_only_where_a_backtest_is_impossible(self):
        from dashboard.state_view import promotion_score
        impossible = promotion_score({"days": 60, "trades": 20, "backtest_impossible": True},
                                     {"net": 1.0}, "NOT_YET_EVALUATED")
        merely_absent = promotion_score({"days": 60, "trades": 20, "backtest_impossible": False},
                                        {"net": 1.0}, "NOT_YET_EVALUATED")
        self.assertEqual(impossible["parts"]["verdict"], 15.0)
        self.assertEqual(merely_absent["parts"]["verdict"], 0.0)

    def test_a_strategy_that_never_traded_scores_zero_for_profitability(self):
        from dashboard.state_view import promotion_score
        got = promotion_score({"days": 0, "trades": 0}, None, "PASS")
        self.assertEqual(got["score"], 30)                   # the verdict, and nothing else earned
        self.assertEqual(got["parts"]["net"], 0)

    def test_every_real_strategy_scores_within_range_and_adds_up(self):
        for r in self.s["strategies"]:
            p = r.get("pilot")
            if not p:
                continue
            self.assertTrue(0 <= p["score"] <= 100, r["key"])
            self.assertEqual(p["score"], round(sum(p["parts"].values())), r["key"])
            if r["verdict"] == "REJECT":
                self.assertEqual(p["score"], 0, r["key"])

    def test_the_live_gate_blocks_a_rejected_strategy_by_verdict(self):
        old = next(r for r in self.s["strategies"] if r["key"] == "old")   # REJECT / ARCHIVED
        self.assertFalse(old["pilot"]["eligible"])
        self.assertTrue(any("Research Verdict" in x for x in old["pilot"]["reasons"]))

    def test_completed_research_runs_are_listed_newest_first_across_every_lane(self):
        # 2026-10-05: a candidate disappears from the ranked table once it is promoted, so the queue's
        # own history is the only surviving record of what an auto run actually produced.
        cand = lambda key, name, lane, market: SimpleNamespace(key=key, name=name, factor_family="Reversal",
            year=2001, authors="A & B", typical_holding_period="1 month", direction="Long only",
            known_strengths="s", known_weaknesses="w", horizon_lane=lane, market=market,
            holding_days_min=30, holding_days_max=30)
        scored = lambda key, name, lane, market: SimpleNamespace(candidate=cand(key, name, lane, market),
            total_score=9.0, axis_scores={"academic_evidence": 8.0},
            feasibility_classification="IMPLEMENTABLE", feasibility_reasons=[])
        roadmap = {"researchable_now": [scored("ind", "India one", "swing", "India")],
                   "deferred_pending_data": [], "weights": {}}
        row = lambda key, outcome, resolved, exp=None: {
            "key": key, "name": key.upper(), "queued": "2026-10-01", "started": "2026-10-01",
            "started_by": "auto", "mode": "backtest", "resolved": resolved, "outcome": outcome,
            "experiment_id": exp, "branch": f"research/{key}"}
        queues = {"india": {"current": None, "history": [row("old", "researched", "2026-10-02", "EXP-999"),
                                                         row("bumped", "superseded", "2026-10-04")]},
                  "crypto": {"current": None, "history": [row("cry", "skipped", "2026-10-06")]},
                  "us": {"current": None, "history": [row("open_one", None, None)]}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap, research_queues=queues)
        res = s["roadmap"]["results"]
        by_key = {r["key"]: r for r in res}
        self.assertEqual(res[0]["key"], "old")                    # newest experiment first by default
        self.assertNotIn("open_one", by_key)                      # still running -- not a result yet
        # the run's own experiment wins over the registry's canonical one for the same strategy
        self.assertEqual((by_key["old"]["experiment_id"], by_key["old"]["exp_no"]), ("EXP-999", 999))
        self.assertEqual(by_key["cry"]["label"], "Crypto")
        # a row that closed without ever being backtested carries no numbers, and says why
        self.assertEqual((by_key["bumped"]["outcome"], by_key["bumped"]["experiment_id"]), ("superseded", None))
        self.assertEqual(by_key["bumped"]["metrics"], {})
        self.assertIsNone(by_key["bumped"]["exp_no"])
        self.assertEqual(by_key["bumped"]["status"], "")          # never registered, so no Paper/Live/Off
        # queue rows that never produced an experiment sort last, not first
        self.assertGreater(res.index(by_key["bumped"]), res.index(by_key["old"]))

    def test_results_include_research_done_before_the_queue_existed(self):
        # 2026-10-05: listing only queue history made the tab read as "nothing has ever been
        # researched" when 29 registry strategies carry real experiments. Those must show too,
        # marked as pre-queue so it stays clear which ones the automation actually produced.
        roadmap = {"researchable_now": [], "deferred_pending_data": [], "weights": {}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap, research_queues={})
        res = s["roadmap"]["results"]
        self.assertTrue(res, "registry strategies carrying an experiment must appear")
        self.assertTrue(all(r["source"] == "earlier" for r in res))
        self.assertTrue(all(r["experiment_id"] for r in res))
        self.assertTrue(all(r["outcome"] == "researched" for r in res))
        self.assertTrue(all(r["resolved"] is None for r in res))   # no invented dates

    def test_lane_strip_covers_all_three_queues_including_an_idle_one(self):
        cand = lambda key, name, lane, market: SimpleNamespace(key=key, name=name, factor_family="Reversal",
            year=2001, authors="A & B", typical_holding_period="1 month", direction="Long only",
            known_strengths="s", known_weaknesses="w", horizon_lane=lane, market=market,
            holding_days_min=30, holding_days_max=30)
        scored = lambda key, name, score, lane, market, feas="IMPLEMENTABLE", reasons=(): SimpleNamespace(
            candidate=cand(key, name, lane, market), total_score=score, axis_scores={"academic_evidence": 8.0},
            feasibility_classification=feas, feasibility_reasons=list(reasons))
        roadmap = {"researchable_now": [scored("ind", "India one", 9.0, "swing", "India"),
                                        scored("cry", "Crypto one", 8.0, "crypto", "Global")],
                   "deferred_pending_data": [scored("usa", "US blocked", 7.0, "long_term", "US",
                                                    "NOT_CURRENTLY_IMPLEMENTABLE", ["Requires 'dividends'"])],
                   "weights": {}}
        queues = {"india": {"current": {"key": "ind", "in_progress": True}, "history": []}}
        s = build_dashboard_state(self.state_dir, self.logs_dir, self.records, {}, None, now=self.now,
                                  roadmap=roadmap, research_queues=queues)
        lanes = {l["lane"]: l for l in s["roadmap"]["lanes"]}
        self.assertEqual(set(lanes), {"india", "crypto", "us"})       # all three, even an empty one
        self.assertEqual(lanes["india"]["current"]["key"], "ind")
        self.assertIsNone(lanes["crypto"]["current"])                 # researchable, but nothing picked up yet
        self.assertEqual(lanes["crypto"]["ready"], 1)
        self.assertEqual((lanes["us"]["ready"], lanes["us"]["deferred"]), (0, 1))   # US: blocked, so it says so
        self.assertEqual(s["roadmap"]["deferred"][0]["lane"], "us")
        # each lane quotes its OWN routine's day -- India Sunday, crypto Tuesday, US Thursday
        self.assertEqual([lanes[k]["next_run"]["iso"] for k in ("india", "crypto", "us")],
                         ["2026-09-13T00:00", "2026-09-15T00:00", "2026-09-17T00:00"])

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


class TestAPromotedStrategyOnTheStrategiesTab(unittest.TestCase):
    """What the Strategies tab says about a strategy that has gone live. Both of these were wrong on
    2026-10-07, the day after SW-030 became the first real-money strategy: its Pool column went blank
    and its capital read Rs10,043 against Rs10,000 funded."""

    def setUp(self):
        from types import SimpleNamespace

        from deployment.base import DeploymentStatus, ResearchVerdict
        from reporting.pool_g import build_pool_g
        self.root = tempfile.mkdtemp()
        book_dir = os.path.join(self.root, "pool_g")
        os.makedirs(book_dir)
        # exactly the shape of the real live book: Rs10,000 funded on a day USDINR was 96.53, so the
        # USD-denominated engine stored 103.5974
        with open(os.path.join(book_dir, "portfolio.json"), "w") as f:
            json.dump({"cash": 103.5974, "starting_capital": 103.5974, "positions": {},
                       "last_processed_date": "2026-10-07"}, f)
        open(os.path.join(book_dir, "trades.jsonl"), "w").close()
        self.pool_g = build_pool_g(self.root, {}, usdinr=96.94, today=date(2026, 10, 7))
        self.record = SimpleNamespace(strategy_key="portfolio_g", display_name="Portfolio G (AI judgment book)",
                                      strategy_id="SW-030", deployment_status=DeploymentStatus.PILOT_LIVE,
                                      research_verdict=ResearchVerdict.NOT_YET_EVALUATED,
                                      primary_experiment_id="", research_verdict_source="",
                                      strategy_family="AI judgment book (crypto, Pool G)")
        self.funded = {"portfolio_g": 10_000.0}

    def _rows(self, mode):
        from dashboard.state_view import strategies_view
        return {r["key"]: r for r in strategies_view([self.record], "pool_d_vwap_fade",
                                                     pool_g=self.pool_g, state_dir=self.root, mode=mode,
                                                     live_allocations=self.funded)}

    def test_going_live_does_not_blank_the_pool_column(self):
        # the pool is a property of the strategy, not of its status
        self.assertEqual(self._rows("live")["portfolio_g"]["pool"], "Pool G")
        self.assertEqual(self._rows("paper")["portfolio_g"]["pool"], "Pool G")

    def test_live_capital_is_the_money_at_the_broker_not_a_round_trip_through_the_dollar(self):
        """Pool G's engine is denominated in USD. Funding it with Rs10,000 stored 103.5974 USD at that
        day's rate; converting back at 96.94 read Rs10,043. That 0.4%% is a move in USDINR, not a gain,
        and the account at CoinDCX holds Rs10,000 either way."""
        row = self._rows("live")["portfolio_g"]
        self.assertEqual(row["capital"], 10_000.0)
        self.assertEqual([p["capital"] for p in row["pools_breakdown"]], [10_000.0])

    def test_paper_mode_still_reports_the_book_itself(self):
        # the override is about real money at a broker; a paper book has none, so it is untouched
        row = self._rows("paper")["portfolio_g"]
        self.assertNotEqual(row["capital"], 10_000.0)
        self.assertAlmostEqual(row["capital"], 103.5974 * 96.94, delta=1.0)   # the Rs10,043 reading

    def test_an_archived_strategy_still_has_no_pool(self):
        from deployment.base import DeploymentStatus
        self.record.deployment_status = DeploymentStatus.ARCHIVED
        self.assertEqual(self._rows("live")["portfolio_g"]["pool"], "-")


class TestTheNotificationsTab(unittest.TestCase):
    """An underfunded book produces no trades and no errors -- it looks exactly like a book that had
    no signals. Suraj, the evening SW-016 went live with Rs10,000 against a Rs12,469 floor: "for
    tomorrow i want to see the calls its makng and they dont pass due to capital not being enough."""

    def _log(self, rows):
        import json
        d = tempfile.mkdtemp()
        from deployment.live_executor import order_log_path
        os.makedirs(d, exist_ok=True)
        with open(order_log_path(d), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + chr(10))
        return d

    def test_a_decision_the_book_could_not_act_on_is_reported(self):
        from dashboard.state_view import notifications_view
        d = self._log([{"at": "2026-10-08T15:41:02", "stage": "not_taken", "strategy": "sw016",
                        "symbol": "SBIN.NS", "side": "BUY", "quantity": 3, "value": 720,
                        "reasons": ["Rs720 is below the Rs12,469 a trade needs"]}])
        view = notifications_view(d)
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["rows"][0]["symbol"], "SBIN.NS")

    def test_a_guard_refusal_is_reported_in_the_same_timeline(self):
        from dashboard.state_view import notifications_view
        d = self._log([{"at": "2026-10-08T20:35:00", "stage": "refused", "strategy": "g",
                        "reasons": ["The NSE is closed right now"]}])
        self.assertEqual(notifications_view(d)["count"], 1)

    def test_a_placed_order_is_not_a_notification(self):
        """It happened. The Orders tab is where things that happened live; this tab is only for
        things that did not."""
        from dashboard.state_view import notifications_view
        d = self._log([{"at": "2026-10-08T09:36:00", "stage": "accepted", "strategy": "sw016",
                        "symbol": "SBIN.NS", "side": "BUY", "quantity": 94},
                       {"at": "2026-10-08T09:36:01", "stage": "sent", "strategy": "sw016"}])
        self.assertEqual(notifications_view(d)["count"], 0)

    def test_causes_are_grouped_commonest_first(self):
        # one recurring reason reads very differently from twenty unrelated ones
        from dashboard.state_view import notifications_view
        d = self._log([{"at": "2026-10-08T15:41:0%d" % i, "stage": "not_taken", "strategy": "sw016",
                        "reasons": ["too small -- the book cannot carry it"]} for i in range(3)]
                      + [{"at": "2026-10-08T20:35:00", "stage": "refused", "strategy": "g",
                          "reasons": ["The NSE is closed"]}])
        view = notifications_view(d)
        self.assertEqual(view["by_reason"][0], {"reason": "too small", "count": 3})
        self.assertEqual(view["by_reason"][1]["count"], 1)

    def test_an_empty_log_is_not_an_error(self):
        from dashboard.state_view import notifications_view
        view = notifications_view(tempfile.mkdtemp())
        self.assertEqual((view["count"], view["rows"], view["by_reason"]), (0, [], []))


class TestNextResearchRun(unittest.TestCase):
    """Each lane's routine fires at midnight IST at the START of its own weekday -- moved there from
    19:00 on 2026-10-07 so an unattended run that takes hours happens while nobody is working."""

    def test_saturday_points_at_midnight_tonight(self):
        from dashboard.state_view import next_research_run
        r = next_research_run(datetime(2026, 9, 26, 12, 30))         # a Saturday
        self.assertEqual((r["iso"], r["label"]), ("2026-09-27T00:00", "Sun 27 Sep, 12:00 am IST"))

    def test_sunday_itself_points_at_the_next_one_because_midnight_has_passed(self):
        from dashboard.state_view import next_research_run
        self.assertEqual(next_research_run(datetime(2026, 9, 27, 0, 1))["iso"], "2026-10-04T00:00")
        self.assertEqual(next_research_run(datetime(2026, 9, 27, 19, 1))["iso"], "2026-10-04T00:00")

    def test_midweek_points_at_the_coming_sunday(self):
        from dashboard.state_view import next_research_run
        self.assertEqual(next_research_run(datetime(2026, 9, 30, 9, 0))["label"], "Sun 4 Oct, 12:00 am IST")

    def test_each_lane_keeps_its_own_weekday(self):
        from dashboard.state_view import next_research_run
        wed = datetime(2026, 9, 30, 9, 0)
        self.assertEqual([next_research_run(wed, lane)["iso"] for lane in ("india", "crypto", "us")],
                         ["2026-10-04T00:00", "2026-10-06T00:00", "2026-10-01T00:00"])


if __name__ == "__main__":
    unittest.main()
