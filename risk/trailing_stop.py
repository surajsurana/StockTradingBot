"""
Trailing stop-loss: once a held position has moved far enough in its favor,
ratchets the stop-loss up to lock in a portion of that gain, instead of
leaving the original entry-based stop in place for the entire life of the
trade. Fixes a real, observed pattern: a position runs up 2-3%, never
reaches its full target, then drifts back down through entry into a loss --
because nothing was watching the peak, only the original fixed stop and
target set once at entry.

Pure function, no I/O -- callers (monitor_positions.py) are responsible for
computing the highest price reached since entry (from real price history)
and for actually moving the GTT order if this returns a new stop.
"""

from typing import Optional


def compute_trailing_stop_update(entry_price: float, current_stop: float,
                                  highest_high_since_entry: float,
                                  activation_pct: float, lock_in_pct: float) -> Optional[float]:
    """
    Returns a new (higher) stop-loss price if the trailing stop should
    ratchet up, or None if it isn't triggered yet or wouldn't actually raise
    the stop.

    activation_pct: how far above entry the highest price since entry must
    have reached before the trailing stop arms at all (e.g. 0.03 = 3%).
    lock_in_pct: where the new stop gets placed once armed, as a return
    above entry (e.g. 0.01 = lock in at least a 1% gain, not just
    breakeven -- a pure breakeven stop can still get tapped out at exactly
    zero P&L on the very next tick).

    Never lowers an existing stop -- only returns a value if it's a genuine
    improvement over current_stop, so calling this repeatedly as new highs
    are made is always safe (idempotent once the position has already been
    ratcheted to its current level).
    """
    if entry_price <= 0:
        return None

    favorable_move_pct = (highest_high_since_entry - entry_price) / entry_price
    if favorable_move_pct < activation_pct:
        return None

    candidate_stop = entry_price * (1 + lock_in_pct)
    if candidate_stop <= current_stop:
        return None

    return candidate_stop
