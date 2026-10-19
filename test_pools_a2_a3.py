"""
Pools A2 and A3 (2026-10-19): Pool A's strategies on their own Rs.2,00,000 and Rs.3,00,000 books,
run inside run_paper_trading.py's own --all-due and --resolve-at-open invocations off the data Pool A
fetched. Capital is the only difference -- these tests hold the engine calls equal to Pool A's apart
from the sizing cap (and the wind-down's scale), check every pool fails on its own, that nothing is
fetched twice, that Pool A is untouched, and that A1 (the legacy books) is never confused with them.

    python -m unittest test_pools_a2_a3
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import deployment.capital_winddown as cwd
import deployment.pead_forward_engine as pfe
from deployment import paper_trading_engine as pte
from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.settings import (PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET,
                                 PAPER_TRADING_WINDDOWN_TARGET_CAPITAL, pool_a_subpool_capital)
from swing_research.base import Signal, Strategy
import run_paper_trading as rpt

KEY = "fake_a2_strategy"
TODAY = date.today()


class _BuyToday(Strategy):
    """Signals X.NS on today's bar with an 8% stop -- 1% risk, so a book of capital C buys C/800 shares."""
    risk_pct_per_unit = 0.01

    def precompute(self, df):
        df = df.copy(); df["date"] = df.index.date; return df

    def entry_signal_at(self, row):
        return Signal(symbol="", direction="BUY", entry_price=100.0, stop_loss=92.0) if row.date == TODAY else None

    def exit_signal_at(self, row, pos):
        return None


