"""
The contract every Swing Research Program strategy follows. A deliberate,
small duplicate of research_lab/base.py's Signal shape (direction-agnostic,
same field names) -- not an import from it, for the same isolation reason
research_lab itself doesn't import strategies/base.py.

PRECOMPUTE-BASED, not slice-based: unlike research_lab's Strategy (called
fresh on a growing window each bar) or the production strategies/base.py
Strategy (same pattern, called on a growing daily-bar window each day),
this interface asks a strategy to compute every indicator it needs ONCE,
vectorized, for a symbol's FULL multi-year history up front (precompute()),
then answers entry/pyramid/exit questions via O(1) lookups into that
precomputed frame inside the day-by-day simulation loop.

Why the difference: this program's universe is the full frozen Nifty 500
(~457 symbols, see swing_research/universe.py) over a multi-year window --
calling generate_signal() on a growing slice each day (the pattern
strategies/ma_crossover.py, strategies/mean_reversion.py, and
backtest/backtester.py already use) means recomputing the same rolling
statistics from scratch on an ever-longer slice every single day, which is
roughly O(n^2) per symbol. Fine for backtest/backtester.py's existing
single-symbol-at-a-time use, but not for hundreds of symbols over years.
Precomputing once per symbol is O(n), vectorized, and orders of magnitude
faster. The two production strategies still get benchmarked in their OWN
native calling convention (see swing_research/backtesting_engine.py's
simulate_symbol_single_unit(), used specifically for them) -- they are
read-only, never adapted to this interface.

Three action hooks (rather than research_lab's single generate_signal)
because a pyramiding, multi-unit system like Turtle Trading has three
genuinely distinct decisions at different points in a trade's life:
  1. Should I open a brand-new position? (entry_signal_at)
  2. Given a position I already hold, should I add another unit?
     (pyramid_signal_at)
  3. Given a position I already hold, does MY OWN exit rule (not the
     mechanical stop-loss check, which the engine handles directly from
     the position's stop_loss) say to close today? (exit_signal_at)
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class Signal:
    symbol: str
    direction: str        # "BUY" (long) or "SELL" (short) -- see research_lab/base.py's
                           # Signal for the same invariant (stop must sit on the correct
                           # side of entry for the given direction). This program's first
                           # strategy (Turtle) is long-only by explicit, approved scope
                           # reduction -- see swing_research/strategies/turtle_system2.py --
                           # but the field stays direction-agnostic for a future strategy.
    entry_price: float
    stop_loss: float       # for a pyramid-add signal, this is the REVISED stop for the
                            # WHOLE position (all units), not just the new unit.
    confidence: float = 1.0
    strategy_name: str = ""
    reason: str = ""


@dataclass
class PositionUnit:
    """One pyramided unit within an open position -- Turtle can hold up to
    4 of these per symbol simultaneously, each entered at a different price
    on a different date."""
    entry_price: float
    entry_date: object     # datetime.date
    quantity: int


@dataclass
class OpenPosition:
    """What a strategy's pyramid_signal_at/exit_signal_at hooks are given
    about a position the engine already holds -- read-only from the
    strategy's point of view, the engine owns the authoritative state."""
    symbol: str
    direction: str
    units: list = field(default_factory=list)   # list[PositionUnit], oldest first
    stop_loss: float = 0.0                        # current whole-position stop

    @property
    def total_quantity(self) -> int:
        return sum(u.quantity for u in self.units)

    @property
    def last_unit_entry_price(self) -> float:
        return self.units[-1].entry_price

    @property
    def average_entry_price(self) -> float:
        total_qty = self.total_quantity
        if total_qty == 0:
            return 0.0
        return sum(u.entry_price * u.quantity for u in self.units) / total_qty


class Strategy:
    """Base class for Swing Research Program strategy implementations
    (precompute-based -- see module docstring)."""

    name = "base"
    max_units = 1              # strategies that don't pyramid leave this at 1
    risk_pct_per_unit = 0.01   # fraction of CURRENT (realized) equity risked -- entry-to-stop
                                # distance -- per unit. See backtesting_engine.py's sizing
                                # formula docstring for why this is mathematically equivalent
                                # to Turtle's own "1% of equity per N, stop at 2N" convention
                                # when a strategy sets risk_pct_per_unit=0.02 and
                                # stop_loss=entry-2N (Turtle does exactly this).
    min_lookback_days = 0      # minimum bars of PRIOR history this strategy's precompute()
                                # needs before entry_signal_at() can possibly return a signal
                                # (e.g. Minervini's 252-day 52-week window). Override per
                                # strategy. Used generically by
                                # swing_research/acceptance_criteria.py to size walk-forward
                                # windows for the recent-period check so a short recent-period
                                # slice isn't split into windows too thin to ever trade --
                                # added 2026-08-03 after this exact starvation produced a
                                # false REJECT for Minervini's recent-period check.

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        """
        price_history: full multi-year daily OHLCV history for ONE symbol
        (columns Open, High, Low, Close, Volume), oldest first.

        Returns an augmented DataFrame, SAME INDEX as price_history, with
        every indicator column this strategy's entry/pyramid/exit hooks
        need -- computed once, vectorized (rolling/ewm), never recomputed
        inside the day-by-day simulation loop.

        No-lookahead is this method's responsibility: any column meant to
        represent "known as of the start of today" (e.g. a breakout level)
        must be built with .shift(1) before .rolling()/.ewm(), so row i's
        value reflects only bars strictly before day i.
        """
        raise NotImplementedError

    def entry_signal_at(self, row) -> Optional[Signal]:
        """row: one row (a pandas Series or namedtuple) from this symbol's
        precomputed frame, for the day being evaluated. Returns a Signal to
        open a brand-new position (the first unit), or None. Only ever
        called when the engine currently holds NO position in this symbol."""
        raise NotImplementedError

    def pyramid_signal_at(self, row, open_position: OpenPosition) -> Optional[Signal]:
        """Only called when the engine already holds an open position in
        this symbol AND len(open_position.units) < self.max_units. Default:
        None -- non-pyramiding strategies don't need to override this."""
        return None

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        """Only called when the engine already holds an open position in
        this symbol. Returns an exit price if the strategy's OWN exit rule
        (not the mechanical stop-loss check, which the engine handles
        directly) triggers today, else None. Default: None -- fixed-target
        strategies (no signal-based exit) don't need to override this."""
        return None
