"""
Crypto Daily Trend Timing -- the fifth Pool E candidate (2026-09-17),
completing the cadence set requested alongside SW-020 (monthly) and
SW-028 (weekly): the SAME Faber 10-month-SMA trend rule, checked and
rebalanced EVERY day instead of every week or month.

This is the fastest cadence this program will test for a trend-HOLD
rule (exit only when the trend flips, not on a fixed schedule). Genuine
intraday trading -- many decisions and fills WITHIN a day -- remains
untested and un-built, per the standing rule from EXP-082/EXP-087: it
does not survive India's 31.2% per-trade tax. This strategy still makes
at most one decision per coin per day, at the daily close.

Expectation, stated before running the experiment: SW-028 (weekly)
already showed that faster checking of this SAME signal catches trend
changes sooner (helping) but reacts to more noise (hurting) -- more
trades, similar-or-better raw/pre-tax return, materially worse
drawdown. Daily checking is expected to push further in that direction;
it may or may not still clear the tax bar. That is the question this
experiment answers, not assumed.

=========================== DOCUMENTED RULES ===========================

Faber (2007)'s rule (see crypto_trend_timing.py), with "month-end"
replaced by "every day" throughout: buy when today's close is above the
10-month SMA, sell when it is below.

===================== IMPLEMENTATION ASSUMPTIONS =====================

Identical to crypto_trend_timing.py (coins, sleeves, 20% stop, ~300-day
SMA, costs, tax) with one change:
1. DECISION DAY = every day instead of month-end or week-end. Estimated
   impact: the experiment's own question (see above).
2. WARM-UP: SMA computed on full history, supplied to each walk-forward
   window, as in the monthly and weekly variants.

Rebalance frequency: daily.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

SMA_LOOKBACK_DAYS = 300   # same ~10-month window as the monthly/weekly variants
STOP_LOSS_PCT = 0.20


def compute_daily_sma(price_history: pd.DataFrame, lookback_days: int = SMA_LOOKBACK_DAYS) -> pd.DataFrame:
    """Adds sma_daily (a lookback_days rolling SMA of the close, read
    every day). Keeps a supplied sma_daily column (warm-up)."""
    df = price_history.sort_index().copy()
    if "sma_daily" not in df.columns:
        df["sma_daily"] = df["Close"].rolling(lookback_days).mean()
    return df


class CryptoDailyTrendTimingStrategy(Strategy):
    name = "crypto_trend_timing_daily"
    max_units = 1
    risk_pct_per_unit = 0.04
    fractional_quantities = True
    min_lookback_days = SMA_LOOKBACK_DAYS + 5

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = compute_daily_sma(price_history)
        df["above_sma"] = df["sma_daily"].notna() & (df["Close"] > df["sma_daily"])
        df["below_sma"] = df["sma_daily"].notna() & (df["Close"] < df["sma_daily"])
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.above_sma):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(entry_price / float(row.sma_daily) - 1) if row.sma_daily else 1.0,
            strategy_name=self.name,
            reason=f"close {entry_price:.2f} above its 300-day SMA {float(row.sma_daily):.2f}",
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if bool(row.below_sma):
            return float(row.Close)
        return None
