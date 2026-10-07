"""
Unit tests for deployment/live_readiness.py -- why a promoted strategy is not trading, and whether
the answer is money or a mistake.

The distinction is the whole point. "It isn't trading" stayed unexplained for weeks because a stale
access token and an empty account look identical from outside, and only one of them is Suraj's
decision to make.

    python test_live_readiness.py
"""

import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_readiness import CONFIG, MONEY, check

LIVE = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t",
                       COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs",
                       LIVE_MAX_ORDER_VALUE_RUPEES=50_000.0,
                       LIVE_MAX_EXPOSURE_RUPEES=5_000_000.0)


def _rec(key="alpha", family="swing_research published strategy", status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key=key, display_name=key, strategy_family=family,
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


def _check(record=None, rupees=200_000.0, settings=LIVE, cash=1_000_000.0, natural=12_500.0):
    with patch("deployment.live_readiness._natural_position", return_value=natural), \
         patch("deployment.credential_store.credential",
               side_effect=lambda name, d, s: getattr(s, name, "")):
        return check(record or _rec(), rupees, settings=settings, state_dir=tempfile.mkdtemp(),
                     broker_cash=cash, now=datetime(2026, 10, 7, 11, 0))


class TestTheSentenceThisExistsToProduce(unittest.TestCase):
    def test_everything_configured_and_funded_is_simply_ready(self):
        r = _check()
        self.assertTrue(r.ready, [b.detail for b in r.blockers])
        self.assertFalse(r.money_only)      # money_only means blocked ON money, not ready

    def test_only_an_empty_account_reads_as_waiting_on_money(self):
        r = _check(cash=500.0)
        self.assertTrue(r.money_only)
        self.assertEqual(r.of_kind(CONFIG), [])
        self.assertIn("holds Rs500", r.of_kind(MONEY)[0].detail)

    def test_one_configuration_fault_stops_it_reading_as_money(self):
        """The guard rail. A single misconfiguration must never be reported as 'just needs funding',
        because funding it would change nothing and the real fault would stay hidden."""
        bare = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="")
        r = _check(settings=bare, cash=500.0)
        self.assertFalse(r.money_only)
        self.assertTrue(r.of_kind(CONFIG))
        self.assertTrue(r.of_kind(MONEY))   # both are reported; the verdict is just not "money only"


class TestEachBlockerIsFoundAndClassified(unittest.TestCase):
    def test_live_trading_off_is_configuration(self):
        off = SimpleNamespace(LIVE_TRADING=False, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t")
        r = _check(settings=off)
        self.assertTrue(any(b.kind == CONFIG and "LIVE_TRADING" in b.detail for b in r.blockers))

    def test_a_missing_access_token_is_configuration_not_money(self):
        bare = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="")
        r = _check(settings=bare)
        self.assertTrue(any(b.kind == CONFIG and "access token" in b.detail for b in r.blockers))

    def test_a_strategy_that_was_never_promoted_says_so(self):
        r = _check(_rec(status=DeploymentStatus.PAPER_TRADING))
        self.assertTrue(any("not live" in b.detail for b in r.blockers))
        self.assertIn("Press P", " ".join(b.fix for b in r.blockers))

    def test_no_capital_assigned_is_money(self):
        r = _check(rupees=0.0)
        self.assertTrue(any(b.kind == MONEY and "No capital" in b.detail for b in r.blockers))

    def test_an_unreadable_balance_is_reported_not_assumed_fine(self):
        r = _check(cash=None)
        self.assertTrue(any(b.kind == CONFIG and "could not be read" in b.detail for b in r.blockers))

    def test_a_market_with_no_broker_stops_everything_else_being_reported(self):
        r = _check(_rec("minervini_us", "us_equity"))
        self.assertEqual(len(r.blockers), 1)       # no point listing caps for a book that cannot trade
        self.assertIn("No broker is wired", r.blockers[0].detail)


