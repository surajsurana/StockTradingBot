"""
Trailing stop-loss: once a held position has covered a meaningful fraction
of the distance to its OWN target, ratchets the stop-loss up to lock in part
of that progress, instead of leaving the original entry-based stop in place
for the entire life of the trade.

v1 of this used a flat percentage (arm at +3% from entry, lock in +1%),
backtested against 91 Nifty 500 symbols / 3 months of real signals from the
two live strategies before being trusted further: it did eliminate the
"runs up then round-trips to a loss" pattern (win rate 48%->91%), but at a
real cost -- it clipped nearly every winning trade (target-hit rate
collapsed 48%->3%) well before it reached its real target, cutting total
P&L by ~24% over the same trades. A flat activation percentage doesn't know
how far a given trade's target actually is -- these strategies aim for 2:1+
reward:risk, so 3% is often a small fraction of the real move, and grabbing
a tiny 1% profit there sacrifices the big winners the whole system depends
on for its edge.

v2 (this version) is relative to each trade's own entry-to-target distance
instead: it only arms once price has covered activation_fraction of that
distance (a trade genuinely most of the way to a big winner), and then locks
in lock_in_fraction of the gain made so far -- protecting against a full
reversal on a trade that got most of the way there, while still leaving
room to reach the actual target on trades that are still developing.

Pure function, no I/O -- callers (monitor_positions.py) are responsible for
computing the highest price reached since entry (from real price history)
and for actually moving the GTT order if this returns a new stop.
"""

from typing import Optional


def compute_trailing_stop_update(entry_price: float, current_stop: float, target: float,
                                  highest_high_since_entry: float,
                                  activation_fraction: float, lock_in_fraction: float) -> Optional[float]:
    """
    Returns a new (higher) stop-loss price if the trailing stop should
    ratchet up, or None if it isn't triggered yet or wouldn't actually raise
    the stop.

    activation_fraction: how much of the entry-to-target distance the
    highest price since entry must have covered before the trailing stop
    arms at all (e.g. 0.6 = the trade must be 60% of the way to its own
    target). Relative to each trade's own target, not a flat percentage --
    a trade with a wide target has more room to run before this fires.
    lock_in_fraction: once armed, how much of the gain made so far (from
    entry to the highest price reached) gets locked in as the new stop
    (e.g. 0.5 = protect half of the move already made). Always leaves the
    new stop below the highest price reached, so the trade still has room
    left to run toward its actual target.

    Never lowers an existing stop -- only returns a value if it's a genuine
    improvement over current_stop, so calling this repeatedly as new highs
    are made is always safe (idempotent once the position has already been
    ratcheted to its current level).
    """
    if entry_price <= 0:
        return None

    distance_to_target = target - entry_price
    if distance_to_target <= 0:
        return None  # degenerate signal, shouldn't happen but guard anyway

    favorable_move = highest_high_since_entry - entry_price
    if favorable_move <= 0:
        return None

    progress_fraction = favorable_move / distance_to_target
    if progress_fraction < activation_fraction:
        return None

    candidate_stop = entry_price + lock_in_fraction * favorable_move
    if candidate_stop <= current_stop:
        return None

    return candidate_stop
