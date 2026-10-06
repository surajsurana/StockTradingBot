"""
live_guard.check_order_allowed(broker=...) -- the right credentials for the right venue.

The guard used to check KITE_API_KEY/KITE_ACCESS_TOKEN whatever the order was, so a correctly
configured CoinDCX order was refused for "no broker API key" while the key sat right there. Every
OTHER check is broker-agnostic and must stay that way: a new venue should inherit the kill switch,
the deployment-status check, the pilot gates and the caps without restating any of them.
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_guard import BROKER_CREDENTIALS, DEFAULT_BROKER, check_order_allowed


def setUpModule():
    """Point the guard's credential store at an empty directory for the whole module.

    Without this these tests read the REAL config/credentials.json, so they pass on a laptop that has
    no store and fail on the VPS that does -- the credentials under test would be supplied by the
    machine rather than by the test."""
    global _STORE_PATCH
    _STORE_PATCH = patch("deployment.live_guard.CONFIG_DIR", tempfile.mkdtemp())
    _STORE_PATCH.start()


def tearDownModule():
    _STORE_PATCH.stop()


def _record(status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key="alpha", display_name="Alpha", strategy_family="fam",
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


KITE_ONLY = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t",
                            COINDCX_API_KEY="", COINDCX_API_SECRET="")
CRYPTO_ONLY = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="", KITE_ACCESS_TOKEN="",
                              COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs")
BOTH = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t",
                       COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs")


def _check(settings, broker, state_dir, **over):
    kwargs = dict(settings=settings, record=_record(), state_dir=state_dir,
                  order_value_rupees=1000.0, current_live_exposure_rupees=0.0,
                  orders_placed_today=0, broker=broker)
    kwargs.update(over)
    return check_order_allowed(**kwargs)


class TestEachBrokerChecksItsOwnCredentials(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_crypto_order_passes_on_coindcx_credentials_alone(self):
        decision = _check(CRYPTO_ONLY, "coindcx", self.d)
        self.assertTrue(decision.allowed, decision.reasons)

    def test_a_crypto_order_is_refused_when_only_kite_is_configured(self):
        decision = _check(KITE_ONLY, "coindcx", self.d)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("CoinDCX" in r for r in decision.reasons), decision.reasons)
        self.assertFalse(any("Kite" in r for r in decision.reasons), decision.reasons)

    def test_an_equity_order_is_refused_when_only_coindcx_is_configured(self):
        decision = _check(CRYPTO_ONLY, "kite", self.d)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("Kite" in r for r in decision.reasons), decision.reasons)

    def test_the_refusal_names_the_venue_so_it_is_actionable(self):
        missing_secret = SimpleNamespace(LIVE_TRADING=True, COINDCX_API_KEY="ck", COINDCX_API_SECRET="")
        decision = _check(missing_secret, "coindcx", self.d)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("CoinDCX API secret" in r for r in decision.reasons), decision.reasons)

    def test_an_unknown_broker_falls_back_to_the_default_rather_than_waving_it_through(self):
        # a typo in a broker name must not become "no credentials to check"
        decision = _check(CRYPTO_ONLY, "nonsense-exchange", self.d)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("Kite" in r for r in decision.reasons), decision.reasons)

    def test_omitting_the_broker_keeps_the_old_kite_behaviour(self):
        decision = check_order_allowed(settings=KITE_ONLY, record=_record(), state_dir=self.d,
                                       order_value_rupees=1000.0, current_live_exposure_rupees=0.0,
                                       orders_placed_today=0)
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(DEFAULT_BROKER, "kite")


class TestEveryOtherCheckStaysBrokerAgnostic(unittest.TestCase):
    """A new venue must inherit all of these, not restate them."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_live_trading_off_stops_a_crypto_order_too(self):
        off = SimpleNamespace(LIVE_TRADING=False, COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs")
        self.assertFalse(_check(off, "coindcx", self.d).allowed)

    def test_the_kill_switch_stops_a_crypto_order_too(self):
        from deployment.live_guard import KILL_SWITCH_FILENAME
        open(os.path.join(self.d, KILL_SWITCH_FILENAME), "w").close()
        decision = _check(CRYPTO_ONLY, "coindcx", self.d)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("kill switch" in r for r in decision.reasons))

    def test_a_paper_strategy_cannot_place_a_crypto_order(self):
        decision = _check(CRYPTO_ONLY, "coindcx", self.d, record=_record(DeploymentStatus.PAPER_TRADING))
        self.assertFalse(decision.allowed)

    def test_the_caps_apply_to_a_crypto_order_too(self):
        decision = _check(CRYPTO_ONLY, "coindcx", self.d, order_value_rupees=9_000_000.0)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("per-order cap" in r for r in decision.reasons))

    def test_the_daily_count_applies_to_a_crypto_order_too(self):
        decision = _check(CRYPTO_ONLY, "coindcx", self.d, orders_placed_today=999)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("at the cap" in r for r in decision.reasons))

    def test_both_configured_lets_either_venue_through(self):
        for broker in ("kite", "coindcx"):
            self.assertTrue(_check(BOTH, broker, self.d).allowed, broker)


class TestTheBrokerTableIsTheOnlyPlaceToAddOne(unittest.TestCase):
    def test_every_broker_declares_at_least_one_credential(self):
        self.assertTrue(BROKER_CREDENTIALS)
        for broker, specs in BROKER_CREDENTIALS.items():
            self.assertTrue(specs, broker)
            for label, names in specs:
                self.assertTrue(label.strip(), broker)
                self.assertTrue(names, broker)

    def test_no_credential_VALUE_ever_appears_in_a_refusal(self):
        # refusals are logged and shown on the dashboard; a message that quoted the key would leak it
        decision = _check(SimpleNamespace(LIVE_TRADING=True, COINDCX_API_KEY="SUPERSECRETKEY",
                                          COINDCX_API_SECRET=""), "coindcx", tempfile.mkdtemp())
        self.assertNotIn("SUPERSECRETKEY", " ".join(decision.reasons))


if __name__ == "__main__":
    unittest.main()
