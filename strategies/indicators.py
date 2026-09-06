"""
Shared technical indicators used by more than one strategy. Kept in one
place so ma_crossover, mean_reversion, and any future strategy compute
things like ATR/RSI/Bollinger Bands identically rather than each having its
own slightly-different copy.
"""

import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def bollinger_bands(close: pd.Series, period: int = 20, num_std: float = 2.0):
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return lower, middle, upper


def macd(close: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9):
    """Standard MACD: fast EMA minus slow EMA, with its own EMA as the
    signal line. Returns (macd_line, signal_line, histogram) -- histogram
    = macd_line - signal_line, positive once the fast EMA is accelerating
    away from the slow EMA faster than the signal line has caught up."""
    fast_ema = close.ewm(span=fast_period, adjust=False).mean()
    slow_ema = close.ewm(span=slow_period, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
