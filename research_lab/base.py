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

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        """
        price_history: DataFrame with columns Open, High, Low, Close,
        Volume. For intraday strategies this is TODAY's bars only, from
        market open up to the current bar -- not multi-day history (see
        backtesting_engineer.py, which calls this with a growing same-day
        window).

        Return a Signal if this strategy wants to open a position right
        now, otherwise return None.
        """
        raise NotImplementedError
