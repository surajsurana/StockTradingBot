"""
deployment/live_guard.py -- the gate every real order must pass (Gate H, 2026-10-06).

The property under test throughout is FAIL CLOSED: there must be no input, however malformed or
absent, that turns "I cannot tell" into "yes". These tests are deliberately adversarial about that,
because a live-trading guard whose failure mode is "allow" is worse than no guard at all.
"""
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_guard import (DEFAULT_MAX_ORDER_PCT_OF_CAPITAL, DEFAULT_MAX_ORDER_VALUE_RUPEES,
                                   DEFAULT_MAX_ORDERS_PER_DAY, KILL_SWITCH_FILENAME,
                                   check_order_allowed, kill_switch_engaged, kill_switch_path)


def _settings(**over):
    # No absolute per-order ceiling by default, matching the shipped default: since 2026-10-07 the
    # per-order cap is a percentage of the book, and an absolute rupee figure is an optional extra
    # for someone who wants one. Tests that care about the absolute ceiling set it themselves.
    base = dict(LIVE_TRADING=True, KITE_API_KEY="key", KITE_ACCESS_TOKEN="token",
                LIVE_MAX_EXPOSURE_RUPEES=25_000.0, LIVE_MAX_ORDERS_PER_DAY=20)
    base.update(over)
    return SimpleNamespace(**base)


def _record(status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key="s1", display_name="S1", strategy_family="fam",
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


# A fixed in-hours timestamp. Since 2026-10-07 the guard refuses an equity order outside the NSE's
# 09:15-15:30, so a test that leaves the clock to chance passes before half past three and fails
# after it. Market hours have their own tests; these are about the other switches.
IN_HOURS = datetime(2026, 10, 7, 11, 0)


# A funded book for the cap arithmetic to work on. Since 2026-10-07 the per-order cap is a FRACTION
# of what the strategy was assigned, not a rupee figure -- a rupee figure is a guess about capital
# and is wrong the moment capital changes, which is how a Rs5,000 cap came to refuse every equity
# order. Rs10,000 assigned at the 50% default gives a Rs5,000 per-order cap, so the numbers these
# tests were written around still read the same.
FUNDED = 10_000.0


def _check(settings=None, record=None, state_dir=None, value=1_000.0, exposure=0.0,
           placed=0, eligibility=None, now=IN_HOURS, funded=FUNDED):
    return check_order_allowed(settings=settings or _settings(), record=record or _record(),
                               state_dir=state_dir or tempfile.mkdtemp(),
                               order_value_rupees=value, current_live_exposure_rupees=exposure,
                               orders_placed_today=placed, eligibility=eligibility, now=now,
                               allocated_rupees=funded)


class TestTheHappyPathIsNarrow(unittest.TestCase):
    def test_a_fully_configured_live_strategy_is_allowed(self):
        d = _check()
        self.assertTrue(d.allowed, d.reasons)
        self.assertEqual(d.reasons, [])
        self.assertTrue(bool(d))                      # truthy, so `if check_order_allowed(...)` reads naturally

    def test_production_is_allowed_as_well_as_pilot_live(self):
        self.assertTrue(_check(record=_record(DeploymentStatus.PRODUCTION)).allowed)


class TestEachSwitchBlocksOnItsOwn(unittest.TestCase):
    def test_live_trading_off_blocks(self):
        d = _check(settings=_settings(LIVE_TRADING=False))
        self.assertFalse(d.allowed)
        self.assertTrue(any("LIVE_TRADING" in r for r in d.reasons))

    def test_live_trading_merely_truthy_is_not_enough(self):
        # "1", "true", 1 all look enabled to a careless check -- only the boolean True counts
        for sneaky in ("True", "1", 1, "yes", [1]):
            self.assertFalse(_check(settings=_settings(LIVE_TRADING=sneaky)).allowed, repr(sneaky))

    def test_a_missing_live_trading_attribute_blocks(self):
        s = _settings()
        del s.LIVE_TRADING
        self.assertFalse(_check(settings=s).allowed)

    def test_kill_switch_blocks(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, KILL_SWITCH_FILENAME), "w").close()
        decision = _check(state_dir=d)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("kill switch" in r for r in decision.reasons))

    def test_missing_credentials_block(self):
        self.assertFalse(_check(settings=_settings(KITE_API_KEY="")).allowed)
        self.assertFalse(_check(settings=_settings(KITE_ACCESS_TOKEN="")).allowed)
        self.assertFalse(_check(settings=_settings(KITE_API_KEY="   ")).allowed)   # whitespace is not a key

    def test_a_paper_trading_strategy_can_never_place_a_live_order(self):
        d = _check(record=_record(DeploymentStatus.PAPER_TRADING))
        self.assertFalse(d.allowed)
        self.assertTrue(any("PAPER_TRADING" in r for r in d.reasons))

    def test_research_and_archived_strategies_are_blocked(self):
        for status in (DeploymentStatus.RESEARCH, DeploymentStatus.ARCHIVED):
            self.assertFalse(_check(record=_record(status)).allowed, status)

    def test_losing_pilot_eligibility_stops_further_orders(self):
        # re-checked per order on purpose: a strategy that drifts out of the gates stops trading
        # rather than coasting on the decision made the day it was promoted
        stale = SimpleNamespace(eligible=False, reasons=["Only 3 paper trades recorded."])
        d = _check(eligibility=stale)
        self.assertFalse(d.allowed)
        self.assertTrue(any("no longer passes" in r for r in d.reasons))

    def test_passing_eligibility_does_not_block(self):
        self.assertTrue(_check(eligibility=SimpleNamespace(eligible=True, reasons=[])).allowed)