def _frame(day=TODAY, price=100.0):
    idx = pd.to_datetime([day])
    return pd.DataFrame([{"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1000}], index=idx)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _record(key=KEY, status=DeploymentStatus.PAPER_TRADING, family="swing_research published strategy"):
    return StrategyRecord(strategy_key=key, display_name="Fake", strategy_family=family,
                          research_verdict=ResearchVerdict.PASS, research_verdict_source="EXP-000",
                          deployment_status=status, deployment_status_history=[],
                          strategy_id="SW-999", primary_experiment_id="")


class _TempState(unittest.TestCase):
    """Points Pool A (and so A2/A3, its siblings) at a temp tree, and restores everything."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pool_a = os.path.join(self.root, "paper_trading")
        self.saved = (pte.PAPER_TRADING_STATE_DIR, cwd.PAPER_TRADING_STATE_DIR, pfe.STATE_DIR_OVERRIDE)
        pte.PAPER_TRADING_STATE_DIR = cwd.PAPER_TRADING_STATE_DIR = self.pool_a
        for name in ("generate_report", "generate_drift_report", "_send_notification"):
            p = patch.object(rpt, name)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(rpt, "is_due_now", return_value=(True, "due")); p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        pte.PAPER_TRADING_STATE_DIR, cwd.PAPER_TRADING_STATE_DIR, pfe.STATE_DIR_OVERRIDE = self.saved
        rpt._STRATEGY_FACTORIES.pop(KEY, None)
        shutil.rmtree(self.root, ignore_errors=True)

    def _pf(self, dirname, key=KEY):
        with open(os.path.join(self.root, dirname, key, "portfolio.json"), encoding="utf-8") as f:
            return json.load(f)


class TestCapitalSettings(unittest.TestCase):
    def test_two_and_three_times_pool_a(self):
        self.assertEqual(PAPER_TRADING_WINDDOWN_TARGET_CAPITAL, 100_000)
        self.assertEqual(pool_a_subpool_capital("pool_a2"), 200_000)
        self.assertEqual(pool_a_subpool_capital("pool_a3"), 300_000)

    def test_names_never_touch_pool_a1(self):
        self.assertEqual(rpt.POOL_A_SUBPOOLS, {"Pool A2": "pool_a2", "Pool A3": "pool_a3"})
        self.assertNotIn("paper_trading_legacy", rpt.POOL_A_SUBPOOLS.values())
        with self.assertRaises(KeyError):
            pool_a_subpool_capital("paper_trading_legacy")


class TestSeedingAndFolders(_TempState):
    def test_seeded_at_own_capital_in_own_sibling_folder(self):
        for label, dirname in rpt.POOL_A_SUBPOOLS.items():
            with rpt._in_subpool(dirname) as path:
                self.assertEqual(path, os.path.join(self.root, dirname))
                rpt._seed_if_missing(KEY, pool_a_subpool_capital(dirname), label)
        self.assertEqual(self._pf("pool_a2")["cash"], 200_000)
        self.assertEqual(self._pf("pool_a2")["starting_capital"], 200_000)
        self.assertEqual(self._pf("pool_a3")["cash"], 300_000)
        self.assertFalse(os.path.exists(self.pool_a))                     # Pool A's folder untouched
        self.assertEqual(pte.PAPER_TRADING_STATE_DIR, self.pool_a)

    def test_existing_book_never_reseeded(self):
        with rpt._in_subpool("pool_a2"):
            rpt._seed_if_missing(KEY, 200_000, "Pool A2")
            pte._save_portfolio(KEY, {"cash": 1.0})
            rpt._seed_if_missing(KEY, 200_000, "Pool A2")
        self.assertEqual(self._pf("pool_a2"), {"cash": 1.0})

    def test_folders_restored_even_on_error(self):
        with self.assertRaises(RuntimeError):
            with rpt._in_subpool("pool_a3"):
                self.assertTrue(pte.PAPER_TRADING_STATE_DIR.endswith("pool_a3"))
                self.assertTrue(cwd.PAPER_TRADING_STATE_DIR.endswith("pool_a3"))
                raise RuntimeError("boom")
        self.assertEqual((pte.PAPER_TRADING_STATE_DIR, cwd.PAPER_TRADING_STATE_DIR), (self.pool_a, self.pool_a))


class TestEndOfDayEngineCalls(_TempState):
    def setUp(self):
        super().setUp()
        self.extra_calls = []
        rpt._STRATEGY_FACTORIES[KEY] = {"display_name": "Fake", "strategy_factory": _BuyToday,
                                        "compute_extra_columns_fn": lambda d: self.extra_calls.append(1) or {}}
        self.calls, self.winddowns = [], []

    def _fake_run_daily(self, fail_in=()):
        def fake(key, strategy, **kw):
            folder = os.path.basename(pte.PAPER_TRADING_STATE_DIR)
            kw["compute_extra_columns_fn"](kw["fetch_data_fn"]())
            self.calls.append((folder, key, type(strategy), kw))
            if folder in fail_in:
                raise RuntimeError(f"boom in {folder}")
            return {"status": "processed", "as_of_date": TODAY.isoformat(), "new_entries": [], "new_exits": [],
                    "mark_to_market_equity": 100_000.0, "open_positions": 0, "cash": 1.0}
        return fake

    def _fake_winddown(self, key, **kw):
        self.winddowns.append((os.path.basename(cwd.PAPER_TRADING_STATE_DIR),
                               os.path.basename(pte.PAPER_TRADING_STATE_DIR), kw))
        return {"withdrawn": 0.0}

    def _run(self, fail_in=(), record=None):
        with patch.object(rpt, "get_strategy", return_value=record or _record()), \
                patch.object(rpt, "get_swing_universe", return_value=["X.NS"]), \
                patch.object(rpt, "fetch_all", return_value={"X.NS": _frame()}) as fetch, \
                patch.object(rpt, "run_daily", side_effect=self._fake_run_daily(fail_in)), \
                patch.object(rpt, "apply_capital_winddown", side_effect=self._fake_winddown):
            out = rpt._run_one(KEY)
        return out, fetch

    def test_same_call_as_pool_a_except_the_sizing_cap(self):
        out, fetch = self._run()
        self.assertIsNotNone(out)
        self.assertEqual(fetch.call_count, 1)                               # the universe fetched once
        self.assertEqual([c[0] for c in self.calls], ["paper_trading", "pool_a2", "pool_a3"])
        a_folder, a_key, a_type, a_kw = self.calls[0]
        self.assertNotIn("sizing_capital_cap", a_kw)                         # Pool A: the engine default, as ever
        for (folder, key, typ, kw), cap in zip(self.calls[1:], (200_000, 300_000)):
            self.assertEqual((key, typ), (a_key, a_type))
            rest = dict(kw); self.assertEqual(rest.pop("sizing_capital_cap"), cap)
            self.assertEqual(rest, a_kw)                                     # identical: data, columns, config, floor
        self.assertEqual(len(self.extra_calls), 1)                           # percentile columns computed once
        self.assertEqual(pte.PAPER_TRADING_STATE_DIR, self.pool_a)

    def test_wind_down_at_each_books_own_scale_in_its_own_folder(self):
        self._run()
        by_folder = {w[0]: w for w in self.winddowns}
        self.assertEqual(set(by_folder), {"paper_trading", "pool_a2", "pool_a3"})
        a_kw = by_folder["paper_trading"][2]
        self.assertNotIn("target_active_capital", a_kw)                      # Pool A unchanged
        for folder, m in (("pool_a2", 2), ("pool_a3", 3)):
            cwd_dir, pte_dir, kw = by_folder[folder]
            self.assertEqual(pte_dir, folder)
            kw = dict(kw)
            self.assertEqual(kw.pop("target_active_capital"), 100_000 * m)
            self.assertEqual(kw.pop("reduced_floor_while_above_target"),
                             PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET * m)
            self.assertEqual(kw, a_kw)

    def test_one_sub_pool_failing_does_not_stop_the_other_or_pool_a(self):
        out, _ = self._run(fail_in=("pool_a2",))
        self.assertIsNotNone(out)                                            # Pool A still completed
        self.assertEqual([c[0] for c in self.calls], ["paper_trading", "pool_a2", "pool_a3"])
        self.assertEqual({w[0] for w in self.winddowns}, {"paper_trading", "pool_a3"})

    def test_pool_a_failing_does_not_stop_the_sub_pools(self):
        out, _ = self._run(fail_in=("paper_trading",))
        self.assertIsNone(out)                                               # Pool A reported its own failure
        self.assertEqual([c[0] for c in self.calls], ["paper_trading", "pool_a2", "pool_a3"])
        self.assertEqual({w[0] for w in self.winddowns}, {"pool_a2", "pool_a3"})

    def test_only_paper_trading_strategies_get_sub_pool_books(self):
        self._run(record=_record(status=DeploymentStatus.PILOT_LIVE))
        self.assertEqual([c[0] for c in self.calls], ["paper_trading"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "pool_a2")))


class TestEndOfDayRealEngine(_TempState):
    """The real engine, same-day-close fills, one signal: positions come out 1:2:3."""

    def test_positions_scale_with_the_book(self):
        rpt._STRATEGY_FACTORIES[KEY] = {"display_name": "Fake", "strategy_factory": _BuyToday,
                                        "execution_config": pte.ExecutionRealismConfig(fill_timing="same_day_close")}
        with patch.object(rpt, "get_strategy", return_value=_record()), \
                patch.object(rpt, "get_swing_universe", return_value=["X.NS"]), \
                patch.object(rpt, "fetch_all", return_value={"X.NS": _frame()}):
            rpt._run_one(KEY)
        qty = {d: self._pf(d)["positions"]["X.NS"]["quantity"] for d in ("paper_trading", "pool_a2", "pool_a3")}
        self.assertEqual(qty, {"paper_trading": 125, "pool_a2": 250, "pool_a3": 375})
        self.assertEqual(self._pf("paper_trading")["starting_capital"], 100_000)   # Pool A seeded as ever
        self.assertEqual(self._pf("pool_a3")["cash"], 300_000 - 375 * 100.0)


class TestPead(_TempState):
    def test_pead_sub_pools_share_the_scan_and_prices_and_get_their_cap(self):
        calls = []

        def fake_pead(symbols, **kw):
            calls.append((os.path.basename(pte.PAPER_TRADING_STATE_DIR), symbols, kw))
            return {"status": "skipped_already_processed", "as_of_date": TODAY.isoformat()}
        with patch.object(rpt, "get_strategy", return_value=_record("pead")), \
                patch.object(rpt, "get_swing_universe", return_value=["X.NS"]), \
                patch.object(pfe, "run_pead_daily", side_effect=fake_pead):
            rpt._run_one("pead")
        self.assertEqual([c[0] for c in calls], ["paper_trading", "pool_a2", "pool_a3"])
        a_kw = calls[0][2]
        self.assertNotIn("state_dir", a_kw); self.assertNotIn("sizing_capital_cap", a_kw)
        for (folder, symbols, kw), cap in zip(calls[1:], (200_000, 300_000)):
            self.assertEqual(symbols, calls[0][1])
            rest = dict(kw)
            self.assertEqual(rest.pop("sizing_capital_cap"), cap)
            self.assertEqual(rest.pop("state_dir"), os.path.join(self.root, folder))
            self.assertEqual(rest, a_kw)                                     # same fetch fns, config, force
        self.assertIsNone(pfe.STATE_DIR_OVERRIDE)

    def test_run_pead_daily_passes_the_cap_and_uses_the_shared_scan(self):
        seen, scans = {}, []
        with patch.object(pfe, "run_daily", side_effect=lambda *a, **kw: seen.update(kw) or {"status": "x"}):
            pfe.run_pead_daily(["X.NS"], as_of_date=TODAY, fetch_ohlcv_fn=lambda s: {},
                               fetch_events_fn=lambda *a, **k: scans.append(a) or [],
                               state_dir=os.path.join(self.root, "pool_a2"), sizing_capital_cap=200_000)
        self.assertEqual(seen["sizing_capital_cap"], 200_000)
        self.assertEqual(len(scans), 1)

    def test_shared_fetchers_ask_once(self):
        with patch.object(rpt, "fetch_all", side_effect=lambda syms, period: {s: s for s in syms}) as fa:
            f = rpt._shared_fetch("3y")
            self.assertEqual(f(["A", "B"]), {"A": "A", "B": "B"})
            self.assertEqual(f(["B", "C"]), {"B": "B", "C": "C"})
        self.assertEqual([c.args[0] for c in fa.call_args_list], [["A", "B"], ["C"]])
        with patch.object(pfe, "fetch_recent_earnings_events_chunked", return_value=["ev"]) as scan:
            ev = rpt._shared_earnings_events()
            self.assertEqual(ev(["A"], TODAY, 10), ["ev"]); ev(["A"], TODAY, 10)
        self.assertEqual(scan.call_count, 1)


class TestResolveAtOpen(_TempState):
    def setUp(self):
        super().setUp()
        rpt._STRATEGY_FACTORIES[KEY] = {"display_name": "Fake", "strategy_factory": _BuyToday}
        pend = lambda syms: {"cash": 0, "starting_capital": 0, "positions": {}, "pending_exits": {},
                             "pending_entries": {s: {"stop_loss": 92.0, "signal_date": "2026-01-01"} for s in syms}}
        self.books = {"paper_trading": pend(["X.NS"]), "pool_a2": pend(["X.NS", "Y.NS"])}   # no A3 book yet

    def _write_books(self):
        for d, pf in self.books.items():
            os.makedirs(os.path.join(self.root, d, KEY), exist_ok=True)
            with open(os.path.join(self.root, d, KEY, "portfolio.json"), "w") as f:
                json.dump(pf, f)

    def _run(self):
        calls = []

        def fake_resolve(key, strategy, fetch_open_data_fn, **kw):
            fetch_open_data_fn()
            calls.append((os.path.basename(pte.PAPER_TRADING_STATE_DIR), type(strategy), kw))
            return {"new_entries": [], "new_exits": []}
        with patch.object(rpt, "get_strategy", return_value=_record()), \
                patch.object(rpt, "fetch_all", side_effect=lambda syms, period: {}) as fa, \
                patch.object(rpt, "resolve_pending_fills_at_open", side_effect=fake_resolve):
            rpt._resolve_at_open_one(KEY)
        return calls, fa

    def test_sub_pools_resolve_with_their_cap_and_only_fetch_what_pool_a_did_not(self):
        self._write_books()
        calls, fa = self._run()
        self.assertEqual([c[0] for c in calls], ["paper_trading", "pool_a2"])   # A3 has no book: skipped
        self.assertEqual(calls[0][2], {"execution_config": rpt._DEFAULT_EXECUTION_CONFIG})   # Pool A as ever
        rest = dict(calls[1][2])
        self.assertEqual(rest.pop("sizing_capital_cap"), 200_000)
        self.assertEqual(rest, calls[0][2])
        self.assertEqual([(c.args[0], c.kwargs["period"]) for c in fa.call_args_list],
                         [(["X.NS"], "5d"), (["Y.NS"], "5d")])
        self.assertEqual(pte.PAPER_TRADING_STATE_DIR, self.pool_a)

    def test_sub_pools_resolve_even_when_pool_a_has_nothing_pending(self):
        self.books["paper_trading"]["pending_entries"] = {}
        self._write_books()
        calls, _ = self._run()
        self.assertEqual([c[0] for c in calls], ["pool_a2"])

    def test_real_resolver_sizes_off_the_cap(self):
        data = {"X.NS": _frame()}
        for d, cap in (("paper_trading", None), ("pool_a2", 200_000)):
            pte.PAPER_TRADING_STATE_DIR = os.path.join(self.root, d)
            pte._save_portfolio(KEY, {"cash": 300_000.0, "starting_capital": 300_000.0, "positions": {},
                                      "pending_exits": {}, "pending_entries": {"X.NS": {"stop_loss": 92.0,
                                                                                        "signal_date": "x"}}})
            kw = {} if cap is None else {"sizing_capital_cap": cap}
            pte.resolve_pending_fills_at_open(KEY, _BuyToday(), lambda: data, as_of_date=TODAY,
                                              execution_config=pte.ExecutionRealismConfig(fill_timing="next_day_open"), **kw)
        self.assertEqual(self._pf("paper_trading")["positions"]["X.NS"]["quantity"], 125)
        self.assertEqual(self._pf("pool_a2")["positions"]["X.NS"]["quantity"], 250)


class TestReporting(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for d, m in (("paper_trading", 1), ("pool_a2", 2), ("pool_a3", 3)):
            os.makedirs(os.path.join(self.root, d, "alpha"))
            with open(os.path.join(self.root, d, "alpha", "portfolio.json"), "w") as f:
                json.dump({"cash": 90000.0 * m, "starting_capital": 100000.0 * m, "last_processed_date": "2026-10-19",
                           "positions": {"X.NS": {"entry_price": 100.0, "quantity": 100 * m, "stop_loss": 92.0,
                                                  "entry_date": "2026-10-19"}}}, f)
            with open(os.path.join(self.root, d, "alpha", "trades.jsonl"), "w") as f:
                f.write(json.dumps({"symbol": "Y.NS", "entry_date": "2026-10-01", "exit_date": "2026-10-10",
                                    "entry_price": 100.0, "exit_price": 110.0, "quantity": 10 * m, "pnl": 100.0 * m}) + "\n")
        # a legacy Pool A1 book -- must never be read as A2/A3, nor they as it
        os.makedirs(os.path.join(self.root, "paper_trading_legacy", "alpha"))
        with open(os.path.join(self.root, "paper_trading_legacy", "alpha", "portfolio.json"), "w") as f:
            json.dump({"cash": 7.0, "positions": {}, "last_processed_date": "2026-10-19"}, f)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _summary(self):
        from reporting.pool_summary import build_pool_summary
        return build_pool_summary(self.root, {"alpha": "Alpha"}, lambda syms: {"X.NS": 110.0}, today=date(2026, 10, 19))

    def test_summary_counts_a2_a3_like_pool_f_and_keeps_a1_apart(self):
        from reporting.pool_summary import format_pool_summary
        s = self._summary()
        self.assertEqual((s["pools"]["A2"]["cash"], s["pools"]["A3"]["cash"]), (180000.0, 270000.0))
        self.assertEqual(s["pools"]["A1"]["cash"], 7.0)
        self.assertAlmostEqual(s["overall"]["unrealised"], 1000.0 * (1 + 2 + 3))
        text = format_pool_summary(s)
        self.assertIn("*Pool A2 (Pool A on Rs.2,00,000 books)*", text)
        self.assertIn("*Pool A3 (Pool A on Rs.3,00,000 books)*", text)
        self.assertIn("A, B, C, D, F, A2, A3, E, E1 post-tax", text)

    def test_pool_a_block_is_unchanged_by_the_sub_pools(self):
        from reporting.pool_summary import format_pool_summary
        with_sub = format_pool_summary(self._summary())
        shutil.rmtree(os.path.join(self.root, "pool_a2")); shutil.rmtree(os.path.join(self.root, "pool_a3"))
        without = format_pool_summary(self._summary())
        block = lambda t: t.split("*Pool A1")[0]
        self.assertEqual(block(with_sub), block(without))
        self.assertNotIn("A2", without)
        self.assertIn("A, B, C, D, F, E, E1 post-tax", without)

    def test_dashboard_state(self):
        from dashboard.state_view import build_dashboard_state
        rec = SimpleNamespace(strategy_key="alpha", display_name="Alpha", strategy_id="SW-001",
                              deployment_status=DeploymentStatus.PAPER_TRADING, research_verdict=ResearchVerdict.PASS,
                              primary_experiment_id="", strategy_family="swing_research published strategy",
                              research_verdict_source="", deployment_status_history=[])
        logs = os.path.join(self.root, "logs"); os.makedirs(logs)
        s = build_dashboard_state(self.root, logs, [rec], {"X.NS": 110.0}, "2026-10-19T15:00",
                                  now=pd.Timestamp("2026-10-19 16:00").to_pydatetime(), usdinr=90.0)
        self.assertEqual(sorted(b["pool"] for b in s["books"] if b["key"] == "alpha"), ["A", "A2", "A3"])
        self.assertNotIn("A1", s["pools"])
        self.assertIn("A2", s["pools"]); self.assertIn("A3", s["pools"])
        row = next(r for r in s["strategies"] if r["key"] == "alpha")
        self.assertEqual(row["pool"], "Pool A, A2, A3")
        self.assertEqual([v["pool"] for v in row["variants"]], ["Pool A", "Pool A2", "Pool A3"])
        self.assertEqual([v["diff"] for v in row["variants"]], [False, True, True])
        self.assertIn("twice", row["variants"][1]["brief"])
        self.assertEqual({p["pool"]: p["closed_trades"] for p in row["pools_breakdown"]},
                         {"Pool A": 1, "Pool A2": 1, "Pool A3": 1})
        self.assertEqual({l["pool"] for l in s["statement"] if l["key"] == "alpha"}, {"Pool A", "Pool A2", "Pool A3"})
        closed = {r["pool"]: r for r in s["ledger"] if r.get("book_key") == "alpha" and r["status"] == "Closed"}
        self.assertEqual(closed["Pool A3"]["qty"], 30)
        self.assertEqual(s["overall"]["capital"], round(sum(p["capital"] for p in s["pools"].values())
                                                        + s["pool_d"]["capital"], 2))

    def test_without_sub_pool_books_the_label_is_as_before(self):
        from dashboard.state_view import strategies_view
        rec = SimpleNamespace(strategy_key="alpha", display_name="Alpha", strategy_id="SW-001",
                              deployment_status=DeploymentStatus.PAPER_TRADING, research_verdict=ResearchVerdict.PASS,
                              primary_experiment_id="", strategy_family="swing_research published strategy")
        row = strategies_view([rec], "x", {"alpha"}, books=[{"key": "alpha", "pool": "A", "capital": 1}],
                              state_dir=self.root)[0]
        self.assertEqual(row["pool"], "Pool A, F")
        self.assertEqual([v["pool"] for v in row["variants"]], ["Pool A", "Pool F"])

    def test_team_tab_text(self):
        from dashboard.state_view import AGENTS, DESKS, POOLS_INFO
        info = {p["pool"]: p for p in POOLS_INFO}
        self.assertIn("₹2,00,000", info["A2"]["text"]); self.assertIn("₹3,00,000", info["A3"]["text"])
        desk = next(d for d in DESKS if d["id"] == "trading_desk")
        self.assertIn("A2", desk["name"]); self.assertIn("A3", desk["blurb"])
        self.assertIn("A2", next(a for a in AGENTS if a["id"] == "paper_trading_engine")["job"])

    def test_paper_capital_one_two_three(self):
        from deployment.paper_capital import set_capital
        r = set_capital(self.root, "alpha", 1_200_000, {"Pool A": 100000.0, "Pool A2": 200000.0, "Pool A3": 300000.0},
                        record=SimpleNamespace(deployment_status=DeploymentStatus.PAPER_TRADING))
        self.assertTrue(r["ok"], r.get("error"))
        cash = {d: _load(os.path.join(self.root, d, "alpha", "portfolio.json"))["cash"]
                for d in ("paper_trading", "pool_a2", "pool_a3")}
        self.assertEqual(cash, {"paper_trading": 190000.0, "pool_a2": 380000.0, "pool_a3": 570000.0})

    def test_equity_series_and_manual_exit_see_the_sub_pools(self):
        from dashboard.manual_exit import apply_manual_exit
        from reporting.equity_series import BOOK_ROOTS
        self.assertIn("pool_a2", BOOK_ROOTS); self.assertIn("pool_a3", BOOK_ROOTS)
        t = apply_manual_exit(self.root, "A2", "alpha", "X.NS", 50, 120.0, today=date(2026, 10, 19))
        self.assertEqual((t["pool"], t["remaining"]), ("A2", 150))
        self.assertEqual(_load(os.path.join(self.root, "paper_trading", "alpha", "portfolio.json"))
                         ["positions"]["X.NS"]["quantity"], 100)                 # Pool A's book untouched


if __name__ == "__main__":
    unittest.main()
