import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from reporting.pool_i import build_pool_i, pool_i_starting_capital, POOL_I_STARTING_CAPITAL_USD
from swing_research.us_equity_costs import IBKRUSTieredCostModel


class TestBuildPoolI(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _write_book(self, key, portfolio, trades):
        book_dir = os.path.join(self.state_dir, "pool_i", key)
        os.makedirs(book_dir, exist_ok=True)
        with open(os.path.join(book_dir, "portfolio.json"), "w", encoding="utf-8") as f:
            json.dump(portfolio, f)
        with open(os.path.join(book_dir, "trades.jsonl"), "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

    def test_no_books_yet(self):
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1))
        self.assertFalse(result["exists"])
        self.assertEqual(result["books"], [])

    def test_seeded_book_with_no_activity(self):
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": POOL_I_STARTING_CAPITAL_USD, "starting_capital": POOL_I_STARTING_CAPITAL_USD,
                          "positions": {}, "last_processed_date": "2026-10-01"}, [])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1))
        self.assertTrue(result["exists"])
        b = result["books"][0]
        self.assertEqual(b["cash"], POOL_I_STARTING_CAPITAL_USD)
        self.assertEqual(b["positions"], 0)
        self.assertTrue(b["updated_today"])

    def test_open_position_unbooked_pnl_and_inr_conversion(self):
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 500.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD,
                          "positions": {"AAPL": {"entry_price": 100.0, "quantity": 5, "entry_date": "2026-09-01"}},
                          "last_processed_date": "2026-10-01"}, [])
        result = build_pool_i(self.state_dir, {"AAPL": 110.0}, 90.0, today=date(2026, 10, 1),
                              other_annual_income=1_400_000)
        b = result["books"][0]
        self.assertEqual(b["positions"], 1)
        self.assertEqual(b["deployed"], 500.0)
        # (110-100)*5 = 50 raw gain, held ~1 month -> short-term, taxed at the 15% New Regime
        # bracket (1,400,000 other income) + 4% cess.
        self.assertEqual(b["unbooked"]["raw"], 50.0)
        self.assertAlmostEqual(b["unbooked"]["tax"], 50.0 * 0.15 * 1.04, places=2)
        self.assertAlmostEqual(result["inr"]["unbooked"]["raw"], 50.0 * 90.0, places=2)

    def test_long_term_closed_trade_taxed_flat_12_5_plus_cess(self):
        self._write_book("cross_sectional_momentum_us",
                         {"cash": 1_200.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"},
                         [{"symbol": "MSFT", "entry_price": 100.0, "exit_price": 200.0, "quantity": 10,
                           "entry_date": "2023-01-01", "exit_date": "2026-01-01", "pnl": 1000.0}])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1), other_annual_income=1_400_000)
        b = result["books"][0]
        # 12.5% + cess, no indexation (Finance (No.2) Act 2024), on the raw gain -- fees not deducted
        self.assertAlmostEqual(b["booked"]["tax"], 1000.0 * 0.125 * 1.04, places=2)
        fees = IBKRUSTieredCostModel().round_trip_cost(10, 100.0, 200.0)
        self.assertAlmostEqual(b["booked"]["fees"], fees, places=2)
        self.assertAlmostEqual(b["booked"]["post_tax"], 1000.0 - fees - 1000.0 * 0.125 * 1.04, places=2)

    def test_progressive_stcg_across_multiple_trades_same_fy(self):
        # Two short-term trades in the same FY: the second trade's marginal rate must
        # reflect the first trade's gain already pushing income into a higher bracket.
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 1_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"},
                         [{"symbol": "AAPL", "entry_price": 100.0, "exit_price": 300.0, "quantity": 1,
                           "entry_date": "2026-04-05", "exit_date": "2026-05-01", "pnl": 200_000.0},
                          {"symbol": "MSFT", "entry_price": 100.0, "exit_price": 150.0, "quantity": 1,
                           "entry_date": "2026-05-05", "exit_date": "2026-06-01", "pnl": 50.0}])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1), other_annual_income=1_400_000)
        b = result["books"][0]
        # 1,400,000 + 200,000 = 1,600,000 floor for the second trade -> lands at the 20% bracket edge.
        from swing_research.us_equity_costs import marginal_stcg_rate
        expected_second_rate = marginal_stcg_rate(1_400_000, 200_000, 50.0)
        self.assertGreater(expected_second_rate, marginal_stcg_rate(1_400_000, 0, 50.0))
        total_expected_tax = (200_000 * marginal_stcg_rate(1_400_000, 0, 200_000)
                              + 50 * expected_second_rate)
        self.assertAlmostEqual(b["booked"]["tax"], total_expected_tax, places=2)

    def test_financial_year_boundary_resets_the_progressive_total(self):
        # A trade just before April 1 and one just after must NOT share a running STCG total.
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 1_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"},
                         [{"symbol": "AAPL", "entry_price": 100.0, "exit_price": 300.0, "quantity": 1,
                           "entry_date": "2026-02-01", "exit_date": "2026-03-20", "pnl": 200_000.0},
                          {"symbol": "MSFT", "entry_price": 100.0, "exit_price": 150.0, "quantity": 1,
                           "entry_date": "2026-04-05", "exit_date": "2026-04-10", "pnl": 50.0}])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1), other_annual_income=1_400_000)
        b = result["books"][0]
        from swing_research.us_equity_costs import marginal_stcg_rate
        expected_tax = (200_000 * marginal_stcg_rate(1_400_000, 0, 200_000)
                        + 50 * marginal_stcg_rate(1_400_000, 0, 50.0))   # resets to 0 for the new FY
        self.assertAlmostEqual(b["booked"]["tax"], expected_tax, places=2)

    def test_totals_and_inr_fold_across_multiple_books(self):
        self._write_book("minervini_trend_template_filter_us",
                         {"cash": 1_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"}, [])
        self._write_book("cross_sectional_momentum_us",
                         {"cash": 2_000.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD, "positions": {},
                          "last_processed_date": "2026-10-01"}, [])
        result = build_pool_i(self.state_dir, {}, 90.0, today=date(2026, 10, 1))
        self.assertEqual(len(result["books"]), 2)
        self.assertEqual(result["usd"]["cash"], 3_000.0)
        self.assertAlmostEqual(result["inr"]["cash"], 3_000.0 * 90.0, places=2)



class TestIBKRFeesInReporting(unittest.TestCase):
    """Interactive Brokers' US Tiered fees (2026-10-19) are applied in REPORTING, the way tax already
    is -- the books' trades, cash and positions must come out exactly as they went in."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.book_dir = os.path.join(self.state_dir, "pool_i", "cross_sectional_momentum_us")
        os.makedirs(self.book_dir)
        self.portfolio = {"cash": 600.0, "starting_capital": POOL_I_STARTING_CAPITAL_USD,
                          "positions": {"AAPL": {"entry_price": 100.0, "quantity": 4, "entry_date": "2026-09-20"}},
                          "last_processed_date": "2026-10-01"}
        self.trade = {"symbol": "MSFT", "entry_price": 50.0, "exit_price": 60.0, "quantity": 8,
                      "entry_date": "2026-09-01", "exit_date": "2026-09-15", "pnl": 80.0}
        with open(os.path.join(self.book_dir, "portfolio.json"), "w", encoding="utf-8") as f:
            json.dump(self.portfolio, f)
        with open(os.path.join(self.book_dir, "trades.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(self.trade) + "\n")

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_closed_trade_pays_both_legs_open_position_entry_plus_exit_as_if_sold_today(self):
        result = build_pool_i(self.state_dir, {"AAPL": 105.0}, 90.0, today=date(2026, 10, 1),
                              other_annual_income=1_400_000)
        b, m = result["books"][0], IBKRUSTieredCostModel()
        self.assertAlmostEqual(b["booked"]["fees"], m.order_cost(8, 50.0, "BUY") + m.order_cost(8, 60.0, "SELL"), places=2)
        self.assertAlmostEqual(b["unbooked"]["fees"], m.order_cost(4, 100.0, "BUY") + m.order_cost(4, 105.0, "SELL"), places=2)
        self.assertGreater(b["booked"]["fees"], 0)
        self.assertAlmostEqual(b["booked"]["pre_tax"], 80.0 - b["booked"]["fees"], places=2)
        self.assertAlmostEqual(b["booked"]["post_tax"], 80.0 - b["booked"]["fees"] - b["booked"]["tax"], places=2)
        self.assertAlmostEqual(b["cash_after_tax"], 600.0 - b["booked"]["fees"] - b["booked"]["tax"], places=2)
        self.assertIn("broker", result["cost_model"])

    def test_tax_is_on_the_raw_gain_unchanged_by_fees(self):
        with_fees = build_pool_i(self.state_dir, {"AAPL": 105.0}, 90.0, today=date(2026, 10, 1),
                                 other_annual_income=1_400_000)
        free = build_pool_i(self.state_dir, {"AAPL": 105.0}, 90.0, today=date(2026, 10, 1),
                            other_annual_income=1_400_000,
                            fees=IBKRUSTieredCostModel(commission_min=0, commission_per_share=0, exchange_per_share=0,
                                                       clearing_per_share=0, sec_fee_pct_of_sell_value=0,
                                                       cat_fee_per_share_sold=0))
        self.assertEqual(free["books"][0]["booked"]["fees"], 0.0)
        self.assertEqual(with_fees["books"][0]["booked"]["tax"], free["books"][0]["booked"]["tax"])
        self.assertEqual(with_fees["books"][0]["unbooked"]["tax"], free["books"][0]["unbooked"]["tax"])

    def test_the_book_on_disk_is_never_touched(self):
        build_pool_i(self.state_dir, {"AAPL": 105.0}, 90.0, today=date(2026, 10, 1))
        with open(os.path.join(self.book_dir, "portfolio.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.portfolio)
        with open(os.path.join(self.book_dir, "trades.jsonl"), encoding="utf-8") as f:
            self.assertEqual([json.loads(l) for l in f if l.strip()], [self.trade])
        result = build_pool_i(self.state_dir, {"AAPL": 105.0}, 90.0, today=date(2026, 10, 1))
        b = result["books"][0]
        self.assertEqual(b["cash"], 600.0)                       # cash is the book's raw cash, fees not taken
        self.assertEqual(b["capital"], round(600.0 + 400.0 - 80.0, 2))
        self.assertEqual(b["booked"]["raw"], 80.0)


class TestPoolI2AndI3Reporting(unittest.TestCase):
    def test_starting_capital_is_two_and_three_times_pool_i(self):
        self.assertEqual(pool_i_starting_capital("pool_i"), POOL_I_STARTING_CAPITAL_USD)
        self.assertEqual(pool_i_starting_capital("pool_i2"), round(2 * POOL_I_STARTING_CAPITAL_USD, 2))
        self.assertEqual(pool_i_starting_capital("pool_i3"), round(3 * POOL_I_STARTING_CAPITAL_USD, 2))

    def test_dirname_reads_the_sub_pool_and_only_it(self):
        root = tempfile.mkdtemp()
        try:
            for dirname, cash in (("pool_i", 1_000.0), ("pool_i2", 2_000.0), ("pool_i3", 3_000.0)):
                os.makedirs(os.path.join(root, dirname, "us_short_term_reversal"))
                with open(os.path.join(root, dirname, "us_short_term_reversal", "portfolio.json"), "w") as f:
                    json.dump({"cash": cash, "positions": {}, "last_processed_date": "2026-10-01"}, f)
            i2 = build_pool_i(root, {}, 90.0, today=date(2026, 10, 1), dirname="pool_i2")
            i3 = build_pool_i(root, {}, 90.0, today=date(2026, 10, 1), dirname="pool_i3")
            self.assertEqual(i2["usd"]["cash"], 2_000.0)
            self.assertEqual(i3["usd"]["cash"], 3_000.0)
            # a book with no starting_capital of its own falls back to ITS pool's size, not Pool I's
            self.assertEqual(i2["books"][0]["starting_capital"], pool_i_starting_capital("pool_i2"))
            self.assertEqual(i3["books"][0]["starting_capital"], pool_i_starting_capital("pool_i3"))
            self.assertEqual(i2["books"][0]["display_name"], "Short-Term Reversal (US)")
        finally:
            shutil.rmtree(root, ignore_errors=True)



class TestRunPoolISubPools(unittest.TestCase):
    """run_pool_i.py runs Pools I, I2 and I3 off one fetched data set (2026-10-19)."""

    def setUp(self):
        import run_pool_i
        self.rpi = run_pool_i
        self.root = tempfile.mkdtemp()
        self.original = run_pool_i.POOL_I_STATE_DIR
        run_pool_i.POOL_I_STATE_DIR = os.path.join(self.root, "pool_i")

    def tearDown(self):
        self.rpi.POOL_I_STATE_DIR = self.original
        shutil.rmtree(self.root, ignore_errors=True)

    def _seeded(self, dirname, key="cross_sectional_momentum_us"):
        with open(os.path.join(self.root, dirname, key, "portfolio.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_each_sub_pool_is_seeded_in_its_own_folder_at_its_own_multiple(self):
        for label, dirname in self.rpi.POOL_I_SUBPOOLS.items():
            self.rpi._seed_if_missing("cross_sectional_momentum_us", dirname, label)
        for dirname, multiple in (("pool_i", 1), ("pool_i2", 2), ("pool_i3", 3)):
            pf = self._seeded(dirname)
            self.assertAlmostEqual(pf["cash"], round(multiple * POOL_I_STARTING_CAPITAL_USD, 2), places=2)
            self.assertEqual(pf["cash"], pf["starting_capital"])
            self.assertEqual(pf["book_currency"], "USD")

    def test_an_existing_book_is_never_reseeded(self):
        self.rpi._seed_if_missing("cross_sectional_momentum_us", "pool_i2", "Pool I2")
        path = os.path.join(self.root, "pool_i2", "cross_sectional_momentum_us", "portfolio.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cash": 1.0}, f)
        self.rpi._seed_if_missing("cross_sectional_momentum_us", "pool_i2", "Pool I2")
        self.assertEqual(self._seeded("pool_i2"), {"cash": 1.0})

    def test_same_engine_call_except_sizing_cap_and_folder(self):
        from unittest import mock
        from deployment import paper_trading_engine as pte
        calls = []

        def fake_run_daily(key, strategy, **kw):
            calls.append((pte.PAPER_TRADING_STATE_DIR, type(strategy), kw))
            return {}
        original_dir = pte.PAPER_TRADING_STATE_DIR
        try:
            with mock.patch.object(pte, "run_daily", side_effect=fake_run_daily):
                for label, dirname in self.rpi.POOL_I_SUBPOOLS.items():
                    self.rpi.run_one("cross_sectional_momentum_us", [], {}, date(2026, 10, 1),
                                     dirname=dirname, label=label)
        finally:
            pte.PAPER_TRADING_STATE_DIR = original_dir
        self.assertEqual([os.path.basename(c[0]) for c in calls], ["pool_i", "pool_i2", "pool_i3"])
        self.assertEqual(len({c[1] for c in calls}), 1)                   # the same Strategy class
        caps = [c[2]["sizing_capital_cap"] for c in calls]
        self.assertEqual(caps, [pool_i_starting_capital(d) for d in ("pool_i", "pool_i2", "pool_i3")])
        for c in calls:
            self.assertEqual(c[2]["min_position_value_rupees"], 5.0)
            self.assertEqual(c[2]["execution_config"], pte.ExecutionRealismConfig(fill_timing="next_day_open"))

    def test_one_sub_pool_failing_does_not_stop_the_others(self):
        from unittest import mock
        import pandas as pd
        ran = []

        def fake_run_one(key, symbols, data, as_of, force=False, dirname="pool_i", label="Pool I", extra_fn=None):
            if dirname == "pool_i2":
                raise RuntimeError("boom")
            ran.append((key, dirname))
        frame = pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-10-01"]))
        with mock.patch.object(self.rpi, "get_swing_universe_us", return_value=["AAPL"]), \
                mock.patch.object(self.rpi, "fetch_all", return_value={"AAPL": frame}) as fetch, \
                mock.patch.object(self.rpi, "_guard", return_value=True), \
                mock.patch.object(self.rpi, "run_one", side_effect=fake_run_one):
            self.rpi.main()
        self.assertEqual(fetch.call_count, 1)                                # one fetch for all three pools
        for key in self.rpi.POOL_I_STRATEGIES:
            self.assertIn((key, "pool_i"), ran)
            self.assertIn((key, "pool_i3"), ran)
            self.assertNotIn((key, "pool_i2"), ran)

    def test_extra_columns_are_computed_once_per_strategy_and_shared(self):
        n = []
        fn = self.rpi._cached(lambda data: n.append(1) or {"x": 1})
        self.assertIs(fn({}), fn({}))
        self.assertEqual(len(n), 1)



class TestPoolsI2AndI3OnTheDashboard(unittest.TestCase):
    """Pools I2 and I3 get Pool E1's treatment: a combined Strategies-tab label with variant tabs, a
    pool breakdown row each, their own P&L statement lines carrying IBKR fees, and ledger rows."""

    def setUp(self):
        from types import SimpleNamespace

        from deployment.base import DeploymentStatus, ResearchVerdict
        self.root = tempfile.mkdtemp()
        self.pools = {}
        for dirname, multiple in (("pool_i", 1), ("pool_i2", 2), ("pool_i3", 3)):
            book_dir = os.path.join(self.root, dirname, "us_short_term_reversal")
            os.makedirs(book_dir)
            with open(os.path.join(book_dir, "portfolio.json"), "w") as f:
                json.dump({"cash": 900.0 * multiple, "starting_capital": pool_i_starting_capital(dirname),
                           "last_processed_date": "2026-10-01",
                           "positions": {"AAPL": {"entry_price": 100.0, "quantity": multiple,
                                                  "entry_date": "2026-09-25"}}}, f)
            with open(os.path.join(book_dir, "trades.jsonl"), "w") as f:
                f.write(json.dumps({"symbol": "MSFT", "entry_date": "2026-09-01", "exit_date": "2026-09-10",
                                    "entry_price": 100.0, "exit_price": 110.0, "quantity": 2 * multiple,
                                    "pnl": 20.0 * multiple}) + "\n")
            self.pools[dirname] = build_pool_i(self.root, {"AAPL": 105.0}, 90.0, today=date(2026, 10, 1),
                                               dirname=dirname, other_annual_income=1_400_000)
        self.record = SimpleNamespace(strategy_key="us_short_term_reversal", display_name="Short-Term Reversal (US)",
                                      strategy_id="SW-040", deployment_status=DeploymentStatus.PAPER_TRADING,
                                      research_verdict=ResearchVerdict.PASS, primary_experiment_id="EXP-096",
                                      research_verdict_source="", strategy_family="us_equity published strategy")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _kw(self):
        return {"pool_i": self.pools["pool_i"], "pool_i2": self.pools["pool_i2"], "pool_i3": self.pools["pool_i3"]}

    def test_combined_pool_label_and_variant_tabs(self):
        from dashboard.state_view import strategies_view
        row = strategies_view([self.record], "pool_d_vwap_fade", state_dir=self.root, **self._kw())[0]
        self.assertEqual(row["pool"], "Pool I, I2, I3")
        self.assertEqual([v["pool"] for v in row["variants"]], ["Pool I", "Pool I2", "Pool I3"])
        self.assertEqual([v["diff"] for v in row["variants"]], [False, True, True])
        self.assertIn("twice", row["variants"][1]["brief"])
        self.assertEqual({b["pool"] for b in row["pools_breakdown"]}, {"Pool I", "Pool I2", "Pool I3"})

    def test_without_sub_pool_books_it_is_plain_pool_i(self):
        from dashboard.state_view import strategies_view
        row = strategies_view([self.record], "pool_d_vwap_fade", state_dir=self.root,
                              pool_i=self.pools["pool_i"])[0]
        self.assertEqual(row["pool"], "Pool I")
        self.assertEqual(row["variants"], [])

    def test_statement_lines_carry_ibkr_fees_per_pool(self):
        from dashboard.state_view import statement_lines
        lines = statement_lines([], {}, {}, {}, [self.record], self.root, [], **self._kw())
        by_pool = {l["pool"]: l for l in lines}
        self.assertEqual(set(by_pool), {"Pool I", "Pool I2", "Pool I3"})
        for dirname, label in (("pool_i", "Pool I"), ("pool_i2", "Pool I2"), ("pool_i3", "Pool I3")):
            b = self.pools[dirname]["books"][0]
            fees_inr = round((b["booked"]["fees"] + b["unbooked"]["fees"]) * 90.0, 2)
            self.assertGreater(fees_inr, 0)
            self.assertAlmostEqual(by_pool[label]["charges"], fees_inr, places=2)
            self.assertEqual(by_pool[label]["detail"], {"us_fee": fees_inr})
            self.assertEqual(by_pool[label]["gst"], 0.0)
            self.assertEqual(by_pool[label]["type"], "US Equity")

    def test_net_on_the_strategies_tab_takes_the_fees_off(self):
        from dashboard.state_view import attach_net_pnl, statement_lines, strategies_view
        rows = strategies_view([self.record], "pool_d_vwap_fade", state_dir=self.root, **self._kw())
        lines = statement_lines([], {}, {}, {}, [self.record], self.root, [], **self._kw())
        attach_net_pnl(rows, lines)
        net = rows[0]["net"]
        self.assertAlmostEqual(net["charges"], sum(l["charges"] for l in lines), places=2)
        self.assertAlmostEqual(net["net"], net["gross"] - net["charges"] - net["tax"], places=2)
        self.assertIn("us_fee", net["detail"])

    def test_ledger_rows_are_labelled_by_their_own_pool_in_usd(self):
        from dashboard.state_view import _ledger
        rows = _ledger(self.root, [], {}, [], date(2026, 10, 1), **self._kw())
        self.assertEqual({r["pool"] for r in rows}, {"Pool I", "Pool I2", "Pool I3"})
        self.assertTrue(all(r["ccy"] == "USD" for r in rows))
        closed = {r["pool"]: r for r in rows if r["status"] == "Closed"}
        self.assertEqual(closed["Pool I3"]["qty"], 6.0)


class TestTeamTabAccountReadiness(unittest.TestCase):
    def test_the_desk_and_agent_exist(self):
        from dashboard.state_view import AGENTS, DESKS
        desk = next(d for d in DESKS if d["id"] == "accounts_desk")
        self.assertEqual(desk["name"], "Brokerage & Accounts")
        self.assertEqual(desk["icon"], "\U0001F3E6")
        agent = next(a for a in AGENTS if a["name"] == "Account Readiness")
        self.assertEqual(agent["desk"], "accounts_desk")
        self.assertEqual(agent["kind"], "AI")
        self.assertIsNone(agent["status_from"])
        self.assertEqual(agent["when"], "On request")
        self.assertIn("Interactive Brokers", agent["detail"])
        self.assertIn("Never opens an account", agent["detail"])

    def test_us_desk_and_pools_info_mention_the_sub_pools_and_the_new_rate(self):
        from dashboard.state_view import DESKS, POOLS_INFO
        us_desk = next(d for d in DESKS if d["id"] == "us_markets_desk")
        self.assertIn("I2", us_desk["blurb"])
        self.assertIn("I3", us_desk["blurb"])
        info = {p["pool"]: p["text"] for p in POOLS_INFO}
        self.assertIn("I2", info)
        self.assertIn("I3", info)
        self.assertIn("12.5%", info["I"])
        self.assertNotIn("20%", info["I"])


if __name__ == "__main__":
    unittest.main()
