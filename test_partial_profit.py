"""
Mock-based unit tests for risk/partial_profit.py -- the logic that decides
when to book partial profit on a held position (sell part of it outright,
extend the target for the rest) rather than an all-or-nothing exit at the
single original target. Run with:

    python test_partial_profit.py
"""

import unittest

from risk.partial_profit import should_book_partial_profit, compute_extended_target, compute_booking_split


class TestShouldBookPartialProfit(unittest.TestCase):
    def test_not_triggered_below_activation_threshold(self):
        # entry 100, target 120 (distance 20) -- highest high 108 is only
        # 40% of the way there, activation requires 50%
        result = should_book_partial_profit(
            entry_price=100.0, target=120.0, highest_high_since_entry=108.0, activation_fraction=0.5,
        )
        self.assertFalse(result)

    def test_triggered_exactly_at_activation_threshold(self):
        result = should_book_partial_profit(
            entry_price=100.0, target=120.0, highest_high_since_entry=110.0, activation_fraction=0.5,
        )
        self.assertTrue(result)

    def test_triggered_past_activation_threshold(self):
        result = should_book_partial_profit(
            entry_price=350.45, target=410.70, highest_high_since_entry=405.0, activation_fraction=0.5,
        )
        self.assertTrue(result)

    def test_no_favorable_move_not_triggered(self):
        result = should_book_partial_profit(
            entry_price=100.0, target=120.0, highest_high_since_entry=95.0, activation_fraction=0.5,
        )
        self.assertFalse(result)

    def test_invalid_entry_price_returns_false(self):
        result = should_book_partial_profit(
            entry_price=0.0, target=120.0, highest_high_since_entry=110.0, activation_fraction=0.5,
        )
        self.assertFalse(result)

    def test_degenerate_target_returns_false(self):
        result = should_book_partial_profit(
            entry_price=100.0, target=100.0, highest_high_since_entry=110.0, activation_fraction=0.5,
        )
        self.assertFalse(result)


class TestComputeExtendedTarget(unittest.TestCase):
    def test_doubles_the_distance_by_default_multiple(self):
        # entry 100, original target 120 (distance 20), extension 1.0 ->
        # extended target = 100 + 20*2 = 140
        result = compute_extended_target(entry_price=100.0, original_target=120.0, extension_multiple=1.0)
        self.assertAlmostEqual(result, 140.0)

    def test_smaller_extension_multiple(self):
        result = compute_extended_target(entry_price=100.0, original_target=120.0, extension_multiple=0.5)
        self.assertAlmostEqual(result, 130.0)

    def test_real_values(self):
        result = compute_extended_target(entry_price=350.45, original_target=410.70, extension_multiple=1.0)
        self.assertAlmostEqual(result, 350.45 + 2 * (410.70 - 350.45))


class TestComputeBookingSplit(unittest.TestCase):
    def test_even_split(self):
        self.assertEqual(compute_booking_split(10, 0.5), (5, 5))

    def test_odd_quantity_rounds(self):
        # 7 * 0.5 = 3.5 -> rounds to 4 (Python banker's rounding: round(3.5) == 4)
        booking_qty, remaining_qty = compute_booking_split(7, 0.5)
        self.assertEqual(booking_qty + remaining_qty, 7)
        self.assertGreater(booking_qty, 0)
        self.assertGreater(remaining_qty, 0)

    def test_too_small_to_split_returns_none(self):
        # 1 share * 0.5 rounds to 0 -- can't book a 0-share tranche
        self.assertIsNone(compute_booking_split(1, 0.5))

    def test_two_shares_splits_evenly(self):
        self.assertEqual(compute_booking_split(2, 0.5), (1, 1))

    def test_extreme_fraction_leaving_nothing_for_remainder_returns_none(self):
        self.assertIsNone(compute_booking_split(4, 1.0))


if __name__ == "__main__":
    unittest.main()