class TestCaps(unittest.TestCase):
    def test_order_value_over_the_cap_blocks(self):
        cap = DEFAULT_MAX_ORDER_PCT_OF_CAPITAL * FUNDED
        self.assertFalse(_check(value=cap + 0.01).allowed)
        self.assertTrue(_check(value=cap).allowed)              # exactly at the cap is allowed

    def test_exposure_cap_counts_the_order_being_placed(self):
        # 24,500 already out plus a 1,000 order is over the 25,000 ceiling -- the new order counts
        s = _settings(LIVE_MAX_EXPOSURE_RUPEES=25_000.0)
        self.assertFalse(_check(settings=s, value=1_000.0, exposure=24_500.0).allowed)
        self.assertTrue(_check(settings=s, value=500.0, exposure=24_500.0).allowed)

    def test_daily_order_count_cap_blocks_at_the_limit_not_after_it(self):
        self.assertTrue(_check(placed=19).allowed)
        self.assertFalse(_check(placed=20).allowed)

    def test_zero_and_negative_order_values_are_refused(self):
        for bad in (0, -1, -5_000.0):
            self.assertFalse(_check(value=bad).allowed, bad)

    def test_absent_cap_settings_fall_back_to_the_conservative_defaults(self):
        s = _settings()
        for name in ("LIVE_MAX_EXPOSURE_RUPEES", "LIVE_MAX_ORDERS_PER_DAY"):
            delattr(s, name)
        cap = DEFAULT_MAX_ORDER_PCT_OF_CAPITAL * FUNDED
        self.assertTrue(_check(settings=s, value=cap).allowed)
        self.assertFalse(_check(settings=s, value=cap + 1).allowed)
        self.assertFalse(_check(settings=s, placed=DEFAULT_MAX_ORDERS_PER_DAY).allowed)

    def test_a_garbage_cap_setting_falls_back_rather_than_disabling_the_cap(self):
        # the dangerous reading of a bad config would be "no cap" -- it must be "the default cap"
        cap = DEFAULT_MAX_ORDER_PCT_OF_CAPITAL * FUNDED
        for junk in ("lots", None, "", -1):
            s = _settings(LIVE_MAX_ORDER_PCT_OF_CAPITAL=junk)
            self.assertFalse(_check(settings=s, value=cap + 1).allowed, repr(junk))

    def test_the_cap_scales_with_the_book_instead_of_being_re_tuned(self):
        """The whole point. The same setting governs a Rs10,000 crypto book and a Rs2.4 lakh equity
        one, and neither needs a number changed when the other is funded."""
        self.assertTrue(_check(funded=10_000.0, value=8_000.0).allowed)
        self.assertFalse(_check(funded=10_000.0, value=8_001.0).allowed)
        big = _settings(LIVE_MAX_EXPOSURE_RUPEES=1_000_000.0)
        self.assertTrue(_check(settings=big, funded=240_607.0, value=12_496.0).allowed)
        # 56.8% of the book is the largest position ever observed in paper -- it must still pass,
        # or the backstop is quietly acting as a sizing control
        self.assertTrue(_check(settings=big, funded=240_607.0, value=0.568 * 240_607.0).allowed)
        self.assertFalse(_check(settings=big, funded=240_607.0, value=240_000.0).allowed)

    def test_an_unknown_allocation_refuses_rather_than_lifting_the_cap(self):
        """Fail closed. "Cannot work out the cap" must never resolve to "there is no cap"."""
        d = _check(funded=0.0)      # and no absolute ceiling set, so nothing is left to bound it
        self.assertFalse(d.allowed)
        self.assertTrue(any("not known" in r for r in d.reasons), d.reasons)

    def test_an_absolute_ceiling_still_applies_when_someone_sets_one(self):
        s = _settings(LIVE_MAX_ORDER_VALUE_RUPEES=2_000.0)
        self.assertFalse(_check(settings=s, funded=240_607.0, value=2_001.0).allowed)
        self.assertTrue(_check(settings=s, funded=240_607.0, value=2_000.0).allowed)

    def test_unreadable_numbers_refuse_rather_than_raise(self):
        for bad in ("abc", None, object()):
            self.assertFalse(_check(value=bad).allowed, repr(bad))
            self.assertFalse(_check(exposure=bad).allowed, repr(bad))
            self.assertFalse(_check(placed=bad).allowed, repr(bad))


