"""
Second strategy: RSI + Bollinger Band mean-reversion.

Opposite philosophy to ma_crossover: instead of following a trend, this
looks for stocks that have moved too far, too fast, in one direction and
bets on them snapping back toward their average. This is the kind of
approach that suits range-bound / choppy stocks (like ICICI Bank looked
to be under the trend-following strategy) rather than cleanly trending ones.

Logic:
- RSI(14) drops below 30 (oversold) AND price touches/breaches the lower
  Bollinger Band (20-day, 2 std) => BUY signal
- Only fires on the day it *becomes* oversold, not every day it stays there
- Stop-loss: recent swing low (tighter than the trend strategy's stop,
  since mean-reversion trades are meant to resolve quickly)
- Target: the middle Bollinger Band (the "average" price is reverting to) —
  this is usually a smaller move than the trend strategy's target, so this
  strategy is expected to have a lower reward:risk ratio but a higher win
  rate, if it works as intended.
"""

from typing import Optional
import pandas as pd

from strategies.base import Strategy, Signal
from strategies.indicators import rsi, bollinger_bands


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    uses_regime_filter = False  # this strategy wants to buy dips even in a down/choppy market

    def __init__(self, rsi_period: int = 14, rsi_oversold: float = 30,
                 bb_period: int = 20, bb_std: float = 2.0):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.bb_period = bb_period
        self.bb_std = bb_std

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        min_bars = max(self.rsi_period, self.bb_period) + 2
        if len(price_history) < min_bars:
            return None

        df = price_history.copy()
        df["rsi"] = rsi(df["Close"], self.rsi_period)
        lower, middle, upper = bollinger_bands(df["Close"], self.bb_period, self.bb_std)
        df["bb_lower"] = lower
        df["bb_middle"] = middle

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        if pd.isna(today["rsi"]) or pd.isna(today["bb_lower"]) or pd.isna(yesterday["rsi"]):
            return None

        today_oversold = (today["rsi"] < self.rsi_oversold) and (today["Close"] <= today["bb_lower"])
        yesterday_oversold = (yesterday["rsi"] < self.rsi_oversold) and (yesterday["Close"] <= df.iloc[-2]["bb_lower"] if not pd.isna(df.iloc[-2]["bb_lower"]) else False)

        if not today_oversold or yesterday_oversold:
            return None  # only fire on the transition into oversold, not every day it stays there

        entry_price = float(today["Close"])
        swing_low = float(df["Low"].iloc[-5:].min())
        stop_loss = min(swing_low, entry_price * 0.97)  # whichever is further, capped at ~3% risk

        if stop_loss >= entry_price:
            return None

        target = float(today["bb_middle"])
        if target <= entry_price:
            return None  # degenerate case: target should be above entry for a BUY

        return Signal(
            symbol="",
            direction="BUY",
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            confidence=0.55,
            strategy_name=self.name,
            reason=f"RSI({self.rsi_period})={today['rsi']:.1f} oversold, price at lower Bollinger Band",
        )
