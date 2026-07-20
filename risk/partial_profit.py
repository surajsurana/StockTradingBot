"""
Partial profit booking: once a held position has covered a meaningful
fraction of the distance to its original target, sells PART of the
position outright (locking in real, certain profit) and extends the
target further for the remaining "runner" shares, rather than an
all-or-nothing exit at the single original target.

Reactive design, not the two-GTTs-from-entry alternative (deliberately
chosen, with a known tradeoff): Zerodha's GTT servers watch price
continuously, but monitor_positions.py only checks a few times a trading
day, so a fast move between checks can let the ORIGINAL, full-quantity
GTT's target leg fire and exit the whole position before this logic ever
gets a chance to intervene. To give this the best practical chance of
acting first, its activation_fraction should be set meaningfully lower
than the trailing stop's (risk/trailing_stop.py) -- firing earlier means
more of the price's approach to target happens after the split, not
before it.

Pure functions, no I/O -- callers (monitor_positions.py) are responsible
for the actual GTT/order placement and for computing highest_high_since_entry
(reused from the same call already made for the trailing stop).
"""

from typing import Optional


def should_book_partial_profit(entry_price: float, target: float, highest_high_since_entry: float,
                                activation_fraction: float) -> bool:
    """
    True once price has covered activation_fraction of the distance from
    entry to the ORIGINAL target. Relative to each trade's own target, same
    reasoning as the trailing stop -- a flat percentage doesn't know how
    far a given trade's real target is.
    """
    if entry_price <= 0:
        return False
    distance_to_target = target - entry_price
    if distance_to_target <= 0:
        return False
    favorable_move = highest_high_since_entry - entry_price
    if favorable_move <= 0:
        return False
    return (favorable_move / distance_to_target) >= activation_fraction


def compute_extended_target(entry_price: float, original_target: float, extension_multiple: float) -> float:
    """
    The runner tranche's new target: extends the original entry-to-target
    distance by extension_multiple (e.g. 1.0 = double the original
    distance -- entry + 2x(original_target - entry)).
    """
    original_distance = original_target - entry_price
    return entry_price + original_distance * (1 + extension_multiple)


def compute_booking_split(total_quantity: int, booking_fraction: float) -> Optional[tuple[int, int]]:
    """
    Splits a position into (booking_qty, remaining_qty). Returns None if
    the position is too small to meaningfully split (either side would be
    zero shares) -- e.g. a 1-share position can't be partially booked, so
    the caller should just leave it alone rather than force a split.
    """
    booking_qty = round(total_quantity * booking_fraction)
    remaining_qty = total_quantity - booking_qty
    if booking_qty <= 0 or remaining_qty <= 0:
        return None
    return booking_qty, remaining_qty
