"""
deployment/live_allocations.py -- per-strategy live capital from the one real cash pool.

The properties under test: the pool can never be over-allocated, a rejected change leaves the stored
map untouched, and anything unreadable reads as empty rather than as a partial allocation.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from deployment.live_allocations import (ALLOCATIONS_FILENAME, allocation_for, load, set_allocation,
                                         total_allocated, unallocated)


class TestLoad(unittest.TestCase):
    def test_a_missing_file_is_empty_not_an_error(self):
        self.assertEqual(load(tempfile.mkdtemp()), {})

    def test_no_state_dir_is_empty(self):
        self.assertEqual(load(None), {})
        self.assertEqual(load(""), {})

    def test_malformed_json_reads_as_empty_rather_than_partial(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ALLOCATIONS_FILENAME), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(load(d), {})

    def test_zero_and_negative_entries_are_dropped_but_the_rest_survive(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ALLOCATIONS_FILENAME), "w", encoding="utf-8") as f:
            json.dump({"good": 5000, "zero": 0, "negative": -100}, f)
        self.assertEqual(load(d), {"good": 5000.0})

    def test_one_unreadable_entry_empties_the_whole_map_rather_than_loading_part_of_it(self):
        # fail closed: a half-understood capital map would size positions off numbers nobody chose.
        # Empty means no strategy can size anything, which is the safe reading of a corrupt file.
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ALLOCATIONS_FILENAME), "w", encoding="utf-8") as f:
            json.dump({"good": 5000, "bad": "lots"}, f)
        self.assertEqual(load(d), {})

    def test_a_json_list_reads_as_empty(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ALLOCATIONS_FILENAME), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(load(d), {})


class TestSetAllocation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_assigns_and_persists(self):
        r = set_allocation(self.d, "alpha", 50_000, available_balance=200_000)
        self.assertTrue(r.ok, r.reasons)
        self.assertEqual(r.allocations, {"alpha": 50_000.0})
        self.assertEqual(load(self.d), {"alpha": 50_000.0})     # survives a reload

    def test_the_pool_can_never_be_over_allocated(self):
        set_allocation(self.d, "alpha", 60_000, available_balance=100_000)
        r = set_allocation(self.d, "beta", 50_000, available_balance=100_000)
        self.assertFalse(r.ok)
        self.assertTrue(any("over the available balance" in x for x in r.reasons))
        self.assertEqual(load(self.d), {"alpha": 60_000.0})     # the rejected change wrote nothing

    def test_exactly_exhausting_the_balance_is_allowed(self):
        set_allocation(self.d, "alpha", 60_000, available_balance=100_000)
        self.assertTrue(set_allocation(self.d, "beta", 40_000, available_balance=100_000).ok)
        self.assertEqual(unallocated(self.d, 100_000), 0.0)

    def test_raising_a_strategys_own_allocation_does_not_count_it_twice(self):
        # the common case: alpha already has 60k of a 100k pool and is being raised to 80k. Counting
        # its existing 60k against the new 80k would wrongly refuse a change that fits.
        set_allocation(self.d, "alpha", 60_000, available_balance=100_000)
        r = set_allocation(self.d, "alpha", 80_000, available_balance=100_000)
        self.assertTrue(r.ok, r.reasons)
        self.assertEqual(load(self.d), {"alpha": 80_000.0})

    def test_zero_removes_the_allocation(self):
        set_allocation(self.d, "alpha", 50_000, available_balance=100_000)
        self.assertTrue(set_allocation(self.d, "alpha", 0, available_balance=100_000).ok)
        self.assertEqual(load(self.d), {})
        self.assertEqual(allocation_for(self.d, "alpha"), 0.0)

    def test_capital_cannot_move_while_the_strategy_holds_live_positions(self):
        # changing capital mid-position would size the remainder off different capital from the part
        # already filled, and from paper -- the comparison would measure the reallocation
        set_allocation(self.d, "alpha", 50_000, available_balance=200_000)
        r = set_allocation(self.d, "alpha", 70_000, available_balance=200_000, open_live_positions=2)
        self.assertFalse(r.ok)
        self.assertTrue(any("open live position" in x for x in r.reasons))
        self.assertEqual(load(self.d), {"alpha": 50_000.0})

    def test_negative_allocation_refused(self):
        self.assertFalse(set_allocation(self.d, "alpha", -1, available_balance=100_000).ok)
        self.assertEqual(load(self.d), {})

    def test_unparseable_inputs_refuse_rather_than_raise(self):
        for bad in ("lots", None, object()):
            self.assertFalse(set_allocation(self.d, "alpha", bad, available_balance=100_000).ok, repr(bad))
            self.assertFalse(set_allocation(self.d, "alpha", 100, available_balance=bad).ok, repr(bad))
        self.assertEqual(load(self.d), {})

    def test_a_blank_strategy_key_is_refused(self):
        for bad in ("", "   ", None):
            self.assertFalse(set_allocation(self.d, bad, 1_000, available_balance=100_000).ok, repr(bad))

    def test_a_negative_balance_is_refused(self):
        self.assertFalse(set_allocation(self.d, "alpha", 0, available_balance=-5).ok)

    def test_a_failed_write_reports_and_does_not_claim_success(self):
        with patch("deployment.live_allocations._save", side_effect=OSError("disk full")):
            r = set_allocation(self.d, "alpha", 1_000, available_balance=100_000)
        self.assertFalse(r.ok)
        self.assertTrue(any("Could not save" in x for x in r.reasons))

    def test_several_problems_are_all_reported(self):
        r = set_allocation(self.d, "", -1, available_balance=100_000, open_live_positions=3)
        self.assertGreaterEqual(len(r.reasons), 3)


class TestTotalsAndRemainder(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "alpha", 30_000, available_balance=100_000)
        set_allocation(self.d, "beta", 20_000, available_balance=100_000)

    def test_total_and_unallocated(self):
        self.assertEqual(total_allocated(self.d), 50_000.0)
        self.assertEqual(unallocated(self.d, 100_000), 50_000.0)

    def test_total_can_exclude_one_strategy(self):
        self.assertEqual(total_allocated(self.d, excluding="alpha"), 20_000.0)

    def test_an_over_allocated_pool_reports_zero_free_not_a_negative(self):
        # if the real balance drops below what is already assigned, "free" must not read as headroom
        self.assertEqual(unallocated(self.d, 10_000), 0.0)

    def test_unreadable_balance_reports_zero_free(self):
        self.assertEqual(unallocated(self.d, "lots"), 0.0)

    def test_allocation_for_an_unknown_strategy_is_zero_not_a_paper_fallback(self):
        # 0.0 means it can size no position at all, which is the correct reading of "none assigned"
        self.assertEqual(allocation_for(self.d, "never_assigned"), 0.0)


class TestTheModuleCannotTrade(unittest.TestCase):
    def test_it_reads_no_broker_and_places_no_orders(self):
        import deployment.live_allocations as la
        with open(la.__file__, encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("import requests", "kiteconnect", "place_order", "from execution"):
            self.assertNotIn(forbidden, source, forbidden)


class TestEachBrokerHoldsItsOwnMoney(unittest.TestCase):
    """Kite and CoinDCX are separate accounts. Rupees at Kite cannot buy crypto and rupees at CoinDCX
    cannot buy shares, so what is assigned at one has no bearing on what can be assigned at the
    other. Summing them together refused a Rs10,000 Kite allocation because Rs10,000 sat assigned at
    CoinDCX -- money in a different account that the Kite order could never have drawn on."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "portfolio_g", 10_000, available_balance=10_000,
                       same_venue_keys={"portfolio_g"})

    def test_an_allocation_at_one_broker_does_not_block_the_other(self):
        out = set_allocation(self.d, "overnight_return_anomaly", 10_000,
                             available_balance=10_500,              # Kite's balance
                             same_venue_keys={"overnight_return_anomaly"})
        self.assertTrue(out.ok, out.reasons)
        self.assertEqual(out.allocations["overnight_return_anomaly"], 10_000)
        self.assertEqual(out.allocations["portfolio_g"], 10_000)    # untouched

    def test_two_strategies_at_the_SAME_broker_do_compete(self):
        out = set_allocation(self.d, "alpha", 8_000, available_balance=10_000,
                             same_venue_keys={"portfolio_g", "alpha"})
        self.assertFalse(out.ok)
        self.assertIn("at this broker", " ".join(out.reasons))

    def test_the_refusal_names_the_broker_so_it_is_not_mistaken_for_the_cap(self):
        out = set_allocation(self.d, "beta", 50_000, available_balance=10_500,
                             same_venue_keys={"beta"})
        self.assertFalse(out.ok)
        self.assertIn("available balance", " ".join(out.reasons))

    def test_without_a_venue_set_every_allocation_still_competes(self):
        # the old behaviour, kept for callers that genuinely have one pool
        out = set_allocation(self.d, "alpha", 8_000, available_balance=10_000)
        self.assertFalse(out.ok)


