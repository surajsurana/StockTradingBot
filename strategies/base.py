"""
The contract every strategy must follow. main.py and the backtester only ever
talk to strategies through this interface -- they never know how a strategy
actually decides. This is what makes strategies swappable/addable without
touching the rest of the system.
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
    confidence: float     # 0.0-1.0, lets risk manager weight position size
    strategy_name: str
    reason: str = ""       # human-readable explanation, useful for reports/debugging


class Strategy:
    """Base class. Every strategy subclasses this and implements generate_signal."""

    name = "base"

    # Whether this strategy should be gated by the Nifty market-regime filter
    # (only trade BUY signals when the broader market is itself in an
    # uptrend). Makes sense for trend-following strategies; usually wrong for
    # mean-reversion strategies, which often specifically want to buy dips
    # during choppy or declining conditions. Override per strategy.
    uses_regime_filter = True

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        """
        price_history: DataFrame with columns Open, High, Low, Close, Volume,
        indexed by date, most recent row last.

        Return a Signal if this strategy wants to open a position today,
        otherwise return None.
        """
        raise NotImplementedError
