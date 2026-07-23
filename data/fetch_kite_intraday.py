"""
Intraday historical candle data via Kite Connect's own historical API --
NOT yfinance. yfinance only ever gives daily bars, which is fine for the
swing strategies (fetch_historical.py) but useless for backtesting a true
same-day (MIS) strategy, which needs real within-day price action.

This depends on the PAID Kite Connect "Connect"-type app specifically
created for market data (config.settings.KITE_MARKET_DATA_API_KEY/SECRET
-- kept deliberately separate from KITE_API_KEY/KITE_ACCESS_TOKEN, which
stay on the free "Personal" tier and keep placing the live trading bot's
real orders untouched). Verified live (2026-07-23) against the real
account: Kite's historical API caps how much date-range you can request
in a SINGLE call, but that's a per-request span limit, not a retention
limit -- real 5-minute data exists at least 240 days back. MAX_SPAN_DAYS
below is chunked across automatically so callers can ask for any range.

    MAX_SPAN_DAYS = {"5minute": 100, "15minute": 200, "60minute": 400}
(empirically confirmed via real API calls; Kite doesn't document exact
numbers, so these are what the API itself reported when a request
exceeded its limit -- re-verify if this ever starts erroring differently.)

For backtesting only -- this module is never imported by run_daily.py or
monitor_positions.py, which keep using the free-tier order/portfolio APIs
exactly as before.
"""

import os
import sys
import time
import io
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

# Same fix as auth/kite_auto_login.py -- makes the absolute "auth.kite_auto_login"
# import resolve whether this module is imported normally (project root on the
# path) or run directly as `python data/fetch_kite_intraday.py` (where Python
# would otherwise only put data/ itself on the path).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth.kite_auto_login import auto_login

MAX_SPAN_DAYS = {
    "minute": 60, "3minute": 100, "5minute": 100, "10minute": 100,
    "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000,
}

INSTRUMENTS_URL = "https://api.kite.trade/instruments/NSE"
HISTORICAL_URL_TEMPLATE = "https://api.kite.trade/instruments/historical/{instrument_token}/{interval}"

_instrument_token_cache: dict = {}


def get_market_data_session(settings) -> dict:
    """
    Logs into the dedicated market-data Kite Connect app (a fresh login each
    call -- this module is only ever used from standalone backtest scripts,
    not a long-running process, so there's no persisted-token complexity to
    manage here unlike auth/kite_auto_login.py's ensure_fresh_kite_session).
    Returns ready-to-use request headers.
    """
    access_token = auto_login(
        settings.KITE_MARKET_DATA_API_KEY, settings.KITE_MARKET_DATA_API_SECRET,
        settings.KITE_USER_ID, settings.KITE_PASSWORD, settings.KITE_TOTP_SECRET,
    )
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {settings.KITE_MARKET_DATA_API_KEY}:{access_token}",
    }


def _load_instrument_tokens(headers: dict) -> dict:
    """Downloads Kite's full NSE instrument dump (a CSV, one row per
    tradeable instrument) ONCE and caches tradingsymbol -> instrument_token
    for the whole process -- avoids one API call per symbol just to look up
    a token, since this file rarely changes within a single backtest run."""
    global _instrument_token_cache
    if _instrument_token_cache:
        return _instrument_token_cache

    resp = requests.get(INSTRUMENTS_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    # equity segment only -- NSE's dump also includes indices/other instrument types
    df = df[df["segment"] == "NSE"]
    _instrument_token_cache = dict(zip(df["tradingsymbol"], df["instrument_token"]))
    return _instrument_token_cache


def get_instrument_token(tradingsymbol: str, headers: dict) -> Optional[int]:
    """tradingsymbol: e.g. "RELIANCE" (no .NS suffix -- that's a yfinance
    convention, Kite uses bare NSE trading symbols)."""
    tokens = _load_instrument_tokens(headers)
    return tokens.get(tradingsymbol)


def _fetch_one_chunk(instrument_token: int, interval: str, from_date: date, to_date: date,
                      headers: dict) -> list:
    resp = requests.get(
        HISTORICAL_URL_TEMPLATE.format(instrument_token=instrument_token, interval=interval),
        headers=headers,
        params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ValueError(f"Kite historical API error for token {instrument_token}: {resp.json()}")
    return resp.json().get("data", {}).get("candles", [])


def fetch_intraday_candles(instrument_token: int, interval: str, from_date: date, to_date: date,
                            headers: dict, rate_limit_delay: float = 0.35) -> pd.DataFrame:
    """
    Fetches intraday candles for the full [from_date, to_date] range,
    automatically chunking into multiple requests to respect Kite's
    per-request span cap (MAX_SPAN_DAYS). rate_limit_delay is a small pause
    between chunk requests -- Kite's historical API is rate-limited (~3
    req/sec is the commonly cited safe ceiling); this keeps a comfortable
    margin under that for a script making many sequential calls.
    """
    max_span = MAX_SPAN_DAYS.get(interval, 100)
    all_candles = []
    chunk_end = to_date
    first_chunk = True
    while chunk_end >= from_date:
        chunk_start = max(from_date, chunk_end - timedelta(days=max_span - 1))
        if not first_chunk:
            time.sleep(rate_limit_delay)
        first_chunk = False
        candles = _fetch_one_chunk(instrument_token, interval, chunk_start, chunk_end, headers)
        all_candles = candles + all_candles  # prepend -- chunks fetched newest-first
        chunk_end = chunk_start - timedelta(days=1)

    if not all_candles:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    df = pd.DataFrame(all_candles, columns=["datetime", "Open", "High", "Low", "Close", "Volume"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="first")]  # chunk boundaries can overlap by one candle
    return df


def fetch_all_intraday(symbols: list[str], interval: str, from_date: date, to_date: date,
                        settings, rate_limit_delay: float = 0.35) -> dict[str, pd.DataFrame]:
    """
    symbols: bare NSE trading symbols, e.g. ["RELIANCE", "TCS"] (no .NS).
    Skips any symbol whose instrument_token can't be found or whose fetch
    fails, with a warning -- same fail-soft convention as
    data/fetch_historical.py's fetch_all().
    """
    headers = get_market_data_session(settings)
    data = {}
    for symbol in symbols:
        token = get_instrument_token(symbol, headers)
        if token is None:
            print(f"WARNING: no instrument_token found for {symbol} -- skipping.")
            continue
        try:
            df = fetch_intraday_candles(token, interval, from_date, to_date, headers,
                                         rate_limit_delay=rate_limit_delay)
            if df.empty:
                print(f"WARNING: no intraday data returned for {symbol} -- skipping.")
                continue
            data[symbol] = df
        except Exception as e:
            print(f"WARNING: could not fetch intraday data for {symbol}: {e}")
        time.sleep(rate_limit_delay)
    return data


if __name__ == "__main__":
    from config import settings as live_settings

    headers = get_market_data_session(live_settings)
    token = get_instrument_token("RELIANCE", headers)
    print(f"RELIANCE instrument_token: {token}")

    df = fetch_intraday_candles(token, "5minute", date.today() - timedelta(days=30), date.today(), headers)
    print(df.tail())
    print(f"\n{len(df)} candles fetched.")
