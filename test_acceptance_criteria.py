"""
Unit tests for swing_research/acceptance_criteria.py -- the strategy-aware
walk-forward windowing fix (_feasible_window_count) and the three-way
PASS/REJECT/INCONCLUSIVE verdict logic (determine_acceptance_verdict).
Run with:

    python test_acceptance_criteria.py
"""

import unittest

from swing_research.acceptance_criteria import (
    _feasible_window_count, determine_acceptance_verdict, meets_acceptance_criteria,
)


class TestFeasibleWindowCount(unittest.TestCase):
    def test_zero_lookback_returns_requested_windows_unchanged(self):
        self.assertEqual(_feasible_window_count(756, 0, 3), 3)

    def test_reduces_windows_when_lookback_is_heavy(self):
        # Minervini's real 2026-08-03 numbers: 741 trading days, 252-day
        # lookback, 60-day min tradeable -> 741 // 312 = 2, capped by requested=3.
        self.assertEqual(_feasible_window_count(741, 252, 3, min_tradeable_days_per_window=60), 2)

    def test_never_exceeds_requested_windows(self):
        # Plenty of data, but caller only asked for 1.
        self.assertEqual(_feasible_window_count(10_000, 55, 1), 1)

    def test_never_returns_fewer_than_1(self):
        self.assertEqual(_feasible_window_count(10, 252, 3, min_tradeable_days_per_window=60), 1)

    def test_turtles_lighter_lookback_keeps_the_full_requested_window_count(self):
        # Turtle's 55-day lookback over a 3-year (~756-trading-day) recent
        # period comfortably supports the requested 3 windows.
        self.assertEqual(_feasible_window_count(756, 55, 3, min_tradeable_days_per_window=60), 3)


class TestDetermineAcceptanceVerdict(unittest.TestCase):
    def test_base_reject_is_reject_regardless_of_recent_period(self):
        self.assertEqual(determine_acceptance_verdict("REJECT", "PASS"), "REJECT")
        self.assertEqual(determine_acceptance_verdict("REJECT", "REJECT"), "REJECT")

    def test_base_pass_and_recent_pass_is_pass(self):
        self.assertEqual(determine_acceptance_verdict("PASS", "PASS"), "PASS")

    def test_base_pass_recent_reject_no_conflict_is_reject(self):
        # Turtle's actual case -- the second-half robustness sub-period
        # corroborated the recent-period REJECT, so no conflict to disclose.
        self.assertEqual(determine_acceptance_verdict("PASS", "REJECT"), "REJECT")
        self.assertEqual(
            determine_acceptance_verdict("PASS", "REJECT", conflicting_robustness_evidence=False), "REJECT")

    def test_base_pass_recent_reject_with_conflict_is_inconclusive(self):
        # Minervini's actual case -- the second-half robustness sub-period
        # PASSed strongly, contradicting the thin recent-period REJECT.
        self.assertEqual(
            determine_acceptance_verdict("PASS", "REJECT", conflicting_robustness_evidence=True), "INCONCLUSIVE")

    def test_conflict_flag_is_irrelevant_when_recent_period_itself_passes(self):
        self.assertEqual(
            determine_acceptance_verdict("PASS", "PASS", conflicting_robustness_evidence=True), "PASS")


class TestMeetsAcceptanceCriteriaBackwardCompatibility(unittest.TestCase):
    def test_matches_determine_acceptance_verdict_pass_case(self):
        self.assertTrue(meets_acceptance_criteria("PASS", "PASS"))

    def test_matches_determine_acceptance_verdict_reject_cases(self):
        self.assertFalse(meets_acceptance_criteria("PASS", "REJECT"))
        self.assertFalse(meets_acceptance_criteria("REJECT", "PASS"))
        self.assertFalse(meets_acceptance_criteria("REJECT", "REJECT"))


if __name__ == "__main__":
    unittest.main()
