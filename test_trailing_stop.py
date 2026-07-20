"""
Mock-based unit tests for risk/trailing_stop.py -- the mechanism that
ratchets a position's stop-loss up once it's moved far enough in its favor,
fixing the observed pattern of a position running up 2-3% then drifting
back to a loss with the original entry-based stop never having moved.
Run with:

    python test_trailing_stop.py
"""

import unittest

from risk.trailing_stop import compute_trailing_stop_update


class TestComputeTrailingStopUpdate(unittest.TestCase):
    def test_not_armed_below_activation_threshold(self):
        # up only 1.5%, activation is 3% -- not armed yet
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, highest_high_since_entry=101.5,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        self.assertIsNone(result)

    def test_armed_exactly_at_activation_threshold(self):
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, highest_high_since_entry=103.0,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        self.assertAlmostEqual(result, 101.0)

    def test_armed_well_above_activation_threshold(self):
        result = compute_trailing_stop_update(
            entry_price=350.45, current_stop=339.35, highest_high_since_entry=370.0,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        self.assertAlmostEqual(result, 350.45 * 1.01)

    def test_never_lowers_an_existing_better_stop(self):
        # current_stop is already above what the trailing logic would set --
        # e.g. called again after a prior ratchet already raised it further.
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=102.0, highest_high_since_entry=104.0,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        self.assertIsNone(result)

    def test_idempotent_when_called_again_at_the_same_high(self):
        first = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, highest_high_since_entry=105.0,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        second = compute_trailing_stop_update(
            entry_price=100.0, current_stop=first, highest_high_since_entry=105.0,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        self.assertIsNone(second)

    def test_invalid_entry_price_returns_none(self):
        result = compute_trailing_stop_update(
            entry_price=0.0, current_stop=97.0, highest_high_since_entry=105.0,
            activation_pct=0.03, lock_in_pct=0.01,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
