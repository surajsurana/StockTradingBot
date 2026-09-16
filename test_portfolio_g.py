"""
Unit tests for Pool G, the crypto AI judgment book: the LLM reply parser,
the mechanical stop-loss check (always first, never the model's call),
BUY/SELL sizing and execution, the full cycle's order of operations, and
the pre/post-tax reporting. No real network or Claude calls -- call_fn
and fetch_live_prices_fn are injected fakes throughout.

    python test_portfolio_g.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from portfolio_g.agent import MAX_SLEEVE_PCT, STOP_LOSS_PCT, CoinDecision, _parse_decisions, get_decisions
from portfolio_g.daily import _apply_decisions, _check_stops, build_snapshot, run_pool_g_cycle
import portfolio_g.state as pgs

SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "SOL"]


def _daily_df(price, n=305, start=None):
    start = start or (date.today() - timedelta(days=n))
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(n)])
    c = pd.Series([price] * n, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000.0})


class TestParseDecisions(unittest.TestCase):
    def test_parses_a_clean_json_array(self):
        text = '[{"symbol": "BTC", "action": "BUY", "conviction": 0.8, "reason": "breaking out"}]'
        decisions = _parse_decisions(text, ["BTC", "ETH"])
        self.assertEqual(len(decisions), 2)   # ETH defaults to AVOID
        by_symbol = {d.symbol: d for d in decisions}
        self.assertEqual((by_symbol["BTC"].action, by_symbol["BTC"].conviction), ("BUY", 0.8))
        self.assertEqual(by_symbol["ETH"].action, "AVOID")

    def test_parses_the_array_out_of_surrounding_prose(self):
        text = 'Here is my analysis:\n[{"symbol": "ETH", "action": "SELL", "reason": "trend broke"}]\nDone.'
        decisions = _parse_decisions(text, ["ETH"])
        self.assertEqual(decisions[0].action, "SELL")

    def test_unknown_action_defaults_to_avoid(self):
        decisions = _parse_decisions('[{"symbol": "BTC", "action": "MOON", "reason": "x"}]', ["BTC"])
        self.assertEqual(decisions[0].action, "AVOID")

    def test_no_json_array_raises(self):
        with self.assertRaises(ValueError):
            _parse_decisions("I refuse to answer in JSON.", ["BTC"])


class TestGetDecisions(unittest.TestCase):
    def test_uses_the_injected_call_fn_and_records_web_search_flag(self):
        def fake_call(prompt, api_key, model):
            self.assertIn("BTC", prompt)
            return '[{"symbol": "BTC", "action": "HOLD", "reason": "steady"}]', True

        result = get_decisions({"BTC": {"price": 100, "chg_1d_pct": 0, "chg_7d_pct": 0, "chg_30d_pct": 0, "vs_300d_sma_pct": 0}},
                               {}, "fake-key", call_fn=fake_call)
        self.assertTrue(result["web_search_used"])
        self.assertEqual(result["decisions"][0].action, "HOLD")


class TestBuildSnapshot(unittest.TestCase):
    def test_pct_changes_and_sma_distance(self):
        df = _daily_df(100.0)
        df.iloc[-1, df.columns.get_loc("Close")] = 120.0   # today's live-adjusted close differs from history
        snap = build_snapshot({"BTC": df}, {"BTC": 150.0})
        s = snap["BTC"]
        self.assertAlmostEqual(s["price"], 150.0)
        self.assertAlmostEqual(s["chg_1d_pct"], (150.0 / 100.0 - 1) * 100, places=2)
        self.assertGreater(s["vs_300d_sma_pct"], 0)

    def test_missing_live_price_skips_the_coin(self):
        snap = build_snapshot({"BTC": _daily_df(100.0)}, {})
        self.assertEqual(snap, {})


class TestStopLossIsMechanicalAndFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = pgs.PORTFOLIO_G_STATE_DIR
        pgs.PORTFOLIO_G_STATE_DIR = self.tmp

    def tearDown(self):
        pgs.PORTFOLIO_G_STATE_DIR = self.orig

    def test_stop_closes_immediately_no_llm_involvement(self):
        portfolio = {"cash": 500.0, "positions": {
            "BTC": {"entry_price": 100.0, "entry_date": "2026-09-01", "entry_time": "2026-09-01T08:00",
                    "quantity": 1.0, "stop_loss": 82.0}}}
        now = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
        stopped = _check_stops(portfolio, {"BTC": 80.0}, now)
        self.assertEqual(len(stopped), 1)
        self.assertEqual((stopped[0]["exit_reason"], stopped[0]["exit_price"]), ("stop_loss", 82.0))
        self.assertAlmostEqual(stopped[0]["pnl"], (82.0 - 100.0) * 1.0)
        self.assertNotIn("BTC", portfolio["positions"])
        self.assertAlmostEqual(portfolio["cash"], 500.0 + 82.0)

    def test_no_stop_when_price_holds_above_it(self):
        portfolio = {"cash": 500.0, "positions": {
            "BTC": {"entry_price": 100.0, "entry_date": "2026-09-01", "entry_time": "2026-09-01T08:00",
                    "quantity": 1.0, "stop_loss": 82.0}}}
        stopped = _check_stops(portfolio, {"BTC": 90.0}, datetime.now(timezone.utc))
        self.assertEqual(stopped, [])
        self.assertIn("BTC", portfolio["positions"])


class TestApplyDecisions(unittest.TestCase):
    def test_buy_sizes_to_max_sleeve_and_sets_the_mechanical_stop(self):
        portfolio = {"cash": 1000.0, "positions": {}}
        decisions = [CoinDecision(symbol="BTC", action="BUY", conviction=0.7, reason="strong trend")]
        now = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
        result = _apply_decisions(portfolio, decisions, {"BTC": 80000.0}, now)
        self.assertEqual(len(result["bought"]), 1)
        pos = portfolio["positions"]["BTC"]
        self.assertAlmostEqual(pos["quantity"] * 80000.0, 1000.0 * MAX_SLEEVE_PCT, delta=0.01)
        self.assertAlmostEqual(pos["stop_loss"], 80000.0 * (1 - STOP_LOSS_PCT), places=2)
        self.assertEqual(pos["reasoning"], "strong trend")
        self.assertAlmostEqual(portfolio["cash"], 1000.0 - pos["quantity"] * 80000.0)

    def test_sell_on_a_held_coin_closes_at_live_price_with_the_llm_reason(self):
        portfolio = {"cash": 750.0, "positions": {
            "ETH": {"entry_price": 3000.0, "entry_date": "2026-09-10", "entry_time": "2026-09-10T08:00",
                    "quantity": 0.05, "stop_loss": 2500.0}}}
        decisions = [CoinDecision(symbol="ETH", action="SELL", conviction=0.6, reason="thesis no longer holds")]
        now = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
        result = _apply_decisions(portfolio, decisions, {"ETH": 3300.0}, now)
        self.assertEqual(len(result["sold"]), 1)
        self.assertEqual((result["sold"][0]["exit_reason"], result["sold"][0]["reason"]), ("llm_sell", "thesis no longer holds"))
        self.assertAlmostEqual(result["sold"][0]["pnl"], (3300.0 - 3000.0) * 0.05)
        self.assertNotIn("ETH", portfolio["positions"])

    def test_hold_and_avoid_never_touch_the_book(self):
        portfolio = {"cash": 500.0, "positions": {
            "SOL": {"entry_price": 100.0, "entry_date": "2026-09-10", "entry_time": "2026-09-10T08:00",
                    "quantity": 1.0, "stop_loss": 82.0}}}
        decisions = [CoinDecision(symbol="SOL", action="HOLD", conviction=0.5, reason="still fine"),
                    CoinDecision(symbol="XRP", action="AVOID", conviction=0.1, reason="no edge")]
        result = _apply_decisions(portfolio, decisions, {"SOL": 110.0, "XRP": 0.5}, datetime.now(timezone.utc))
        self.assertEqual((result["sold"], result["bought"]), ([], []))
        self.assertIn("SOL", portfolio["positions"])
        self.assertAlmostEqual(portfolio["cash"], 500.0)


class TestRunPoolGCycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = pgs.PORTFOLIO_G_STATE_DIR
        pgs.PORTFOLIO_G_STATE_DIR = self.tmp

    def tearDown(self):
        pgs.PORTFOLIO_G_STATE_DIR = self.orig

    def test_stop_fires_before_the_llm_ever_sees_the_coin_as_held(self):
        # Seed a position already below its stop.
        pgs.save_portfolio({"cash": 500.0, "starting_capital": 1000.0,
                            "positions": {"BTC": {"entry_price": 100000.0, "entry_date": "2026-09-01",
                                                  "entry_time": "2026-09-01T08:00", "quantity": 0.001,
                                                  "stop_loss": 82000.0}}, "last_run_at": None})
        seen_held = {}

        def fake_call(prompt, api_key, model):
            seen_held["prompt"] = prompt
            return "[]", False   # every coin defaults to AVOID

        daily_history = {s: _daily_df(100.0 if s != "BTC" else 100000.0) for s in SYMBOLS}
        result = run_pool_g_cycle(daily_history, lambda syms: {"BTC": 80000.0, "ETH": 3000.0, "BNB": 500.0, "XRP": 0.5, "SOL": 150.0},
                                  "fake-key", now=datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc), call_fn=fake_call)
        self.assertEqual(len(result["stopped"]), 1)
        self.assertIn("Currently held: nothing.", seen_held["prompt"])   # BTC was already stopped out before the LLM was asked
        pf = pgs.load_portfolio()
        self.assertNotIn("BTC", pf["positions"])
        self.assertAlmostEqual(pf["cash"], 500.0 + 82000.0 * 0.001)

    def test_full_cycle_buy_then_next_cycle_sell(self):
        pgs.save_portfolio({"cash": 1000.0, "starting_capital": 1000.0, "positions": {}, "last_run_at": None})
        daily_history = {s: _daily_df(100.0) for s in SYMBOLS}

        def buy_btc(prompt, api_key, model):
            return '[{"symbol": "BTC", "action": "BUY", "conviction": 0.8, "reason": "breakout"}]', False

        r1 = run_pool_g_cycle(daily_history, lambda syms: {s: 100.0 for s in syms}, "fake-key",
                              now=datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc), call_fn=buy_btc)
        self.assertEqual(len(r1["bought"]), 1)

        def sell_btc(prompt, api_key, model):
            self.assertIn("BTC", prompt)   # now shown as held
            return '[{"symbol": "BTC", "action": "SELL", "conviction": 0.6, "reason": "reversal"}]', False

        r2 = run_pool_g_cycle(daily_history, lambda syms: {s: 130.0 for s in syms}, "fake-key",
                              now=datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc), call_fn=sell_btc)
        self.assertEqual(len(r2["sold"]), 1)
        self.assertGreater(r2["sold"][0]["pnl"], 0)
        pf = pgs.load_portfolio()
        self.assertEqual(pf["positions"], {})


class TestPoolGReporting(unittest.TestCase):
    def test_pre_and_post_tax_on_an_open_position(self):
        from reporting.pool_g import build_pool_g
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "pool_g"))
        with open(os.path.join(root, "pool_g", "portfolio.json"), "w") as f:
            json.dump({"cash": 800.0, "starting_capital": 1000.0,
                       "positions": {"BTC": {"entry_price": 80000.0, "quantity": 0.0025,
                                             "entry_date": "2026-09-15", "reasoning": "breakout"}}}, f)
        with open(os.path.join(root, "pool_g", "trades.jsonl"), "w") as f:
            pass
        g = build_pool_g(root, {"BTC": 84000.0}, usdinr=100.0, today=date(2026, 9, 17))
        self.assertEqual(g["positions"], 1)
        self.assertAlmostEqual(g["unbooked"]["raw"], 10.0)
        self.assertLess(g["unbooked"]["post_tax"], g["unbooked"]["raw"])
        self.assertEqual(g["open_positions"][0]["reasoning"], "breakout")


if __name__ == "__main__":
    unittest.main()
