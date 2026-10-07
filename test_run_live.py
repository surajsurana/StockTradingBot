"""
run_live.py -- one entry point, every promoted strategy, whatever pool it lives in.

The requirement this serves: "click P in paper trading and allocate capital, then it starts trading
with real money. No additional step." Until this existed, only Pool G could place an order; a
promoted Pool A strategy would have been eligible and then nothing would have run its book.
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run_live
from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_allocations import set_allocation

SETTINGS = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t",
                           COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs")


def _rec(key, family="swing_research published strategy", status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key=key, display_name=key, strategy_family=family,
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


class TestWhoIsEligible(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_promoted_and_funded_is_eligible(self):
        set_allocation(self.d, "alpha", 50_000, available_balance=100_000)
        with patch.object(run_live, "list_strategies", return_value=[_rec("alpha")]):
            self.assertEqual([(r.strategy_key, a) for r, a in run_live.eligible(self.d)],
                             [("alpha", 50_000.0)])

    def test_promoted_but_unfunded_is_skipped_not_an_error(self):
        # a deliberate, safe intermediate state: it sizes every position to zero
        with patch.object(run_live, "list_strategies", return_value=[_rec("alpha")]):
            self.assertEqual(run_live.eligible(self.d), [])

    def test_funded_but_unpromoted_is_skipped(self):
        set_allocation(self.d, "alpha", 50_000, available_balance=100_000)
        with patch.object(run_live, "list_strategies",
                          return_value=[_rec("alpha", status=DeploymentStatus.PAPER_TRADING)]):
            self.assertEqual(run_live.eligible(self.d), [])

    def test_every_pool_is_eligible_on_the_same_terms(self):
        # the whole point: nothing about Pool G is special to the dispatcher
        for key, family in (("alpha", "swing_research published strategy"),
                            ("crypto_tsmom", "crypto research published strategy"),
                            ("portfolio_g", "AI judgment book (crypto, Pool G)")):
            d = tempfile.mkdtemp()
            set_allocation(d, key, 10_000, available_balance=10_000)
            with patch.object(run_live, "list_strategies", return_value=[_rec(key, family)]):
                self.assertEqual(len(run_live.eligible(d)), 1, key)


class TestItRefusesRatherThanGuesses(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_market_with_no_broker_is_refused(self):
        # Pool I is paper-only and no US broker is wired; defaulting it to Kite would mean funding
        # US positions from the Indian equity account
        out = run_live.run_one(_rec("minervini_us", "us_equity"), 10_000, settings=SETTINGS,
                               state_dir=self.d)
        self.assertEqual(out["status"], "refused")
        self.assertIn("No broker is wired", out["reason"])

    def test_missing_credentials_refuse_rather_than_trade(self):
        bare = SimpleNamespace(LIVE_TRADING=True)
        with patch("deployment.credential_store.credential", return_value=""):
            out = run_live.run_one(_rec("alpha"), 10_000, settings=bare, state_dir=self.d)
        self.assertEqual(out["status"], "refused")
        self.assertIn("credentials are not configured", out["reason"])


class TestOneFailureDoesNotStopTheRest(unittest.TestCase):
    def test_a_crashing_strategy_is_reported_and_the_loop_continues(self):
        # a shared runner that dies on the first problem would silently stop managing every other
        # live position
        d = tempfile.mkdtemp()
        for key in ("alpha", "beta"):
            set_allocation(d, key, 10_000, available_balance=100_000)

        def boom(record, rupees, **kw):
            if record.strategy_key == "alpha":
                raise RuntimeError("engine exploded")
            return {"key": record.strategy_key, "status": "dry_run"}

        with patch.object(run_live, "list_strategies", return_value=[_rec("alpha"), _rec("beta")]), \
             patch.object(run_live, "run_one", side_effect=boom):
            results = run_live.run_all(settings=SETTINGS, state_dir=d)
        by_key = {r["key"]: r for r in results}
        self.assertEqual(by_key["alpha"]["status"], "error")
        self.assertIn("engine exploded", by_key["alpha"]["reason"])
        self.assertEqual(by_key["beta"]["status"], "dry_run")     # the other one still ran


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_pool_g_goes_to_its_own_adapter(self):
        seen = {}

        def fake(*a, **kw):
            seen["called"] = True
            return {"status": "dry_run", "orders": []}

        with patch("run_pool_g_live.run_live", side_effect=fake), \
             patch("data.fetch_crypto.fetch_all_crypto_daily", return_value={}), \
             patch("data.fetch_crypto.fetch_usdinr_rate", return_value=96.42):
            out = run_live.run_one(_rec("portfolio_g", "AI judgment book (crypto, Pool G)"), 10_000,
                                   settings=SETTINGS, state_dir=self.d, client=object())
        self.assertTrue(seen.get("called"))
        self.assertEqual(out["venue"], "coindcx")

    def test_a_paper_engine_pool_goes_to_the_shared_runner(self):
        seen = {}

        def fake(key, **kw):
            seen["key"] = key
            seen["dry_run"] = kw.get("dry_run")
            return {"status": "dry_run", "orders": []}

        with patch("run_pool_live.run_live", side_effect=fake), \
             patch("data.fetch_historical.fetch_all", return_value={}), \
             patch("swing_research.universe.get_swing_universe", return_value=[]):
            out = run_live.run_one(_rec("ma_pullback"), 50_000, settings=SETTINGS,
                                   state_dir=self.d, client=object())
        self.assertEqual(seen["key"], "ma_pullback")
        self.assertEqual(out["venue"], "kite")

    def test_dry_run_is_the_default_on_every_path(self):
        import inspect
        self.assertIs(inspect.signature(run_live.run_one).parameters["dry_run"].default, True)
        self.assertIs(inspect.signature(run_live.run_all).parameters["dry_run"].default, True)


class TestEveryCryptoStrategyCanActuallyTrade(unittest.TestCase):
    """Pressing P on a crypto strategy must make it trade on CoinDCX. Before this, only Pool G had an
    adapter: the six Pool E/E1 strategies would have been promoted, funded, and then refused with
    "no spec" -- and if they HAD run, the shared runner hardcoded the equity executor, so their
    orders would have gone to Kite. The right book, the wrong exchange, and nothing to notice."""

    def test_every_PROMOTABLE_crypto_strategy_has_an_engine_plan(self):
        # Scoped to what P can actually act on. A strategy still in RESEARCH cannot reach PILOT_LIVE
        # at all -- the registry only allows RESEARCH -> PAPER_TRADING or ARCHIVED -- so having no
        # live adapter for one is correct, not a gap. crypto_xs_momentum and crypto_vol_managed are
        # both RESEARCH/REJECT and sit there deliberately.
        from deployment.base import DeploymentStatus
        from deployment.deployment_manager import list_strategies
        from deployment.venues import COINDCX, venue_of
        import run_pool_live as rpl
        promotable = (DeploymentStatus.PAPER_TRADING, DeploymentStatus.PILOT_LIVE,
                      DeploymentStatus.PRODUCTION)
        missing = []
        for r in list_strategies():
            if venue_of(r) != COINDCX or r.strategy_key == "portfolio_g":
                continue            # Pool G has its own adapter
            if r.deployment_status not in promotable:
                continue
            if rpl._engine_plan(r.strategy_key) is None:
                missing.append(r.strategy_key)
        self.assertEqual(missing, [], f"crypto strategies you could promote but not run: {missing}")

    def test_a_research_stage_strategy_cannot_be_promoted_anyway(self):
        # the reason the test above is scoped the way it is
        from deployment.base import DeploymentStatus, is_valid_transition
        self.assertFalse(is_valid_transition(DeploymentStatus.RESEARCH, DeploymentStatus.PILOT_LIVE))

    def test_a_pool_e_strategy_runs_against_its_own_folder_not_the_equity_one(self):
        import run_pool_live as rpl
        plan = rpl._engine_plan("crypto_tsmom")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["dirname"], "pool_e")        # not paper_trading
        self.assertIsNotNone(plan["execution"])            # same_day_close, as Pool E runs it

    def test_an_equity_strategy_keeps_the_equity_folder_and_defaults(self):
        import run_pool_live as rpl
        plan = rpl._engine_plan("ma_pullback")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["dirname"], "paper_trading")
        self.assertIsNone(plan["execution"])

    def test_a_crypto_order_goes_to_the_crypto_executor_not_kite(self):
        import run_pool_live as rpl
        with open(rpl.__file__, encoding="utf-8") as f:
            source = f.read()
        body = source[source.index("    from deployment.venues import COINDCX, venue_of"):]
        body = body[:body.index("placed = []")]
        self.assertIn("place_crypto_order", body)
        self.assertIn("place_live_order", body)
        self.assertIn("venue == COINDCX", body)


class TestAddingAPoolIsAnAdapter(unittest.TestCase):
    def test_the_dispatcher_names_only_pool_g_as_special(self):
        # every other pool shares one path; if that list grows, the shape has gone wrong
        with open(run_live.__file__, encoding="utf-8") as f:
            source = f.read()
        code = " ".join(line.split("#")[0] for line in source.splitlines())
        self.assertEqual(code.count("POOL_G_KEY"), 2)      # the constant, and the one branch

    def test_it_owns_no_broker_call(self):
        with open(run_live.__file__, encoding="utf-8") as f:
            code = " ".join(line.split("#")[0] for line in f.read().splitlines())
        for forbidden in ("place_order", "kiteconnect", "import requests"):
            self.assertNotIn(forbidden, code, forbidden)


class TestTheEquityPathCanActuallyTrade(unittest.TestCase):
    """Pressing P on an Indian strategy must make it trade on Kite. Three things stood between it and
    that on 2026-10-07, each of which would otherwise have been discovered with real money at stake."""

    def test_a_queued_entry_is_reported_but_never_sent(self):
        """An Indian strategy decides after the close and queues its entries for the next open,
        because that is when they can be bought. A queued entry has no price and no quantity yet --
        it is sized from tomorrow's open. intended_orders() used to emit those as orders anyway, so
        the after-close run would have tried to buy a None quantity at a None price."""
        import run_pool_live as rpl
        orders = rpl.intended_orders({
            "new_entries": [{"symbol": "ACME.NS", "quantity": 40, "entry_price": 310.0}],
            "new_pending_entries": [{"symbol": "BETA.NS", "stop_loss": 90.0}],
            "new_pending_exits": [{"symbol": "GAMMA.NS"}],
        })
        by_symbol = {o["symbol"]: o for o in orders}
        self.assertTrue(by_symbol["ACME.NS"]["placeable"])
        self.assertFalse(by_symbol["BETA.NS"]["placeable"])      # still reported...
        self.assertFalse(by_symbol["GAMMA.NS"]["placeable"])
        self.assertIsNone(by_symbol["BETA.NS"]["quantity"])      # ...because there is nothing to send

    def test_only_placeable_orders_reach_the_executor(self):
        import run_pool_live as rpl
        with open(rpl.__file__, encoding="utf-8") as f:
            source = f.read()
        body = source[source.index("    placed = []"):source.index("unplaced = [")]
        self.assertIn('if not order.get("placeable", True):', body)

    def test_resolve_at_open_is_how_a_queued_entry_becomes_an_order(self):
        import run_pool_live as rpl
        seen = {}

        def fake_resolve(key, strategy, **kw):
            seen["key"] = key
            return {"status": "processed", "new_entries": [], "new_exits": []}

        def must_not_run(*a, **kw):
            raise AssertionError("resolve-at-open must not detect new signals")

        d = tempfile.mkdtemp()
        set_allocation(d, "ma_pullback", 50_000, available_balance=100_000)
        with patch.object(rpl.pte, "resolve_pending_fills_at_open", side_effect=fake_resolve), \
             patch.object(rpl.pte, "run_daily", side_effect=must_not_run), \
             patch.object(rpl, "list_strategies", return_value=[_rec("ma_pullback")]):
            out = rpl.run_live("ma_pullback", fetch_data_fn=lambda: {}, state_dir=d,
                               dry_run=True, resolve_at_open=True)
        self.assertEqual(seen.get("key"), "ma_pullback")
        self.assertEqual(out["status"], "dry_run")

    def test_the_live_equity_book_gets_the_same_notional_floor_as_the_paper_one(self):
        # otherwise the live book takes the Rs3,532 positions the paper books were taking, with real
        # money, paying the flat DP charge for real
        import run_pool_live as rpl
        from swing_research.broker_costs import min_viable_notional
        self.assertAlmostEqual(rpl._engine_plan("ma_pullback")["min_value"], min_viable_notional())
        self.assertEqual(rpl._engine_plan("crypto_tsmom")["min_value"], 5.0)   # crypto keeps its own


class TestAFreshLiveBookIsUsable(unittest.TestCase):
    """The first run of a new live book died in _resolve_pending_fills with "'list' object has no
    attribute 'keys'": _ensure_book seeded pending_entries/pending_exits as lists while the engine
    reads them as dicts keyed by symbol. setdefault() could not rescue it -- the key was present,
    just the wrong type -- so this would have broken the first live run of every pool except G,
    which has its own adapter."""

    def test_the_seeded_book_has_the_shape_the_engine_reads(self):
        import json
        import run_pool_live as rpl
        d = tempfile.mkdtemp()
        rpl._ensure_book(d, "alpha", 100_000.0)
        with open(os.path.join(d, "alpha", "portfolio.json"), encoding="utf-8") as f:
            book = json.load(f)
        self.assertIsInstance(book["pending_entries"], dict)
        self.assertIsInstance(book["pending_exits"], dict)
        self.assertIsInstance(book["positions"], dict)
        self.assertEqual(book["cash"], 100_000.0)

    def test_it_matches_what_the_engine_itself_would_create(self):
        import json
        import deployment.paper_trading_engine as pte
        import run_pool_live as rpl
        d = tempfile.mkdtemp()
        rpl._ensure_book(d, "alpha", 100_000.0)
        with open(os.path.join(d, "alpha", "portfolio.json"), encoding="utf-8") as f:
            seeded = json.load(f)
        source = open(pte.__file__, encoding="utf-8").read()
        self.assertIn('"pending_entries": {}, "pending_exits": {},', source)
        for key in ("pending_entries", "pending_exits", "positions"):
            self.assertIsInstance(seeded[key], dict, key)

    def test_an_existing_book_is_never_overwritten(self):
        import json
        import run_pool_live as rpl
        d = tempfile.mkdtemp()
        rpl._ensure_book(d, "alpha", 100_000.0)
        path = os.path.join(d, "alpha", "portfolio.json")
        with open(path, encoding="utf-8") as f:
            book = json.load(f)
        book["cash"] = 42.0
        with open(path, "w", encoding="utf-8") as f:
            json.dump(book, f)
        rpl._ensure_book(d, "alpha", 100_000.0)      # its cash is the real traded balance
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cash"], 42.0)


class TestOrdersCannotGoIntoAClosedMarket(unittest.TestCase):
    """The live cron fired at 08:35 and 20:35 IST, set when the only live book was crypto. Both are
    outside the NSE's 09:15-15:30. Checked in the guard rather than left to the cron being right."""

    def _decision(self, when, broker):
        from deployment.live_guard import check_order_allowed
        d = tempfile.mkdtemp()
        with patch("deployment.live_guard._credential", return_value="x"):
            return check_order_allowed(settings=SETTINGS, record=_rec("alpha"), state_dir=d,
                                       order_value_rupees=10_000, current_live_exposure_rupees=0,
                                       orders_placed_today=0, now=when, broker=broker)

    def test_an_equity_order_outside_market_hours_is_refused(self):
        from datetime import datetime
        for when in (datetime(2026, 10, 7, 8, 35), datetime(2026, 10, 7, 20, 35),
                     datetime(2026, 10, 10, 11, 0)):          # a Saturday
            decision = self._decision(when, "kite")
            self.assertFalse(decision.allowed, when)
            self.assertTrue(any("NSE is closed" in r for r in decision.reasons), decision.reasons)

    def test_an_equity_order_inside_market_hours_passes_this_check(self):
        from datetime import datetime
        decision = self._decision(datetime(2026, 10, 7, 11, 0), "kite")
        self.assertFalse(any("NSE is closed" in r for r in decision.reasons), decision.reasons)

    def test_crypto_is_never_gated_on_market_hours(self):
        from datetime import datetime
        for when in (datetime(2026, 10, 7, 3, 0), datetime(2026, 10, 11, 23, 0)):
            decision = self._decision(when, "coindcx")
            self.assertFalse(any("NSE is closed" in r for r in decision.reasons), decision.reasons)


