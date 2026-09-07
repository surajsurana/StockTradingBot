"""
The contract every research_lab PAIRS strategy follows -- a deliberate,
small PARALLEL to research_lab/base.py's Signal/Strategy, not an
extension of it. base.Strategy.generate_signal() is a documented
one-symbol-in, one-Signal-out contract (both existing engines call it
once per symbol per bar); a pairs trade is ONE decision that opens TWO
legs at once, sized market-neutral, and exited on the SPREAD between
them rather than on either leg's own price. Overloading Signal/Strategy
with an optional second symbol would force every existing single-symbol
strategy to carry params it never uses -- the same "duplicate a tiny,
stable interface rather than share it across a boundary" reasoning
base.py itself applies to swing_research's version.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class PairSignal:
    long_leg: str          # "a" or "b" -- a ROLE, not a symbol. The engine holds the real
                           # symbol_a/symbol_b for the pair it's calling the strategy about and
                           # resolves the role itself -- same convention as Signal(symbol="")
                           # where the caller, never the strategy, fills in the symbol.
    entry_zscore: float    # the live z-score that triggered this signal. Its SIGN matters:
                           # the engine's stop is direction-aware (pairs_simulator._check_spread_exit)
    stop_zscore: float     # magnitude, e.g. 3.5 -- exit if the spread widens FURTHER to here
    target_zscore: float   # magnitude, e.g. 0.5 -- exit once the spread has reverted to within here
    confidence: float      # 0.0-1.0
    strategy_name: str
    reason: str = ""


class PairStrategy:
    """Base class for research_lab pairs-strategy prototypes."""

    name = "pairs_base"

    def generate_pair_signal(self, bars_a_so_far: pd.DataFrame, bars_b_so_far: pd.DataFrame,
                              spread_context: Optional[dict] = None) -> Optional[PairSignal]:
        """
        bars_a_so_far / bars_b_so_far: TODAY's bars for each leg, from
        market open up to the current bar, already inner-joined on
        timestamp by pairs_simulator so both always end at the same bar.

        spread_context: computed ONCE per pair per day by
        pairs_simulator._compute_pair_day_context(), from data strictly
        before today (no lookahead): {"symbol_a", "symbol_b",
        "baseline_mean", "baseline_std" (the Close_A/Close_B ratio pooled
        over the trailing 20 trading days' bars), "correlation" (trailing
        60-day daily-close-return correlation, or None if unavailable),
        "correlation_ok" (bool -- False whenever the correlation is None,
        i.e. fails closed)}. The strategy computes the LIVE ratio and
        z-score itself from the two legs' latest Closes, the same way
        breadth_thrust_laggard_catchup.py computes its own live VWAP from
        the bars it's handed.

        Return a PairSignal to open both legs right now (the engine fills
        each leg's entry price from this bar's Close), otherwise None.
        """
        raise NotImplementedError