class TestTheDeploymentCapIsItsOwnLimit(unittest.TestCase):
    """How much real money you are willing to have deployed IN TOTAL is a different fact from what
    one broker holds. Folding them into one number made a refusal by the cap look like an empty
    account, and sent you to the broker instead of to Settings."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "portfolio_g", 10_000, available_balance=10_000,
                       same_venue_keys={"portfolio_g"}, deployment_cap=10_000)

    def test_the_cap_counts_every_broker(self):
        out = set_allocation(self.d, "overnight_return_anomaly", 10_000, available_balance=10_500,
                             same_venue_keys={"overnight_return_anomaly"}, deployment_cap=10_000)
        self.assertFalse(out.ok)
        joined = " ".join(out.reasons)
        self.assertIn("deployment cap", joined)
        self.assertIn("Raise it under Settings", joined)

    def test_raising_the_cap_lets_it_through(self):
        out = set_allocation(self.d, "overnight_return_anomaly", 10_000, available_balance=10_500,
                             same_venue_keys={"overnight_return_anomaly"}, deployment_cap=25_000)
        self.assertTrue(out.ok, out.reasons)

    def test_a_cap_refusal_is_worded_differently_from_a_balance_refusal(self):
        cap = set_allocation(self.d, "x", 5_000, available_balance=10_500,
                             same_venue_keys={"x"}, deployment_cap=10_000)
        bal = set_allocation(self.d, "y", 50_000, available_balance=10_500,
                             same_venue_keys={"y"}, deployment_cap=10_000_000)
        self.assertIn("deployment cap", " ".join(cap.reasons))
        self.assertNotIn("deployment cap", " ".join(bal.reasons))

    def test_no_cap_given_means_only_the_broker_balance_governs(self):
        out = set_allocation(self.d, "overnight_return_anomaly", 10_000, available_balance=10_500,
                             same_venue_keys={"overnight_return_anomaly"})
        self.assertTrue(out.ok, out.reasons)


if __name__ == "__main__":
    unittest.main()
