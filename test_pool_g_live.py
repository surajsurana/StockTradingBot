"""
run_pool_g_live.py and deployment/crypto_executor.py -- Pool G against real money.

Three things matter most here and each has its own class:

  1. THE PERCENTAGES MUST MATCH PAPER. A live book with a tenth of paper's capital must take
     positions that are a tenth the size and the SAME percentage of its own book. This is the
     explicit requirement ("no difference in percentages in paper and live") and it is the thing
     most likely to break silently.
  2. The paper book must never be touched -- it is the control the live book is measured against.
  3. Nothing reaches the exchange without passing the guard, and nothing is sent without being
     recorded first.
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import portfolio_g.state as pg_state
import run_pool_g_live as rpg
from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_allocations import set_allocation
from portfolio_g.agent import MAX_SLEEVE_PCT
from portfolio_g.daily import _apply_decisions

SETTINGS = SimpleNamespace(LIVE_TRADING=True, COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs")


def _record(status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key="portfolio_g", display_name="Pool G", strategy_family="crypto",
                          research_verdict=ResearchVerdict.NOT_YET_EVALUATED,
                          research_verdict_source="no backtest possible -- LLM judgment",
                          deployment_status=status)


class TestThePercentagesMatchPaper(unittest.TestCase):
    """Pool G sizes at MAX_SLEEVE_PCT of its OWN cash, so a smaller book takes proportionally smaller
    positions at the identical percentage. Crypto quantities are fractional, so unlike equities
    nothing rounds down to zero and the match is exact."""

    @staticmethod
    def _buy(cash, price):
        portfolio = {"cash": cash, "positions": {}}
        decision = SimpleNamespace(symbol="BTC", action="BUY", conviction="high", reason="x")
        _apply_decisions(portfolio, [decision], {"BTC": price}, __import__("datetime").datetime(2026, 10, 7))
        return portfolio

    def test_a_tenth_of_the_capital_buys_a_tenth_of_the_position(self):
        paper = self._buy(1_000.0, 85_784.83)          # paper's own starting capital, in USDT
        live = self._buy(100.0, 85_784.83)             # a tenth of it
        ratio = live["positions"]["BTC"]["quantity"] / paper["positions"]["BTC"]["quantity"]
        self.assertAlmostEqual(ratio, 0.1, delta=0.001)

    def test_the_sleeve_is_the_same_percentage_of_the_book_at_every_size(self):
        # Not EXACTLY equal, and the reason is worth stating: quantities round to 6 decimals, which
        # quantises the sleeve slightly. It bites hardest on BTC, whose unit price is so large that
        # a tenth of a small book is a few ten-thousandths of a coin -- measured at 24.98% against an
        # intended 25% on a Rs10,000 book, where paper itself lands on 24.9977%. Every other coin is
        # exact to four decimal places. A 0.07% deviation is the crypto analogue of the equity
        # floor(), and it is three orders of magnitude milder (equities lose 92% of orders at 5%).
        for cash in (1_000.0, 103.7, 25.0, 5_000.0):
            for price in (85_784.83, 2_702.23, 1.51):
                book = self._buy(cash, price)
                spent = cash - book["cash"]
                self.assertAlmostEqual(spent / cash, MAX_SLEEVE_PCT, delta=0.0005,
                                       msg=f"cash={cash} price={price}")

    def test_a_small_book_does_not_round_down_to_nothing(self):
        # the equity books lose 92% of their orders to floor() at 5% capital; crypto is fractional,
        # so the percentage rule survives all the way down
        book = self._buy(103.7, 85_784.83)             # Rs10,000 at 96.42
        self.assertGreater(book["positions"]["BTC"]["quantity"], 0)
        self.assertAlmostEqual((103.7 - book["cash"]) / 103.7, MAX_SLEEVE_PCT, delta=0.0005)

    def test_the_live_book_is_seeded_at_the_rupees_assigned_converted_at_the_days_rate(self):
        d = tempfile.mkdtemp()
        path = os.path.join(rpg.live_dir(d), "portfolio.json")
        rpg._ensure_book(path, round(10_000 / 96.42, 4))
        with open(path) as f:
            book = json.load(f)
        self.assertAlmostEqual(book["cash"], 103.7, places=1)
        self.assertEqual(book["cash"], book["starting_capital"])


class TestItNeverTouchesThePaperBook(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "portfolio_g", 10_000, available_balance=10_000)

    def _run(self, **over):
        kwargs = dict(fetch_data_fn={}, fetch_prices_fn=lambda s: {}, api_key="k", usdinr=96.42,
                      state_dir=self.d, settings=SETTINGS)
        kwargs.update(over)
        return rpg.run_live(**kwargs)

    def test_the_cycle_is_pointed_at_the_live_book_and_restored_afterwards(self):
        before = pg_state.PORTFOLIO_G_STATE_DIR
        seen = {}

        def fake_cycle(*a, **k):
            seen["dir"] = pg_state.PORTFOLIO_G_STATE_DIR
            return {"status": "processed", "stopped": [], "sold": [], "bought": []}

        with patch.object(rpg, "list_strategies", return_value=[_record()]), \
             patch("portfolio_g.daily.run_pool_g_cycle", side_effect=fake_cycle):
            self._run()
        self.assertEqual(seen["dir"], rpg.live_dir(self.d))
        self.assertEqual(pg_state.PORTFOLIO_G_STATE_DIR, before)

    def test_the_redirection_is_restored_even_when_the_cycle_raises(self):
        before = pg_state.PORTFOLIO_G_STATE_DIR
        with patch.object(rpg, "list_strategies", return_value=[_record()]), \
             patch("portfolio_g.daily.run_pool_g_cycle", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self._run()
        self.assertEqual(pg_state.PORTFOLIO_G_STATE_DIR, before)

    def test_an_existing_live_book_is_never_reset(self):
        path = os.path.join(rpg.live_dir(self.d), "portfolio.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"cash": 42.5, "positions": {"BTC": {}}}, f)
        rpg._ensure_book(path, 999.0)
        with open(path) as f:
            self.assertEqual(json.load(f)["cash"], 42.5)


class TestADryRunLeavesNothingBehind(unittest.TestCase):
    """run_pool_g_cycle() applies its decisions to the book and SAVES it before anything is placed.
    So a run that places nothing would still leave the live book believing it holds positions that
    were never bought -- and that book is what the live track record is read from."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "portfolio_g", 10_000, available_balance=10_000)
        self.target = os.path.join(rpg.live_dir(self.d), "portfolio.json")

    def _cycle_that_buys(self, *a, **k):
        # stands in for the real cycle: writes a position into the book, as the real one does
        import json as _json
        os.makedirs(os.path.dirname(self.target), exist_ok=True)
        with open(self.target, "w") as f:
            _json.dump({"cash": 1.0, "positions": {"BTC": {"quantity": 0.0003}}}, f)
        return {"status": "processed", "stopped": [], "sold": [],
                "bought": [{"symbol": "BTC", "quantity": 0.0003, "price": 85_784.83}]}

    def test_a_dry_run_restores_the_book_it_found(self):
        with patch.object(rpg, "list_strategies", return_value=[_record()]),              patch("portfolio_g.daily.run_pool_g_cycle", side_effect=self._cycle_that_buys):
            result = rpg.run_live({}, lambda s: {}, "k", 96.42, state_dir=self.d, settings=SETTINGS)
        self.assertEqual(result["status"], "dry_run")
        with open(self.target) as f:
            book = json.load(f)
        self.assertEqual(book["positions"], {})          # the phantom position is gone
        self.assertAlmostEqual(book["cash"], 103.7, places=1)

    def test_a_dry_run_on_a_book_that_did_not_exist_leaves_none(self):
        # seeding happens inside the run, so a dry run must not leave a seeded book behind either
        rpg._restore(self.target, None)
        with patch.object(rpg, "list_strategies", return_value=[_record()]),              patch("portfolio_g.daily.run_pool_g_cycle", side_effect=self._cycle_that_buys):
            rpg.run_live({}, lambda s: {}, "k", 96.42, state_dir=self.d, settings=SETTINGS)
        with open(self.target) as f:
            self.assertEqual(json.load(f)["positions"], {})

    def test_a_dry_run_still_reports_what_it_would_have_done(self):
        with patch.object(rpg, "list_strategies", return_value=[_record()]),              patch("portfolio_g.daily.run_pool_g_cycle", side_effect=self._cycle_that_buys):
            result = rpg.run_live({}, lambda s: {}, "k", 96.42, state_dir=self.d, settings=SETTINGS)
        self.assertEqual([(o["side"], o["symbol"]) for o in result["orders"]], [("BUY", "BTC")])