class TestTheCapsAreCheckedAgainstWhatItWouldActuallyTrade(unittest.TestCase):
    """The check that would have saved the first live equity week. A per-order cap below the smallest
    position the engine can open refuses every order the strategy will ever produce -- one at a time,
    so it reads as bad luck rather than a setting."""

    def test_a_small_book_is_a_funding_problem_not_a_cap_misconfiguration(self):
        """The cap is a PERCENTAGE, so on a small book it is always below the floor -- and on a small
        book the floor is what stops the trade anyway. Reporting both made a funding problem read as
        a misconfiguration, so the strategy showed as BLOCKED and sent you to a setting when the
        answer was capital. This is the exact Rs10,000 SW-016 case."""
        r = _check(rupees=10_000.0, natural=662.0)
        self.assertEqual(r.of_kind(CONFIG), [], [b.detail for b in r.of_kind(CONFIG)])
        self.assertTrue(r.money_only)
        self.assertIn("Assign about", " ".join(b.fix for b in r.of_kind(MONEY)))

    def test_the_default_percentage_cap_clears_the_floor_on_a_real_book(self):
        # 50% of Rs240,607 is Rs120,303, comfortably above the Rs12,469 floor -- the point of making
        # it a percentage is that this needs no tuning per book
        r = _check(rupees=240_607.0, natural=12_469.0)
        self.assertEqual([b.detail for b in r.of_kind(CONFIG)], [])

    def test_a_per_order_cap_below_this_strategys_own_position_size_is_a_fault(self):
        tight = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t",
                                LIVE_MAX_ORDER_VALUE_RUPEES=20_000.0,
                                LIVE_MAX_EXPOSURE_RUPEES=5_000_000.0)
        r = _check(settings=tight, natural=35_000.0)
        self.assertTrue(any("over the Rs20,000 per-order cap" in b.detail for b in r.blockers))

    def test_an_exposure_cap_below_the_allocation_is_a_fault(self):
        tight = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t",
                                LIVE_MAX_ORDER_VALUE_RUPEES=50_000.0,
                                LIVE_MAX_EXPOSURE_RUPEES=25_000.0)
        r = _check(settings=tight)
        self.assertTrue(any(b.kind == CONFIG and "total live exposure is capped" in b.detail
                            for b in r.blockers))

    def test_a_book_too_small_for_its_own_sizing_is_MONEY_and_says_how_much(self):
        """Not a misconfiguration: the strategy and the caps are fine, there is simply not enough
        capital for its positions to clear the charge floor, and the fix is a number."""
        r = _check(rupees=5_000.0, natural=325.0)
        hit = [b for b in r.blockers if "would be about" in b.detail]
        self.assertTrue(hit)
        self.assertEqual(hit[0].kind, MONEY)
        self.assertIn("Assign about", hit[0].fix)

    def test_crypto_has_no_notional_floor_so_a_small_book_is_fine(self):
        # the floor is a rupee-equity figure; CoinDCX has no DP charge and Pool G trades at Rs2,600
        r = _check(_rec("portfolio_g", "AI judgment book (crypto, Pool G)"), rupees=10_000.0,
                   cash=10_000.0, natural=2_600.0)
        self.assertTrue(r.ready, [b.detail for b in r.blockers])


class TestItChangesNothing(unittest.TestCase):
    def test_it_places_no_orders_and_changes_no_state(self):
        import deployment.live_readiness as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = " ".join(line.split("#")[0] for line in f.read().splitlines())
        for forbidden in ("place_order", "place_live_order", "place_crypto_order", "set_allocation",
                          "ensure_fresh_kite_session", "atomic_write", "json.dump", "os.replace"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_it_opens_files_only_to_read(self):
        import deployment.live_readiness as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = f.read()
        for mode in ('"w"', "'w'", '"a"', "'a'", '"w+"', '"r+"'):
            self.assertNotIn(f"open({mode}", code)
            self.assertNotIn(f", {mode})", code, mode)


class TestTheCommandLineActuallyRuns(unittest.TestCase):
    """check_live_ready.py shipped with a NameError in main() and every test still passed, because
    nothing imported it and nothing called it. A command whose entry point is never executed is a
    command that is only tested by the person running it on the server."""

    def _run(self, reports, cash=None):
        import io as _io
        from contextlib import redirect_stdout
        import check_live_ready as cli
        buf = _io.StringIO()
        with patch.object(cli, "_broker_cash", return_value=cash or {"kite": 10_500.0, "coindcx": 7_384.0}),              patch.object(cli, "check_all", return_value=reports), redirect_stdout(buf):
            code = cli.main()
        return code, buf.getvalue()

    def test_it_runs_and_says_only_money_is_left(self):
        code, out = self._run([_check(cash=500.0)])
        self.assertEqual(code, 0)
        self.assertIn("WAITING ON MONEY", out)
        self.assertIn("only thing stopping", out)

    def test_a_configuration_fault_exits_nonzero(self):
        bare = SimpleNamespace(LIVE_TRADING=False, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t")
        code, out = self._run([_check(settings=bare)])
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", out)
        self.assertIn("set up wrong", out)

    def test_nothing_promoted_is_not_an_error(self):
        code, out = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("No strategy is promoted", out)

    def test_an_unreadable_balance_prints_rather_than_crashes(self):
        code, out = self._run([_check(cash=None)], cash={"kite": None, "coindcx": 7_384.0})
        self.assertIn("not readable", out)


if __name__ == "__main__":
    unittest.main()