class TestTheBookAndTheBrokerSpellSymbolsDifferently(unittest.TestCase):
    """The books hold RELIANCE.NS; Kite reports RELIANCE. reconcile() key-matched the two, so every
    equity position read as "the book holds 40 but the exchange reports 0" -- which halts the run."""

    def _book(self, positions):
        import json
        import os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "portfolio.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"positions": positions}, f)
        return path

    def test_the_two_spellings_reconcile(self):
        from deployment.reconciliation import reconcile_against_exchange
        from deployment.venues import KITE
        book = self._book({"RELIANCE.NS": {"quantity": 40}})
        client = SimpleNamespace(balances=lambda: [{"currency": "RELIANCE", "balance": 40}])
        self.assertTrue(reconcile_against_exchange(book, client, venue=KITE).ok)

    def test_a_real_shortfall_is_still_caught(self):
        from deployment.reconciliation import reconcile_against_exchange
        from deployment.venues import KITE
        book = self._book({"RELIANCE.NS": {"quantity": 40}})
        client = SimpleNamespace(balances=lambda: [{"currency": "RELIANCE", "balance": 10}])
        self.assertFalse(reconcile_against_exchange(book, client, venue=KITE).ok)

    def test_crypto_is_unaffected_by_the_translation(self):
        from deployment.reconciliation import reconcile_against_exchange
        from deployment.venues import COINDCX
        book = self._book({"BTC": {"quantity": 0.00031}})
        client = SimpleNamespace(balances=lambda: [{"currency": "BTC", "balance": 0.00031}])
        self.assertTrue(reconcile_against_exchange(book, client, venue=COINDCX).ok)


class TestOneCronLinePerMarket(unittest.TestCase):
    def test_a_venue_filter_runs_only_that_market(self):
        from deployment.venues import COINDCX, KITE
        d = tempfile.mkdtemp()
        for key in ("alpha", "portfolio_g"):
            set_allocation(d, key, 10_000, available_balance=100_000)
        records = [_rec("alpha"), _rec("portfolio_g", "AI judgment book (crypto, Pool G)")]
        with patch.object(run_live, "list_strategies", return_value=records), \
             patch.object(run_live, "run_one",
                          side_effect=lambda r, a, **kw: {"key": r.strategy_key, "status": "dry_run"}):
            equity = run_live.run_all(settings=SETTINGS, state_dir=d, venues={KITE})
            crypto = run_live.run_all(settings=SETTINGS, state_dir=d, venues={COINDCX})
            both = run_live.run_all(settings=SETTINGS, state_dir=d)
        self.assertEqual([r["key"] for r in equity], ["alpha"])
        self.assertEqual([r["key"] for r in crypto], ["portfolio_g"])
        self.assertEqual(sorted(r["key"] for r in both), ["alpha", "portfolio_g"])


if __name__ == "__main__":
    unittest.main()
