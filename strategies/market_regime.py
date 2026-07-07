"""
Market-regime filter: is the broader market itself in an uptrend?

Rule (kept simple and explainable, same philosophy as ma_crossover): Nifty's
close is above its own 200-day moving average => "bullish regime". This is
a widely used, well-understood trend definition, not something exotic.

Individual stock strategies can check this before taking a BUY signal, so a
stock-level crossover doesn't get taken when the whole market is trending
down — this is precisely the kind of situation that caused the repeated
ICICI Bank losses in the first backtest.
"""

import pandas as pd


def build_regime_series(nifty_history: pd.DataFrame, ma_period: int = 200) -> pd.Series:
    """
    Returns a boolean Series (indexed by date, same index as nifty_history)
    that is True on days the market is considered bullish.
    """
    ma = nifty_history["Close"].rolling(ma_period).mean()
    return nifty_history["Close"] > ma


def is_bullish_on(regime_series: pd.Series, date) -> bool:
    """
    Look up whether a given date was bullish. Returns False (safe default,
    i.e. "don't trade") if the date isn't in the series or the value is NaN
    (not enough history yet for the 200-day average).
    """
    if date not in regime_series.index:
        return False
    value = regime_series.loc[date]
    return bool(value) if pd.notna(value) else False
