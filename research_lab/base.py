"""
The contract every research_lab strategy follows. A deliberate, small
DUPLICATE of strategies/base.py's Signal/Strategy shape -- not an import
from it. The interface itself is ~15 lines and completely stable; keeping
it duplicated means research_lab has zero import dependency on the swing
package, which is worth far more than avoiding this little repetition
given how strongly isolation matters here (see PROJECT_CONTEXT.md).
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class Signal:
    symbol: str
    direction: str        # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    target: float
    confidence: float     # 0.0-1.0
    strategy_name: str
    reason: str = ""


class Strategy:
    """Base class for research_lab strategy prototypes."""

    name = "base"

    def generate_signal(self, price_history: pd.DataFrame, context: Optional[dict] = None) -> Optional[Signal]:
        """
        price_history: DataFrame with columns Open, High, Low, Close,
        Volume. TODAY's bars only, from market open up to the current bar
        -- not multi-day history (see backtesting_engineer.py, which calls
        this with a growing same-day window).

        context: pre-computed multi-day facts backtesting_engineer.py
        derives ONCE per day from the full multi-symbol history before the
        day starts, since a strategy checking a genuinely intraday-only
        signal has no other way to know things like "yesterday's close" or
        "this stock's typical volume at this time of day" -- it only ever
        sees today's bars in price_history. Currently provided keys:
        "prior_close" (float or None if unavailable) and
        "avg_first_15min_volume_20d" (float or None). None if a strategy
        doesn't need any multi-day context (e.g. a pure intraday
        opening-range strategy).

        Return a Signal if this strategy wants to open a position right
        now, otherwise return None.
        """
        raise NotImplementedError
