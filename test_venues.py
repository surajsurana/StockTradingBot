"""
deployment/venues.py -- which broker executes a strategy's orders.

THE BUG THIS EXISTS TO PREVENT, which already happened once: funding Pool G was capped against the
Kite equity balance (Rs500) while its money sat at CoinDCX (Rs10,000), and the card directly above
the input said so. Two sources of truth disagreed -- is_crypto_record() matches a family STARTING
with "crypto" and means the Pool E lane specifically, while Pool G's family reads "AI judgment book
(crypto, Pool G)" -- and the wrong one was guarding real money.

The last class runs over the REAL registry, so the next strategy whose family does not announce
itself fails here rather than at the moment someone tries to fund it.
"""
import unittest
from types import SimpleNamespace

from deployment.deployment_manager import list_strategies
from deployment.venues import COINDCX, KITE, NONE, venue_label, venue_of


def _rec(key="x", family="swing research published strategy"):
    return SimpleNamespace(strategy_key=key, strategy_family=family)


class TestCryptoBooks(unittest.TestCase):
    def test_pool_e_lane_strategies_go_to_coindcx(self):
        self.assertEqual(venue_of(_rec("crypto_tsmom", "crypto research published strategy")), COINDCX)

    def test_pool_g_goes_to_coindcx_despite_its_family_not_starting_with_crypto(self):
        # the exact record that broke: the word is in the family but not at the front
        self.assertEqual(venue_of(_rec("portfolio_g", "AI judgment book (crypto, Pool G)")), COINDCX)

    def test_pool_g_is_matched_by_KEY_not_by_the_description(self):
        # a key is exact; a substring match on a human-written description is a guess, and guessing
        # is what put real money against the wrong account
        self.assertEqual(venue_of(_rec("portfolio_g", "anything at all")), COINDCX)
        self.assertEqual(venue_of(_rec("some_other", "AI judgment book (crypto, Pool Z)")), KITE)


class TestEquityAndUnsupported(unittest.TestCase):
    def test_an_india_swing_strategy_goes_to_kite(self):
        self.assertEqual(venue_of(_rec("ma_pullback")), KITE)

    def test_a_us_equity_book_has_no_venue_rather_than_a_wrong_one(self):
        # Pool I is paper-only and no US broker is wired, so there is no honest answer. Returning
        # Kite would mean funding US positions from the Indian equity account.
        us = _rec("minervini_trend_template_filter_us", "us_equity")   # the real family string
        self.assertEqual(venue_of(us), NONE)
        self.assertEqual(venue_label(us), "no broker")

    def test_labels_are_human_readable(self):
        self.assertEqual(venue_label(_rec("portfolio_g", "AI judgment book (crypto, Pool G)")), "CoinDCX")
        self.assertEqual(venue_label(_rec("ma_pullback")), "Kite")


class TestAgainstTheRealRegistry(unittest.TestCase):
    """Runs over the registry as it actually is, so a new strategy cannot quietly inherit the wrong
    account by being named in a way no rule anticipated."""

    def setUp(self):
        self.records = list_strategies()

    def test_every_registered_strategy_resolves_to_a_known_venue(self):
        self.assertTrue(self.records)
        for r in self.records:
            self.assertIn(venue_of(r), (KITE, COINDCX, NONE), r.strategy_key)

    def test_every_strategy_the_dashboard_calls_crypto_is_routed_to_coindcx(self):
        # this is the cross-check that was missing: the Strategies table's own notion of "Crypto"
        # (STRATEGY_BRIEFS) and the venue decision must never disagree again
        from dashboard.state_view import STRATEGY_BRIEFS
        checked = 0
        for r in self.records:
            kind = (STRATEGY_BRIEFS.get(r.strategy_key) or (None,))[0]
            if kind == "Crypto":
                self.assertEqual(venue_of(r), COINDCX, f"{r.strategy_key} shows as Crypto but routes elsewhere")
                checked += 1
        self.assertTrue(checked, "no strategy is labelled Crypto in the briefs")

    def test_pool_g_specifically_routes_to_coindcx_in_the_live_registry(self):
        pool_g = next((r for r in self.records if r.strategy_key == "portfolio_g"), None)
        self.assertIsNotNone(pool_g, "portfolio_g is not registered")
        self.assertEqual(venue_of(pool_g), COINDCX)

    def test_the_real_us_equity_books_have_no_venue(self):
        from deployment.base import is_us_equity_record
        us = [r for r in self.records if is_us_equity_record(r)]
        self.assertTrue(us, "no US equity strategies registered")
        for r in us:
            self.assertEqual(venue_of(r), NONE, r.strategy_key)

    def test_no_strategy_whose_family_mentions_crypto_is_routed_to_kite(self):
        for r in self.records:
            if "crypto" in str(getattr(r, "strategy_family", "")).lower():
                self.assertNotEqual(venue_of(r), KITE, r.strategy_key)


if __name__ == "__main__":
    unittest.main()
