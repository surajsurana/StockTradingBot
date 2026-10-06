"""
Third (PROTOTYPE, not yet registered/active) strategy: trend-continuation
pullback to the 20-day moving average.

Complements ma_crossover, which only fires on the single day the 20-day MA
crosses above the 50-day MA -- once an uptrend is already established (which
can last weeks), ma_crossover has nothing left to say about it, even as the
stock keeps trending. Real example that motivated this: HSCL.NS on
2026-07-16, up ~9% over 10 days with the 20MA already weeks above the 50MA
-- ma_crossover correctly reported "no signal" because the crossover already
happened, long before that move.

This strategy picks up from there: instead of waiting for the cross, it
waits for price to pull back toward the already-rising 20-day average and
show a bullish reaction there -- the "buy the dip in an established uptrend"
pattern, a standard trend-continuation entry distinct from both this
system's existing strategies (ma_crossover trades the cross itself;
mean_reversion trades oversold snapbacks against the trend).

Logic:
- Confirmed uptrend: 20-day MA above 50-day MA (same test as ma_crossover,
  but does NOT require today to be the crossover day -- this is the whole
  point, it should fire on day 20 of a trend just as well as day 2)
- Price pulls back close to the 20-day MA: today's Low comes within
  pullback_band_pct of the 20-day MA (from above) without closing below it
- Bullish reaction confirms buyers stepped in at the average, not a
  breakdown in progress: today closes above both the 20-day MA and its own
  open (a green candle)
- Stop-loss: the lower of (a) today's Low minus a small ATR buffer, or (b)
  20-day MA * (1 - stop_below_ma_pct) -- meant to fail fast if the pullback
  turns into a real breakdown rather than a bounce
- Target: the highest High of the prior lookback window (the level the
  stock was already near before pulling back) -- naturally trend-aware
  rather than a fixed reward:risk multiple, BUT the trade is only taken if
  that target clears min_reward_risk (default 1.8x the stop distance).
  Without this, a pullback close to a recent high has almost no room left
  to run -- backtested at 90 Nifty 500 symbols / 3y without this gate:
  50.3% win rate but only 1.1:1 average reward:risk (Rs.433 avg win vs
  Rs.395 avg loss), a coin-flip edge with no real margin once real-world
  slippage/brokerage are accounted for (not modeled in the backtest).
- Regime-filtered (uses_regime_filter = True), same as ma_crossover: this is
  a trend-following entry, so it should only fire when the broader market
  itself is in an uptrend

STATUS: prototype only. Not in strategies/technical_agent.py's
STRATEGY_REGISTRY and not in config.ACTIVE_STRATEGIES -- must be backtested
first (see backtest_pullback_continuation.py) before it's ever wired into
the live daily scan.
"""

from typing import Optional
import pandas as pd
from strategies.base import Strategy, Signal
from strategies.ma_crossover import _atr


