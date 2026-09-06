"""
Volume-Backed Breakout -- price closes above its highest High of the
prior 20 trading days on volume at least 1.5x the prior 20-day average --
a new N-day high confirmed by visibly larger-than-normal turnover.

Ported into the Swing Research / Pool A paper-trading framework on
2026-09-06 from strategies/volume_backed_breakout.py (built in this
program's older, separate `strategies/` + `backtest/backtester.py` track
on 2026-09-05, alongside strategies/rsi_macd_confluence.py and
strategies/pullback_continuation.py, per explicit request to evaluate
three named candidates). That informal track's own multi-year backtest
(backtest_volume_backed_breakout.py, 5yr/26-symbol run) found 415 trades,
37.1% win rate, net +Rs.11,507 -- the strongest of the three candidates
tested there, but never walk-forward validated the way every Pool A
strategy otherwise is. Per explicit direction (2026-09-06), ported here
rather than re-implemented from scratch.

Named `volume_backed_breakout_pool_a.py` (not the same filename as the
original) to avoid any import collision with
strategies/volume_backed_breakout.py -- these are two independent files
in two independent, isolated tracks (this one imports only
swing_research/, the original imports only strategies/ +
backtest/backtester.py; neither imports the other).

FULLY FAITHFUL PORT -- entry, stop-loss, AND exit (fixed price target)
are all UNCHANGED from strategies/volume_backed_breakout.py. An earlier
version of this file substituted a time-stop for the original's fixed
target, because deployment/paper_trading_engine.py had no mechanism to
express one -- per explicit direction ("we must not play with strategy
rules"), that engine gap was fixed instead (Signal.target_price, added
2026-09-06 to swing_research/base.py, checked mechanically by
paper_trading_engine.py's run_daily() at the same priority tier as the
existing stop-loss check) rather than the strategy's own rules being
adapted to the engine's prior limitation. See ma_pullback.py's own
module docstring and Signal.target_price's own docstring for the full
engine-side explanation (this strategy was ported and fixed at the same
time, for the same reason).

Entry logic:
- Breakout: today's Close exceeds the highest High of the prior 20
  trading days (excluding today itself).
- Volume confirmation: today's Volume >= 1.5x the average Volume of the
  prior 20 trading days (excluding today itself).
- Entry fires the day both conditions are true (naturally close to a
  one-shot event -- a new N-day high, by definition, marks a single day
  until a further new high is made).

Stop-loss: the tighter (closer-to-entry) of (a) the prior 20-day high
itself minus a small ATR buffer, or (b) 1.5x ATR below entry.

Target: fixed 2:1 reward:risk from entry.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

BREAKOUT_LOOKBACK_DAYS = 20
VOLUME_AVG_LOOKBACK_DAYS = 20
VOLUME_MULTIPLE = 1.5
ATR_PERIOD = 14
STOP_ATR_MULTIPLE = 1.5
REWARD_RISK_MULTIPLE = 2.0


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class VolumeBackedBreakoutPoolAStrategy(Strategy):
    name = "volume_backed_breakout"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = max(BREAKOUT_LOOKBACK_DAYS, VOLUME_AVG_LOOKBACK_DAYS, ATR_PERIOD) + 2

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        # shift(1) -- the prior N days, excluding today itself, so today's
        # own high/volume can never inflate its own breakout threshold.
        df["prior_high"] = df["High"].shift(1).rolling(BREAKOUT_LOOKBACK_DAYS).max()
        df["prior_avg_volume"] = df["Volume"].shift(1).rolling(VOLUME_AVG_LOOKBACK_DAYS).mean()
        df["atr"] = _atr(df)

        breakout = df["Close"] > df["prior_high"]
        volume_confirmed = df["Volume"] >= VOLUME_MULTIPLE * df["prior_avg_volume"]
        df["qualifies"] = breakout & volume_confirmed & (df["prior_avg_volume"] > 0)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or not bool(row.qualifies):
            return None
        if pd.isna(row.atr) or pd.isna(row.prior_high):
            return None

        entry_price = float(row.Close)
        breakout_level_stop = float(row.prior_high) - 0.5 * float(row.atr)
        atr_based_stop = entry_price - STOP_ATR_MULTIPLE * float(row.atr)
        stop_loss = max(breakout_level_stop, atr_based_stop)   # the tighter of the two

        if stop_loss >= entry_price:
            return None

        target_price = entry_price + REWARD_RISK_MULTIPLE * (entry_price - stop_loss)

        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            target_price=target_price,
            # confidence left at its default -- no natural cross-sectional ranking measure exists
            # for this per-symbol pattern signal, same convention as Turtle System 2 and
            # Turn-of-the-Month (see swing_research/candidate_ranking.py's date-seeded tie-break).
            strategy_name=self.name,
            reason=(f"New {BREAKOUT_LOOKBACK_DAYS}-day high ({row.prior_high:.2f}) on "
                    f"{row.Volume / row.prior_avg_volume:.1f}x average volume"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        # No signal-based exit -- entirely engine-mechanical (stop-loss or
        # target_price, both checked by deployment/paper_trading_engine.py
        # before this is ever called), matching the original's own design
        # exactly (fixed 2:1 target OR stop-loss, whichever comes first).
        return None