class TestDivergenceIsSurfacedNotSilent(unittest.TestCase):
    """A live run writes the book before placing, so anything refused leaves the book ahead of
    reality. Reconciliation is not built yet; the gap must at least be visible."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "portfolio_g", 10_000, available_balance=10_000)
        self.cycle = {"status": "processed", "stopped": [], "sold": [],
                      "bought": [{"symbol": "BTC", "quantity": 0.0003, "price": 85_784.83}]}

    def _run(self, placed_ok):
        def fake_place(**kw):
            return SimpleNamespace(placed=placed_ok, order_id="o1" if placed_ok else "",
                                   fill_price=1.0, reasons=[] if placed_ok else ["guard refused"])
        with patch.object(rpg, "list_strategies", return_value=[_record()]),              patch("portfolio_g.daily.run_pool_g_cycle", return_value=self.cycle):
            return rpg.run_live({}, lambda s: {}, "k", 96.42, state_dir=self.d, settings=SETTINGS,
                                place_fn=fake_place, dry_run=False)

    def test_a_refused_order_is_reported_as_a_divergence(self):
        result = self._run(placed_ok=False)
        self.assertEqual(len(result["divergence"]), 1)
        self.assertIn("guard refused", result["divergence"][0])
        self.assertIn("BTC", result["divergence"][0])

    def test_everything_placed_means_no_divergence(self):
        self.assertEqual(self._run(placed_ok=True)["divergence"], [])


class TestItRefusesRatherThanGuesses(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _run(self, records, allocate=0, usdinr=96.42):
        if allocate:
            set_allocation(self.d, "portfolio_g", allocate, available_balance=allocate)
        with patch.object(rpg, "list_strategies", return_value=records):
            return rpg.run_live({}, lambda s: {}, "k", usdinr, state_dir=self.d, settings=SETTINGS)

    def test_unregistered_is_refused(self):
        self.assertIn("not in the registry", self._run([])["reason"])

    def test_a_paper_strategy_is_refused(self):
        r = self._run([_record(DeploymentStatus.PAPER_TRADING)], allocate=10_000)
        self.assertEqual(r["status"], "refused")
        self.assertIn("PAPER_TRADING", r["reason"].upper())

    def test_an_unfunded_strategy_is_refused(self):
        self.assertIn("no live capital", self._run([_record()])["reason"])

    def test_a_missing_exchange_rate_is_refused_rather_than_assumed(self):
        r = self._run([_record()], allocate=10_000, usdinr=0)
        self.assertIn("USD/INR", r["reason"])

    def test_a_refusal_runs_no_cycle_and_writes_nothing(self):
        with patch("portfolio_g.daily.run_pool_g_cycle") as cycle:
            self._run([_record(DeploymentStatus.PAPER_TRADING)], allocate=10_000)
        cycle.assert_not_called()
        self.assertFalse(os.path.exists(rpg.live_dir(self.d)))


class TestOrdersAndDryRun(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "portfolio_g", 10_000, available_balance=10_000)
        self.cycle = {"status": "processed",
                      "stopped": [{"symbol": "ETH", "quantity": 0.1, "exit_price": 2702.23}],
                      "sold": [{"symbol": "XRP", "quantity": 100.0, "exit_price": 1.51}],
                      "bought": [{"symbol": "BTC", "quantity": 0.0003, "price": 85784.83}]}

    def test_stops_sells_and_buys_all_become_orders_with_the_stop_first(self):
        orders = rpg.intended_orders(self.cycle)
        self.assertEqual([(o["side"], o["symbol"], o["why"]) for o in orders],
                         [("SELL", "ETH", "stop loss"), ("SELL", "XRP", "model sell"),
                          ("BUY", "BTC", "model buy")])

    def test_an_incomplete_order_is_dropped_rather_than_sent_with_a_hole(self):
        self.assertEqual(rpg.intended_orders({"bought": [{"symbol": "BTC", "quantity": None,
                                                          "price": 1.0}]}), [])
        self.assertEqual(rpg.intended_orders({}), [])

    def test_dry_run_is_the_default_and_places_nothing(self):
        placed = []
        with patch.object(rpg, "list_strategies", return_value=[_record()]), \
             patch("portfolio_g.daily.run_pool_g_cycle", return_value=self.cycle):
            result = rpg.run_live({}, lambda s: {}, "k", 96.42, state_dir=self.d, settings=SETTINGS,
                                  place_fn=lambda **kw: placed.append(kw))
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(len(result["orders"]), 3)
        self.assertEqual(placed, [])                  # the whole path ran; nothing was sent

    def test_placing_requires_dry_run_false_explicitly(self):
        sent = []

        def fake_place(**kw):
            sent.append(kw)
            return SimpleNamespace(placed=True, order_id="o1", fill_price=1.0, reasons=[])

        with patch.object(rpg, "list_strategies", return_value=[_record()]), \
             patch("portfolio_g.daily.run_pool_g_cycle", return_value=self.cycle):
            result = rpg.run_live({}, lambda s: {}, "k", 96.42, state_dir=self.d, settings=SETTINGS,
                                  place_fn=fake_place, dry_run=False)
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(p["placed"] for p in result["placed"]))
        # the quantity handed to the exchange is the one the cycle decided, in coin units
        self.assertEqual(sorted(s["quantity"] for s in sent), [0.0003, 0.1, 100.0])
        self.assertTrue(all(s["usdinr"] == 96.42 for s in sent))


if __name__ == "__main__":
    unittest.main()
