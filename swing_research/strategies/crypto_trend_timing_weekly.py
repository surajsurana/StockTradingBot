"""
Crypto Weekly Trend Timing -- the fourth Pool E candidate (2026-09-17),
built after the direction "I want to do some paper trading in crypto
that runs all day ... we have not had a single crypto trade": the two
live Pool E books (SW-020 Faber, SW-021 MOP) both decide only once a
MONTH, so the account can go weeks with nothing visible happening. This
is the SAME 10-month-SMA trend rule, but checked and rebalanced on
EVERY week's last UTC day instead of the last day of the month --
crypto trades every day, so a weekly cadence is a natural rebalance
frequency, and a trend rule (hold through the trend, exit only when it
flips) keeps realised trades far fewer than "52 decisions/year" would
suggest.

Deliberately NOT an intraday strategy: it still makes at most one
decision per coin per week, at a daily close. Genuine intraday crypto
trading was tested (EXP-082, weekly rebalance of a CROSS-SECTIONAL rank;
EXP-087, monthly re-sizing) and REJECTED both times after tax -- see
research_lab/research_conclusions.jsonl's standing rule: realising a
gain more than a handful of times a year loses to India's 31.2%
per-trade tax with no loss offset. This candidate exists to test where
that line actually sits, with weekly checks but a trend-holding rule,
not to trade more for its own sake.

=========================== DOCUMENTED RULES ===========================

Faber (2007)'s rule (see crypto_trend_timing.py's own citation), with
ONE change: "month-end" is replaced by "the last UTC calendar day of
each ISO week (Sunday)" throughout -- buy when the week-end close is
above the 10-month SMA, sell when it is below.

===================== IMPLEMENTATION ASSUMPTIONS =====================

Identical to crypto_trend_timing.py (same coins, same 20% sleeves, same
20% stop, same 10-month/300-day SMA, same costs and tax) with ONE
change:
1. DECISION DAY = the last day of each ISO week (Sunday, UTC) instead
   of the last day of each month -- the SMA itself is unchanged (still
   a ~300-calendar-day average), only how often it is READ changes.
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- more frequent
   checks catch a trend change faster (helps) but also react to more
   noise (whipsaws, hurts); the point of running this experiment is to
   see which dominates after costs and tax.
2. WARM-UP: the SMA is computed on full history and supplied to each
   walk-forward window, exactly as crypto_trend_timing.py does.

Rebalance frequency: weekly (every Sunday, UTC).
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

SMA_MONTHS_EQUIVALENT_DAYS = 300   # same ~10-month window as crypto_trend_timing.py, in calendar days
STOP_LOSS_PCT = 0.20


def compute_week_end_sma(price_history: pd.DataFrame, lookback_days: int = SMA_MONTHS_EQUIVALENT_DAYS) -> pd.DataFrame:
    """Adds is_week_end (the bar dated the last day, Sunday, of an ISO
    week that is fully present in the data) and sma_week_end (a
    lookback_days-calendar-day rolling SMA of the close, read only on
    week-end rows). Keeps a supplied sma_week_end column (warm-up)."""
    df = price_history.sort_index().copy()
    df["is_week_end"] = df.index.weekday == 6   # Sunday
    if "sma_week_end" not in df.columns:
        df["sma_week_end"] = df["Close"].rolling(lookback_days).mean()
    return df


class CryptoWeeklyTrendTimingStrategy(Strategy):
    name = "crypto_trend_timing_weekly"
    max_units = 1
    risk_pct_per_unit = 0.04
    fractional_quantities = True
    min_lookback_days = SMA_MONTHS_EQUIVALENT_DAYS + 5

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = compute_week_end_sma(price_history)
        df["above_sma"] = df["is_week_end"] & (df["Close"] > df["sma_week_end"])
        df["below_sma"] = df["is_week_end"] & df["sma_week_end"].notna() & (df["Close"] < df["sma_week_end"])
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.above_sma):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(entry_price / float(row.sma_week_end) - 1) if row.sma_week_end else 1.0,
            strategy_name=self.name,
            reason=f"week-end close {entry_price:.2f} above its 300-day SMA {float(row.sma_week_end):.2f}",
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if bool(row.below_sma):
            return float(row.Close)
        return None
