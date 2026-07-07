"""
Historical (and near-real-time) daily candle data, via yfinance.

Why yfinance and not Kite: your Kite Connect app is on the free "Personal"
plan, which doesn't include the historical/live market data APIs (that's the
paid "Connect" tier). yfinance is free and sufficient for daily-candle swing
strategies. If you later upgrade to Kite Connect's paid tier, this file can
be swapped for a Kite-based fetcher without touching strategies, risk, or
execution code -- they only care about getting a pandas DataFrame back.
"""

import pandas as pd
import yfinance as yf


def fetch_daily_candles(symbol: str, period: str = "2y") -> pd.DataFrame:
    """
    Fetch daily OHLCV candles for a symbol.

    symbol: e.g. "RELIANCE.NS" (NSE tickers need the .NS suffix in yfinance)
    period: how far back, e.g. "1y", "2y", "5y", "max"

    Returns a DataFrame indexed by date with columns: Open, High, Low, Close, Volume
    """
    df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)

    if df is None or df.empty:
        raise ValueError(f"No data returned for {symbol}. Check the ticker or your internet connection.")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()
    return df


def fetch_all(symbols: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    """Fetch daily candles for a list of symbols. Skips any that fail, with a warning."""
    data = {}
    for symbol in symbols:
        try:
            data[symbol] = fetch_daily_candles(symbol, period=period)
        except Exception as e:
            print(f"WARNING: could not fetch {symbol}: {e}")
    return data


def fetch_nifty(period: str = "2y") -> pd.DataFrame:
    """
    Fetch Nifty 50 index history. Used as a market-regime filter: strategies
    can check whether the broader market is trending up before taking a
    signal, rather than reacting purely to one stock's own chart.
    """
    return fetch_daily_candles("^NSEI", period=period)


if __name__ == "__main__":
    df = fetch_daily_candles("RELIANCE.NS", period="6mo")
    print(df.tail())
    print(f"\n{len(df)} rows fetched.")
