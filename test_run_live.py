"""
run_live.py -- one entry point, every promoted strategy, whatever pool it lives in.

The requirement this serves: "click P in paper trading and allocate capital, then it starts trading
with real money. No additional step." Until this existed, only Pool G could place an order; a
promoted Pool A strategy would have been eligible and then nothing would have run its book.
"""
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


if __name__ == "__main__":
    unittest.main()
