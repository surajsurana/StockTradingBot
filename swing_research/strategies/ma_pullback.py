"""
Moving Average Pullback -- trend-continuation entry: buy a pullback to a
rising 20-day moving average within a confirmed uptrend (20-day MA above
50-day MA), on a bullish (green, above-MA) reaction candle.

Ported into the Swing Research / Pool A paper-trading framework on
2026-09-06 from strategies/pullback_continuation.py (a PROTOTYPE in this
program's older, separate `strategies/` + `backtest/backtester.py` track,
built July 2026, never promoted to that track's own live path). That
informal track's own multi-year backtest (backtest_pullback_continuation.py,
5yr/26-symbol run) found 242 trades, 37.6% win rate, net +Rs.5,830 --
positive, but never walk-forward validated the way every Pool A strategy
otherwise is. Per explicit direction (2026-09-06), ported here rather than
re-implemented from scratch.

FULLY FAITHFUL PORT -- entry, stop-loss, AND exit (fixed price target)
are all UNCHANGED from strategies/pullback_continuation.py. An earlier
version of this file substituted a time-stop for the original's fixed
target, because deployment/paper_trading_engine.py had no mechanism to
express one -- per explicit direction ("we must not play with strategy
rules"), that engine gap was fixed instead (Signal.target_price, added
2026-09-06 to swing_research/base.py, checked mechanically by
paper_trading_engine.py's run_daily() at the same priority tier as the
existing stop-loss check) rather than the strategy's own rules being
adapted to the engine's prior limitation. See Signal.target_price's own
docstring for the engine side of this.

Entry logic:
- Confirmed uptrend: 20-day MA above 50-day MA.
- Price pulls back close to the 20-day MA: today's Low comes within
  pullback_band_pct (1.5%) of the 20-day MA from above, without closing
  below it.
- Bullish reaction: today closes above both the 20-day MA and its own
  Open (a green candle).
- Only fires on the transition day (uptrend + pullback + bullish
  reaction all true today), not every day the conditions happen to hold.

Stop-loss: the tighter (closer-to-entry) of (a) today's Low minus a
small ATR buffer, or (b) 20-day MA * (1 - stop_below_ma_pct), bounded to
between 1.5% and 10% risk.

Target: the highest High of the trailing target_lookback (20) trading
days INCLUDING today (matches the original's own
`df["High"].iloc[-target_lookback:].max()`, which does NOT shift/exclude
the signal day itself), gated by a minimum 1.8x reward:risk -- a signal
whose recent high is too close to entry to clear that ratio is not taken
at all (same as the original: "already at/above the recent high -- no
room left to the target").
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

FAST_MA_PERIOD = 20
SLOW_MA_PERIOD = 50
PULLBACK_BAND_PCT = 0.015
STOP_BELOW_MA_PCT = 0.03
ATR_PERIOD = 14
TARGET_LOOKBACK_DAYS = 20
MIN_REWARD_RISK = 1.8


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class MaPullbackStrategy(Strategy):
    name = "ma_pullback"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = SLOW_MA_PERIOD + 2

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        df["fast_ma"] = df["Close"].rolling(FAST_MA_PERIOD).mean()
        df["slow_ma"] = df["Close"].rolling(SLOW_MA_PERIOD).mean()
        df["atr"] = _atr(df)
        # Matches the original's df["High"].iloc[-target_lookback:].max()
        # exactly -- a trailing window that INCLUDES today, not shifted.
        df["target_high"] = df["High"].rolling(TARGET_LOOKBACK_DAYS).max()

        uptrend = df["fast_ma"] > df["slow_ma"]
        pullback_touched = df["Low"] <= df["fast_ma"] * (1 + PULLBACK_BAND_PCT)
        bullish_reaction = (df["Close"] > df["fast_ma"]) & (df["Close"] > df["Open"])

        df["qualifies"] = uptrend & pullback_touched & bullish_reaction
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev):
            return None
        if not bool(row.qualifies):
            return None
        if pd.isna(row.atr) or pd.isna(row.fast_ma) or pd.isna(row.target_high):
            return None

        entry_price = float(row.Close)
        buffer = 0.5 * float(row.atr)
        low_based_stop = float(row.Low) - buffer
        ma_based_stop = float(row.fast_ma) * (1 - STOP_BELOW_MA_PCT)
        stop_loss = max(low_based_stop, ma_based_stop)   # the tighter of the two
        stop_loss = min(stop_loss, entry_price * 0.985)  # never less than 1.5% risk
        stop_loss = max(stop_loss, entry_price * 0.90)   # never more than 10% risk

        if stop_loss >= entry_price:
            return None

        target_price = float(row.target_high)
        if target_price <= entry_price:
            return None  # already at/above the recent high -- no room left to the target

        reward_risk = (target_price - entry_price) / (entry_price - stop_loss)
        if reward_risk < MIN_REWARD_RISK:
            return None  # recent high is too close to entry to be worth the risk taken

        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            target_price=target_price,
            # confidence left at its default -- no natural cross-sectional ranking measure exists
            # for this per-symbol pattern signal, same convention as Turtle System 2 and
            # Turn-of-the-Month (see swing_research/candidate_ranking.py's date-seeded tie-break).
            strategy_name=self.name,
            reason=(f"Pullback to rising 20-day MA ({row.fast_ma:.2f}) with bullish reaction, "
                    f"uptrend confirmed (20MA > 50MA)"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        # No signal-based exit -- entirely engine-mechanical (stop-loss or
        # target_price, both checked by deployment/paper_trading_engine.py
        # before this is ever called), matching the original's own design
        # exactly (fixed target OR stop-loss, whichever comes first, no
        # third exit path).
        return None
