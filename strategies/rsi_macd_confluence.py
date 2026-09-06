"""
Fourth (PROTOTYPE, not yet registered/active) strategy: RSI recovery from
oversold, confirmed by a bullish MACD crossover on the same day --
"confluence" of two independent momentum-oscillator signals agreeing,
rather than trading either one alone.

Created 2026-09-05 per explicit request to evaluate this alongside the
Moving Average Pullback and Volume-Backed Breakout candidates -- "just
create and test, don't implement" (same standing instruction as
strategies/pullback_continuation.py's own 2026-07-23 precedent). Not
wired into strategies/technical_agent.py's STRATEGY_REGISTRY or
config.ACTIVE_STRATEGIES.

*** NOT THE SAME STRATEGY AS mean_reversion.py *** -- that existing
strategy fires on RSI-oversold + lower-Bollinger-Band touch ALONE (a pure
countertrend snapback bet, uses_regime_filter=False). This strategy
requires a SECOND, independent confirming signal (MACD line crossing
above its signal line) on the SAME day, and is treated as a
trend-confirmation entry (uses_regime_filter=True) rather than a pure
countertrend bet -- see "regime filter" note below for the reasoning.

Logic:
- RSI(14) was oversold (<30) within the last few days and has now
  recovered back above 30 -- a "coming out of oversold" transition, not
  a raw oversold reading (buying while still falling is what
  mean_reversion already tests; this strategy specifically wants
  confirmation that the bounce has already started).
- MACD(12,26,9) line crosses above its own signal line on the SAME day
  as the RSI recovery -- the "confluence": two independently-computed
  momentum readings agreeing on the same day, not just one indicator
  alone.
- Entry: both conditions true on the same day, and only on that
  transition day (not every day both remain true afterward).
- Stop-loss: recent swing low (same convention as mean_reversion.py),
  since this is still fundamentally an oversold-bounce entry even with
  the MACD confirmation.
- Target: fixed 2:1 reward:risk from entry (no natural anchor like a
  band middle or recent high exists for a pure oscillator-confluence
  signal -- disclosed as a simplification, not derived from any specific
  published methodology).
- REGIME FILTER: set to True (unlike mean_reversion's False) -- a
  judgment call, not a documented rule: requiring the broader market to
  also be in an uptrend treats this as a trend-following momentum-
  confirmation entry (buying the recovery, expecting the confluence to
  mark a real turn) rather than a countertrend bet taken regardless of
  market conditions. Estimated impact: DIRECTIONALLY UNKNOWN --
  disclosed here since it is a real design choice, not derived from
  a documented rule.
"""

from typing import Optional
import pandas as pd

from strategies.base import Strategy, Signal
from strategies.indicators import rsi, macd

REWARD_RISK_MULTIPLE = 2.0


class RsiMacdConfluenceStrategy(Strategy):
    name = "rsi_macd_confluence"
    uses_regime_filter = True   # see module docstring's "REGIME FILTER" note -- a disclosed judgment call

    def __init__(self, rsi_period: int = 14, rsi_oversold: float = 30,
                 rsi_lookback_days: int = 5,
                 macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9,
                 stop_lookback: int = 5, reward_risk_multiple: float = REWARD_RISK_MULTIPLE):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_lookback_days = rsi_lookback_days
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.stop_lookback = stop_lookback
        self.reward_risk_multiple = reward_risk_multiple

    def _compute(self, price_history: pd.DataFrame):
        min_bars = max(self.rsi_period, self.macd_slow + self.macd_signal) + self.rsi_lookback_days + 2
        if len(price_history) < min_bars:
            return None

        df = price_history.copy()
        df["rsi"] = rsi(df["Close"], self.rsi_period)
        macd_line, signal_line, histogram = macd(df["Close"], self.macd_fast, self.macd_slow, self.macd_signal)
        df["macd_line"] = macd_line
        df["macd_signal"] = signal_line
        return df

    def _signal_gates(self, df: pd.DataFrame):
        """Returns (rsi_recovery, macd_bullish_cross, entry_price, stop_loss,
        target) or (False, False, None, None, None) -- shared by
        generate_signal() and diagnose() so the two can never drift apart."""
        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        if pd.isna(today["rsi"]) or pd.isna(today["macd_line"]) or pd.isna(today["macd_signal"]) \
                or pd.isna(yesterday["macd_line"]) or pd.isna(yesterday["macd_signal"]):
            return False, False, None, None, None

        was_oversold_recently = bool(
            (df["rsi"].iloc[-(self.rsi_lookback_days + 1):-1] < self.rsi_oversold).any()
        )
        rsi_recovery = was_oversold_recently and bool(today["rsi"] >= self.rsi_oversold)

        macd_bullish_cross = bool(
            (yesterday["macd_line"] <= yesterday["macd_signal"]) and (today["macd_line"] > today["macd_signal"])
        )

        if not (rsi_recovery and macd_bullish_cross):
            return rsi_recovery, macd_bullish_cross, None, None, None

        entry_price = float(today["Close"])
        swing_low = float(df["Low"].iloc[-self.stop_lookback:].min())
        stop_loss = min(swing_low, entry_price * 0.97)  # whichever is further, capped at ~3% risk

        if stop_loss >= entry_price:
            return rsi_recovery, macd_bullish_cross, None, None, None

        target = entry_price + self.reward_risk_multiple * (entry_price - stop_loss)
        return rsi_recovery, macd_bullish_cross, entry_price, stop_loss, target

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        df = self._compute(price_history)
        if df is None:
            return None

        rsi_recovery, macd_bullish_cross, entry_price, stop_loss, target = self._signal_gates(df)
        if entry_price is None:
            return None

        today = df.iloc[-1]
        return Signal(
            symbol="",
            direction="BUY",
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            confidence=0.55,
            strategy_name=self.name,
            reason=(f"RSI({self.rsi_period}) recovered to {today['rsi']:.1f} after recent oversold, "
                    f"confirmed by MACD bullish crossover same day"),
        )

    def diagnose(self, price_history: pd.DataFrame) -> dict:
        """Mirrors generate_signal()'s gates for funnel reporting -- same
        pattern as ma_crossover.py/mean_reversion.py/pullback_continuation.py."""
        result = {
            "sufficient_history": False, "rsi_recovery": None,
            "macd_bullish_cross": None, "valid_stop_and_target": None, "signal": None,
        }
        df = self._compute(price_history)
        if df is None:
            return result
        result["sufficient_history"] = True

        rsi_recovery, macd_bullish_cross, entry_price, stop_loss, target = self._signal_gates(df)
        result["rsi_recovery"] = rsi_recovery
        result["macd_bullish_cross"] = macd_bullish_cross
        if entry_price is None:
            return result
        result["valid_stop_and_target"] = True
        result["signal"] = self.generate_signal(price_history)
        return result
