"""
Fifth (PROTOTYPE, not yet registered/active) strategy: price breaks out to
a new N-day high on volume well above its recent average -- the classic
"the crowd is confirming this move" breakout filter.

Created 2026-09-05 per explicit request to evaluate this alongside the
Moving Average Pullback and RSI+MACD Confluence candidates -- "just
create and test, don't implement" (same standing instruction as
strategies/pullback_continuation.py's own 2026-07-23 precedent). Not
wired into strategies/technical_agent.py's STRATEGY_REGISTRY or
config.ACTIVE_STRATEGIES.

RELATED PRIOR RESULT, DIFFERENT MECHANISM: research_lab/'s intraday
Opening Range Breakout program already tested a volume-confirmed breakout
idea (SEED-ORB-5, "Opening Range Breakout + volume confirmation sweep")
and found volume confirmation made things WORSE, ultimately recommending
abandoning "range-breakout-of-a-computed-range" as a mechanism entirely.
That result is NOT directly transferable here: SEED-ORB-5's "range" was
the day's own OPENING RANGE (an intraday construction, re-computed fresh
every single day), tested for same-day exits. This strategy's "range" is
a rolling N-DAY price channel (a positional/swing construction, matching
this program's multi-day holding style, same N-day-high convention as
Donchian-channel breakout systems), tested with a multi-day hold via this
program's normal swing risk-manager/target machinery -- a structurally
different breakout definition and holding horizon, disclosed here so this
result is read as new evidence, not a re-run of SEED-ORB-5.

Logic:
- Breakout: today's Close exceeds the highest High of the prior
  breakout_lookback (default 20) trading days -- a new N-day high,
  excluding today itself.
- Volume confirmation: today's Volume is at least volume_multiple
  (default 1.5x) times the average Volume of the prior 20 trading days --
  the "crowd" must be visibly larger than normal on the breakout day
  itself, not just the price making a new high on ordinary turnover.
- Entry: both conditions true on the same day. Naturally close to a
  one-shot event (a new N-day high, by definition, marks a single day
  until a further new high is made), so no separate state-transition
  check is needed the way a threshold-crossing indicator needs one.
- Stop-loss: the lower of (a) the breakout level itself (the prior N-day
  high) minus a small ATR buffer -- a genuine breakout should not
  immediately fall back through the level it just broke -- or (b) an ATR-
  multiple below entry, whichever is TIGHTER (closer to entry), same
  "tighter of two candidates" convention as pullback_continuation.py's
  own stop construction.
- Target: fixed 2:1 reward:risk from entry -- same disclosed
  simplification as rsi_macd_confluence.py (no natural measured-move or
  band anchor built for this evaluation; a real measured-move target
  based on the width of the pre-breakout base would be a natural
  refinement if this candidate looks promising enough to pursue further).
- Regime-filtered (uses_regime_filter = True): a breakout is a trend-
  initiation bet, same reasoning as ma_crossover.py and
  pullback_continuation.py.
"""

from typing import Optional
import pandas as pd

from strategies.base import Strategy, Signal
from strategies.indicators import atr

BREAKOUT_LOOKBACK_DAYS = 20
VOLUME_MULTIPLE = 1.5
REWARD_RISK_MULTIPLE = 2.0


class VolumeBackedBreakoutStrategy(Strategy):
    name = "volume_backed_breakout"
    uses_regime_filter = True

    def __init__(self, breakout_lookback: int = BREAKOUT_LOOKBACK_DAYS,
                 volume_avg_lookback: int = 20, volume_multiple: float = VOLUME_MULTIPLE,
                 atr_period: int = 14, stop_atr_multiple: float = 1.5,
                 reward_risk_multiple: float = REWARD_RISK_MULTIPLE):
        self.breakout_lookback = breakout_lookback
        self.volume_avg_lookback = volume_avg_lookback
        self.volume_multiple = volume_multiple
        self.atr_period = atr_period
        self.stop_atr_multiple = stop_atr_multiple
        self.reward_risk_multiple = reward_risk_multiple

    def _compute(self, price_history: pd.DataFrame):
        min_bars = max(self.breakout_lookback, self.volume_avg_lookback, self.atr_period) + 2
        if len(price_history) < min_bars:
            return None

        df = price_history.copy()
        # shift(1) -- the prior N days, excluding today itself, so today's
        # own high/volume can never inflate its own breakout threshold.
        df["prior_high"] = df["High"].shift(1).rolling(self.breakout_lookback).max()
        df["prior_avg_volume"] = df["Volume"].shift(1).rolling(self.volume_avg_lookback).mean()
        df["atr"] = atr(df, self.atr_period)
        return df

    def _signal_gates(self, df: pd.DataFrame):
        """Returns (breakout, volume_confirmed, entry_price, stop_loss,
        target) or (bool, bool, None, None, None) -- shared by
        generate_signal() and diagnose() so the two can never drift apart."""
        today = df.iloc[-1]

        if pd.isna(today["prior_high"]) or pd.isna(today["prior_avg_volume"]) or pd.isna(today["atr"]) \
                or today["prior_avg_volume"] <= 0:
            return False, False, None, None, None

        breakout = bool(today["Close"] > today["prior_high"])
        volume_confirmed = bool(today["Volume"] >= self.volume_multiple * today["prior_avg_volume"])

        if not (breakout and volume_confirmed):
            return breakout, volume_confirmed, None, None, None

        entry_price = float(today["Close"])
        breakout_level_stop = float(today["prior_high"]) - 0.5 * float(today["atr"])
        atr_based_stop = entry_price - self.stop_atr_multiple * float(today["atr"])
        stop_loss = max(breakout_level_stop, atr_based_stop)  # the tighter (closer-to-entry) of the two

        if stop_loss >= entry_price:
            return breakout, volume_confirmed, None, None, None

        target = entry_price + self.reward_risk_multiple * (entry_price - stop_loss)
        return breakout, volume_confirmed, entry_price, stop_loss, target

    def generate_signal(self, price_history: pd.DataFrame) -> Optional[Signal]:
        df = self._compute(price_history)
        if df is None:
            return None

        breakout, volume_confirmed, entry_price, stop_loss, target = self._signal_gates(df)
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
            reason=(f"New {self.breakout_lookback}-day high ({today['prior_high']:.2f}) on "
                    f"{today['Volume'] / today['prior_avg_volume']:.1f}x average volume"),
        )

    def diagnose(self, price_history: pd.DataFrame) -> dict:
        """Mirrors generate_signal()'s gates for funnel reporting -- same
        pattern as ma_crossover.py/mean_reversion.py/pullback_continuation.py."""
        result = {
            "sufficient_history": False, "breakout": None,
            "volume_confirmed": None, "valid_stop_and_target": None, "signal": None,
        }
        df = self._compute(price_history)
        if df is None:
            return result
        result["sufficient_history"] = True

        breakout, volume_confirmed, entry_price, stop_loss, target = self._signal_gates(df)
        result["breakout"] = breakout
        result["volume_confirmed"] = volume_confirmed
        if entry_price is None:
            return result
        result["valid_stop_and_target"] = True
        result["signal"] = self.generate_signal(price_history)
        return result
