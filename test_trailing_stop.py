"""
Mock-based unit tests for risk/trailing_stop.py -- the mechanism that
ratchets a position's stop-loss up once it's covered a meaningful fraction
of the distance to its own target, fixing the observed pattern of a
position running up then drifting back to a loss with the original
entry-based stop never having moved.

v2 note: this is relative to each trade's own entry-to-target distance
(not a flat percentage) -- a v1 flat-percentage version was backtested
against 91 Nifty 500 symbols / 3 months of real signals and found to clip
nearly every winning trade well before its real target (target-hit rate
48%->3%), cutting total P&L ~24% despite raising win rate 48%->91%. Run
with:

    python test_trailing_stop.py
"""

import unittest

from risk.trailing_stop import compute_trailing_stop_update, compute_trailing_stop_fallback


class TestComputeTrailingStopUpdate(unittest.TestCase):
    def test_not_armed_below_activation_threshold(self):
        # entry 100, target 120 (distance 20) -- highest high 108 is only
        # 40% of the way there, activation requires 60%
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, target=120.0, highest_high_since_entry=108.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        self.assertIsNone(result)

    def test_armed_exactly_at_activation_threshold(self):
        # 60% of the 20-point distance to target = 12 -> highest high 112
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, target=120.0, highest_high_since_entry=112.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        # favorable_move = 12, lock in 50% of that = 6 -> new stop 106
        self.assertAlmostEqual(result, 106.0)

    def test_armed_well_past_activation_threshold(self):
        # a trade most of the way to a real 2:1+ target, matching this
        # system's actual reward:risk profile
        result = compute_trailing_stop_update(
            entry_price=350.45, current_stop=339.35, target=410.70, highest_high_since_entry=400.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        favorable_move = 400.0 - 350.45
        expected = 350.45 + 0.5 * favorable_move
        self.assertAlmostEqual(result, expected)
        self.assertLess(result, 400.0)  # still leaves room below the high reached so far

    def test_never_lowers_an_existing_better_stop(self):
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=110.0, target=120.0, highest_high_since_entry=115.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        self.assertIsNone(result)

    def test_idempotent_when_called_again_at_the_same_high(self):
        first = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, target=120.0, highest_high_since_entry=115.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        second = compute_trailing_stop_update(
            entry_price=100.0, current_stop=first, target=120.0, highest_high_since_entry=115.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        self.assertIsNone(second)

    def test_invalid_entry_price_returns_none(self):
        result = compute_trailing_stop_update(
            entry_price=0.0, current_stop=97.0, target=120.0, highest_high_since_entry=115.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        self.assertIsNone(result)

    def test_degenerate_target_at_or_below_entry_returns_none(self):
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, target=100.0, highest_high_since_entry=115.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        self.assertIsNone(result)

    def test_wider_target_requires_more_favorable_move_to_arm(self):
        # same absolute move (+10 from entry), but a wider target means it's
        # a smaller FRACTION of the distance -- shouldn't arm here even
        # though it would have for test_armed_exactly_at_activation_threshold
        result = compute_trailing_stop_update(
            entry_price=100.0, current_stop=97.0, target=150.0, highest_high_since_entry=110.0,
            activation_fraction=0.6, lock_in_fraction=0.5,
        )
        self.assertIsNone(result)


class TestComputeTrailingStopFallback(unittest.TestCase):
    """
    compute_trailing_stop_fallback() -- used when the ideal (peak-based)
    stop from compute_trailing_stop_update() can't actually be placed
    because price has pulled back below it. Real incident this fixes:
    NTPC.NS reached +3.2% since entry, the ideal ratchet was rejected the
    moment it was attempted, and because price never again exceeded that
    same peak, the position sat with NO improved protection for the next
    ~3 weeks despite remaining solidly in profit the whole time.

    v3.1: uses its own fallback_activation_fraction, deliberately LOWER
    than the ideal stop's activation_fraction (current price has, by
    construction, already pulled back from the peak that cleared the
    higher bar) but NOT zero -- an earlier draft fired on any positive
    gain at all and was backtested (150 Nifty 500 symbols/3mo) to make
    total P&L WORSE than no fallback (Rs.136 -> -Rs.410), clipping trades
    on ordinary volatility. These tests pin down the corrected contract.
    """

    def test_locks_in_gain_off_current_price_not_peak(self):
        # NTPC-shaped: entry 344, target 357.75 (distance 13.75), current
        # price 351 -- (351-344)/13.75 = 51%, clears a 50% fallback bar
        # despite being nowhere near the ideal stop's own 80% bar.
        result = compute_trailing_stop_fallback(
            entry_price=344.0, current_stop=333.10, target=357.75, current_price=351.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.7,
        )
        favorable_move = 351.0 - 344.0
        expected = 344.0 + 0.7 * favorable_move
        self.assertAlmostEqual(result, expected)
        self.assertLess(result, 351.0)  # always placeable -- strictly below current price
        self.assertGreater(result, 333.10)  # genuine improvement over the original stop

    def test_not_armed_below_fallback_activation_threshold(self):
        # current price only 22% of the way to target -- a real but small
        # gain that should NOT trigger the fallback (this is exactly the
        # case an earlier, gate-less draft got wrong and was backtested
        # to hurt total P&L).
        result = compute_trailing_stop_fallback(
            entry_price=344.0, current_stop=333.10, target=357.75, current_price=347.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.7,
        )
        self.assertIsNone(result)

    def test_armed_exactly_at_fallback_activation_threshold(self):
        result = compute_trailing_stop_fallback(
            entry_price=100.0, current_stop=90.0, target=120.0, current_price=110.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.5,
        )
        self.assertAlmostEqual(result, 105.0)

    def test_never_lowers_an_existing_better_stop(self):
        result = compute_trailing_stop_fallback(
            entry_price=100.0, current_stop=115.0, target=120.0, current_price=118.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.5,
        )
        self.assertIsNone(result)

    def test_current_price_at_or_below_entry_returns_none(self):
        result = compute_trailing_stop_fallback(
            entry_price=100.0, current_stop=90.0, target=120.0, current_price=100.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.7,
        )
        self.assertIsNone(result)

    def test_invalid_entry_price_returns_none(self):
        result = compute_trailing_stop_fallback(
            entry_price=0.0, current_stop=97.0, target=120.0, current_price=115.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.7,
        )
        self.assertIsNone(result)

    def test_degenerate_target_at_or_below_entry_returns_none(self):
        result = compute_trailing_stop_fallback(
            entry_price=100.0, current_stop=97.0, target=100.0, current_price=105.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.7,
        )
        self.assertIsNone(result)

    def test_none_current_price_returns_none(self):
        result = compute_trailing_stop_fallback(
            entry_price=100.0, current_stop=97.0, target=120.0, current_price=None,
            fallback_activation_fraction=0.5, lock_in_fraction=0.7,
        )
        self.assertIsNone(result)

    def test_lock_in_fraction_near_one_still_never_reaches_current_price(self):
        # an aggressive lock-in fraction must still guarantee a strictly
        # placeable (below current price) result, never an invalid one
        result = compute_trailing_stop_fallback(
            entry_price=100.0, current_stop=90.0, target=110.0, current_price=109.0,
            fallback_activation_fraction=0.5, lock_in_fraction=0.99,
        )
        self.assertIsNotNone(result)
        self.assertLess(result, 109.0)


if __name__ == "__main__":
    unittest.main()