class TestRefusalsAreAlwaysExplained(unittest.TestCase):
    def test_every_refusal_carries_at_least_one_reason(self):
        cases = [dict(settings=_settings(LIVE_TRADING=False)), dict(value=0),
                 dict(value="abc"), dict(record=_record(DeploymentStatus.RESEARCH)),
                 dict(placed=999), dict(value=99_999.0)]
        for case in cases:
            d = _check(**case)
            self.assertFalse(d.allowed, case)
            self.assertTrue(d.reasons, case)
            self.assertTrue(all(isinstance(r, str) and r.strip() for r in d.reasons), case)

    def test_several_problems_are_all_reported_not_just_the_first(self):
        d = _check(settings=_settings(LIVE_TRADING=False, KITE_API_KEY=""),
                   record=_record(DeploymentStatus.PAPER_TRADING), value=99_999.0)
        self.assertGreaterEqual(len(d.reasons), 4)

    def test_a_refusal_is_falsy(self):
        self.assertFalse(bool(_check(value=0)))


class TestKillSwitchHelpers(unittest.TestCase):
    def test_engaged_only_when_the_file_is_present(self):
        d = tempfile.mkdtemp()
        self.assertFalse(kill_switch_engaged(d))
        open(kill_switch_path(d), "w").close()
        self.assertTrue(kill_switch_engaged(d))

    def test_an_unreadable_state_dir_counts_as_engaged(self):
        from unittest.mock import patch
        with patch("os.path.exists", side_effect=OSError("boom")):
            self.assertTrue(kill_switch_engaged("/anything"))


class TestTheModuleCannotTrade(unittest.TestCase):
    def test_it_imports_no_broker_or_execution_code(self):
        # structural guarantee: the thing that decides whether to trade has no means to trade
        import deployment.live_guard as lg
        with open(lg.__file__, encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("import requests", "from execution", "import execution",
                          "kiteconnect", "place_order"):
            self.assertNotIn(forbidden, source, forbidden)


class TestTheProportionalityRule(unittest.TestCase):
    """ARCHITECTURE.md, FUND ALLOCATION: a live book behaves as a scaled copy of its paper book, so
    everything that decides how much to trade is a PERCENTAGE of that book's own capital, never a
    rupee amount. This has been got wrong more than once; these tests are what stop it recurring."""

    def test_the_same_order_percentage_is_allowed_on_any_book_size(self):
        # 30% of the book passes whether the book is Rs5,000 or Rs25 lakh -- that IS the rule
        big = _settings(LIVE_MAX_EXPOSURE_RUPEES=10_000_000.0)
        for capital in (5_000.0, 100_000.0, 240_607.0, 2_500_000.0):
            self.assertTrue(_check(settings=big, funded=capital, value=0.30 * capital).allowed,
                            f"30% of Rs{capital:,.0f} was refused")

    def test_the_same_order_percentage_is_refused_on_any_book_size(self):
        big = _settings(LIVE_MAX_EXPOSURE_RUPEES=10_000_000.0)
        for capital in (5_000.0, 100_000.0, 240_607.0, 2_500_000.0):
            self.assertFalse(_check(settings=big, funded=capital, value=0.95 * capital).allowed,
                             f"95% of Rs{capital:,.0f} was allowed")

    def test_the_backstop_clears_the_largest_position_ever_observed(self):
        """A backstop that binds in normal operation is a bug. 56.8% of book is the real maximum
        across 419 paper trades (ma_pullback), so the cap must sit above it."""
        from deployment.live_guard import DEFAULT_MAX_ORDER_PCT_OF_CAPITAL
        self.assertGreater(DEFAULT_MAX_ORDER_PCT_OF_CAPITAL, 0.568)

    def test_no_rupee_denominated_cap_is_applied_by_default(self):
        """The absolute ceiling is opt-in. A rupee default is the thing that broke: it is a guess
        about capital, and wrong the moment capital changes."""
        from deployment.live_guard import DEFAULT_MAX_ORDER_VALUE_RUPEES
        self.assertEqual(DEFAULT_MAX_ORDER_VALUE_RUPEES, 0.0)

    def test_the_rule_is_written_down_where_the_next_person_will_look(self):
        with open("ARCHITECTURE.md", encoding="utf-8") as f:
            doc = f.read()
        self.assertIn("## FUND ALLOCATION", doc)
        self.assertIn("PERCENTAGE OF THAT BOOK'S OWN CAPITAL", doc)


if __name__ == "__main__":
    unittest.main()
