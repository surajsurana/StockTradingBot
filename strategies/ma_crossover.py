"""
First strategy: moving-average crossover swing strategy.

Logic (deliberately simple to start — this is meant to be a working baseline
you can understand end-to-end, not a sophisticated edge):
- Fast MA (20-day) crossing above Slow MA (50-day) => BUY signal (uptrend starting)
- Volume confirmation: today's volume must be at least
  volume_confirmation_multiple x its own volume_avg_period-day average --
  a crossover on unusually thin volume is more likely noise that reverses
  than a real, sustained move with actual conviction behind it. Missing or
  insufficient volume data fails this check (no signal) rather than being
  ignored, same "don't force a trade on incomplete information" philosophy
  as the rest of this strategy.
- Stop-loss: recent swing low, or entry minus 2x ATR, whichever is tighter
- Target: entry + 2x the entry-to-stop distance (2:1 reward:risk minimum)
- Only fires on the day of the crossover, not every day the fast MA is above
  the slow MA (otherwise you'd re-signal on a position you're already in)
"""

from typing import Optional
import pandas as pd
from strategies.base import Strategy, Signal


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class MACrossoverStrategy(Strategy):
    name = "ma_crossover"

    def __init__(self, fast_period: int = 20, slow_period: int = 50,
                 require_volume_confirmation: bool = True,
                 volume_avg_period: int = 20, volume_confirmation_multiple: float = 1.5):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.require_volume_confirmation = require_volume_confirmation
        self.volume_avg_period = volume_avg_period
        self.volume_confirmation_multiple = volume_confirmation_multiple

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        if len(price_history) < self.slow_period + 2:
            return None  # not enough history yet

        df = price_history.copy()
        df["fast_ma"] = df["Close"].rolling(self.fast_period).mean()
        df["slow_ma"] = df["Close"].rolling(self.slow_period).mean()
        df["atr"] = _atr(df)
        # shift(1) so today's own volume isn't part of its own baseline --
        # otherwise a big volume day partially inflates the average it's
        # being compared against, dampening the ratio.
        df["avg_volume"] = df["Volume"].shift(1).rolling(self.volume_avg_period).mean()

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        if pd.isna(today["fast_ma"]) or pd.isna(today["slow_ma"]) or pd.isna(today["atr"]):
            return None

        crossed_up = (yesterday["fast_ma"] <= yesterday["slow_ma"]) and (today["fast_ma"] > today["slow_ma"])

        if not crossed_up:
            return None

        if self.require_volume_confirmation:
            if pd.isna(today["avg_volume"]) or today["avg_volume"] <= 0:
                return None
            if today["Volume"] < self.volume_confirmation_multiple * today["avg_volume"]:
                return None

        entry_price = float(today["Close"])
        atr_stop_distance = 2 * float(today["atr"])

        # recent swing low over the last 10 days, as an alternative (often tighter) stop
        swing_low = float(df["Low"].iloc[-10:].min())
        stop_candidate_1 = entry_price - atr_stop_distance
        stop_candidate_2 = swing_low
        stop_loss = max(stop_candidate_1, stop_candidate_2)  # tighter of the two (higher value = smaller loss)

        if stop_loss >= entry_price:
            return None  # degenerate case, skip

        risk_per_share = entry_price - stop_loss
        target = entry_price + 2 * risk_per_share  # 2:1 reward:risk

        reason = f"{self.fast_period}MA crossed above {self.slow_period}MA"
        if self.require_volume_confirmation:
            reason += f", volume {today['Volume'] / today['avg_volume']:.1f}x average"

        return Signal(
            symbol="",  # filled in by caller, which knows which symbol this history belongs to
            direction="BUY",
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            confidence=0.6,
            strategy_name=self.name,
            reason=reason,
        )
