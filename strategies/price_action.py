"""
Objective price-action facts -- recent move magnitude, position relative to
moving averages, and volume behavior -- fed into the Research Analyst's
synthesis prompt so it actually sees what the chart has been doing, not just
whether a strategy's entry trigger fired today.

Motivated by a real gap found live: PATANJALI.NS slid double-digit % off its
highs (well below its own 20/50/200-day averages) while every fundamentals
and news input stayed positive, and the Research Analyst never saw the
price move itself -- it only ever saw "no technical signal today," which is
what both strategies report whether a stock is calm or actively crashing,
since neither one generates a SELL signal.

Read-only description, same as the rest of this module's neighbors
(fundamentals/, news/) -- never places or sizes a trade itself.
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class PriceAction:
    price: float
    pct_off_high: float             # e.g. -19.2 = 19.2% below the lookback-window high
    high_lookback_days: int
    above_20ma: Optional[bool]
    above_50ma: Optional[bool]
    above_200ma: Optional[bool]
    volume_ratio: Optional[float]   # today's volume / prior-20-day average volume
    is_down_day: bool               # today's close below yesterday's close
    pct_since_entry: Optional[float]  # None for a fresh candidate with no position yet


def compute_price_action(price_history: pd.DataFrame, entry_price: Optional[float] = None,
                          high_lookback_days: int = 20) -> Optional[PriceAction]:
    """
    Returns None if there isn't enough history for even the shortest window
    (20 days) -- callers should skip the price-action section of the prompt
    entirely rather than report partial/misleading facts.
    """
    if len(price_history) < high_lookback_days + 1:
        return None

    df = price_history
    close = df["Close"]
    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    high_window = float(df["High"].iloc[-high_lookback_days:].max())
    pct_off_high = (float(today["Close"]) - high_window) / high_window * 100

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

    prior_20d_volume = df["Volume"].iloc[-21:-1]
    avg_volume = prior_20d_volume.mean() if len(prior_20d_volume) > 0 else None
    volume_ratio = (float(today["Volume"]) / float(avg_volume)) if avg_volume and avg_volume > 0 else None

    pct_since_entry = None
    if entry_price is not None and entry_price > 0:
        pct_since_entry = (float(today["Close"]) - entry_price) / entry_price * 100

    return PriceAction(
        price=float(today["Close"]),
        pct_off_high=float(pct_off_high),
        high_lookback_days=high_lookback_days,
        above_20ma=bool(today["Close"] > ma20) if pd.notna(ma20) else None,
        above_50ma=bool(today["Close"] > ma50) if ma50 is not None and pd.notna(ma50) else None,
        above_200ma=bool(today["Close"] > ma200) if ma200 is not None and pd.notna(ma200) else None,
        volume_ratio=volume_ratio,
        is_down_day=bool(today["Close"] < yesterday["Close"]),
        pct_since_entry=pct_since_entry,
    )