class PullbackContinuationStrategy(Strategy):
    name = "pullback_continuation"
    uses_regime_filter = True

    def __init__(self, fast_period: int = 20, slow_period: int = 50,
                 pullback_band_pct: float = 0.015, stop_below_ma_pct: float = 0.03,
                 target_lookback: int = 20, min_reward_risk: float = 1.8):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.pullback_band_pct = pullback_band_pct
        self.stop_below_ma_pct = stop_below_ma_pct
        self.target_lookback = target_lookback
        self.min_reward_risk = min_reward_risk

    def _compute(self, price_history: pd.DataFrame):
        min_bars = self.slow_period + 2
        if len(price_history) < min_bars:
            return None

        df = price_history.copy()
        df["fast_ma"] = df["Close"].rolling(self.fast_period).mean()
        df["slow_ma"] = df["Close"].rolling(self.slow_period).mean()
        df["atr"] = _atr(df)
        return df

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        df = self._compute(price_history)
        if df is None:
            return None

        today = df.iloc[-1]
        if pd.isna(today["fast_ma"]) or pd.isna(today["slow_ma"]) or pd.isna(today["atr"]):
            return None

        uptrend = today["fast_ma"] > today["slow_ma"]
        if not uptrend:
            return None

        pullback_touched = today["Low"] <= today["fast_ma"] * (1 + self.pullback_band_pct)
        if not pullback_touched:
            return None

        bullish_reaction = (today["Close"] > today["fast_ma"]) and (today["Close"] > today["Open"])
        if not bullish_reaction:
            return None

        entry_price = float(today["Close"])
        buffer = 0.5 * float(today["atr"])
        low_based_stop = float(today["Low"]) - buffer
        ma_based_stop = float(today["fast_ma"]) * (1 - self.stop_below_ma_pct)
        stop_loss = max(low_based_stop, ma_based_stop)  # the tighter (closer-to-entry) of the two

        # sanity bounds: never risk less than 1.5% (a hair-trigger stop would
        # oversize the position under risk-based sizing) or more than 10%
        # (caps a single trade's loss even if both candidates land far below entry)
        stop_loss = min(stop_loss, entry_price * 0.985)
        stop_loss = max(stop_loss, entry_price * 0.90)

        if stop_loss >= entry_price:
            return None

        target = float(df["High"].iloc[-self.target_lookback:].max())
        if target <= entry_price:
            return None  # already at/above the recent high -- no room left to the target

        reward_risk = (target - entry_price) / (entry_price - stop_loss)
        if reward_risk < self.min_reward_risk:
            return None  # recent high is too close to entry to be worth the risk taken

        return Signal(
            symbol="",
            direction="BUY",
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            confidence=0.60,
            strategy_name=self.name,
            reason=f"Pullback to rising 20-day MA ({today['fast_ma']:.2f}) with bullish reaction, "
                   f"uptrend confirmed (20MA > 50MA)",
        )

    def diagnose(self, price_history: pd.DataFrame) -> dict:
        """Mirrors generate_signal()'s gates for funnel reporting -- see the
        same pattern/rationale in ma_crossover.py and mean_reversion.py."""
        result = {
            "sufficient_history": False, "uptrend": None,
            "pullback_touched": None, "bullish_reaction": None,
            "valid_stop": None, "valid_target": None,
            "sufficient_reward_risk": None, "signal": None,
        }
        df = self._compute(price_history)
        if df is None:
            return result

        today = df.iloc[-1]
        if pd.isna(today["fast_ma"]) or pd.isna(today["slow_ma"]) or pd.isna(today["atr"]):
            return result
        result["sufficient_history"] = True

        uptrend = today["fast_ma"] > today["slow_ma"]
        result["uptrend"] = bool(uptrend)
        if not uptrend:
            return result

        pullback_touched = today["Low"] <= today["fast_ma"] * (1 + self.pullback_band_pct)
        result["pullback_touched"] = bool(pullback_touched)
        if not pullback_touched:
            return result

        bullish_reaction = (today["Close"] > today["fast_ma"]) and (today["Close"] > today["Open"])
        result["bullish_reaction"] = bool(bullish_reaction)
        if not bullish_reaction:
            return result

        entry_price = float(today["Close"])
        buffer = 0.5 * float(today["atr"])
        low_based_stop = float(today["Low"]) - buffer
        ma_based_stop = float(today["fast_ma"]) * (1 - self.stop_below_ma_pct)
        stop_loss = max(low_based_stop, ma_based_stop)
        stop_loss = min(stop_loss, entry_price * 0.985)
        stop_loss = max(stop_loss, entry_price * 0.90)
        valid_stop = stop_loss < entry_price
        result["valid_stop"] = valid_stop
        if not valid_stop:
            return result

        target = float(df["High"].iloc[-self.target_lookback:].max())
        valid_target = target > entry_price
        result["valid_target"] = valid_target
        if not valid_target:
            return result

        reward_risk = (target - entry_price) / (entry_price - stop_loss)
        sufficient_reward_risk = reward_risk >= self.min_reward_risk
        result["sufficient_reward_risk"] = sufficient_reward_risk
        if not sufficient_reward_risk:
            return result

        result["signal"] = self.generate_signal(price_history)
        return result
