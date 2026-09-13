"""
Crypto daily candles for the Pool F lane (added 2026-09-13) -- Binance's
public klines endpoint, no account or API key needed, deep history
(BTCUSDT from 2017). Prices are in USDT; the Pool F book is kept in USDT
and shown in rupees at the USD/INR rate from yfinance (USDT trades at a
small premium to USD in India -- disclosed, not modelled).

Verified reachable from both the dev machine and the VPS on 2026-09-13
(HTTP 200). CoinDCX's public INR candles are also reachable but have a
much shorter history, so research runs on USDT pairs.

Same fail-soft convention as data/fetch_historical.fetch_all(): a symbol
that errors or returns nothing is skipped with a warning, never a crash.
"""

import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
KLINES_PER_REQUEST = 1000
DEFAULT_USDINR = 95.0

# Large, liquid USDT spot pairs -- no stablecoins, no leveraged tokens.
# Order is by rough market-cap rank on 2026-09-13; symbols Binance does
# not list (or delists later) are skipped at fetch time, not an error.
CRYPTO_UNIVERSE = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK",
    "LTC", "BCH", "TRX", "ATOM", "UNI", "XLM", "ETC", "NEAR", "APT", "ARB",
    "OP", "FIL", "ICP", "HBAR", "VET", "ALGO", "AAVE", "MKR", "INJ", "SUI",
    "TON", "RENDER", "GRT", "SAND", "MANA", "EGLD", "THETA", "XTZ", "EOS", "FET",
]


def klines_to_dataframe(rows: list) -> pd.DataFrame:
    """Binance kline rows -> OHLCV DataFrame indexed by the candle's UTC
    open date (tz-naive midnight), the shape every engine here expects."""
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["open_time", "Open", "High", "Low", "Close", "Volume"]
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    df = df.drop(columns=["open_time"]).astype(float)
    df.index.name = None
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_binance_daily(symbol: str, start: Optional[date] = None, end: Optional[date] = None,
                        quote: str = "USDT", pause: float = 0.1) -> pd.DataFrame:
    """Every completed daily candle for {symbol}{quote} between start and
    end (inclusive), paginated in KLINES_PER_REQUEST chunks. The current,
    still-open day is dropped so no bar is ever partial."""
    end = end or datetime.now(timezone.utc).date()
    start = start or date(2017, 1, 1)
    start_ms = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    rows = []
    while start_ms <= end_ms:
        resp = requests.get(BINANCE_KLINES_URL, params={"symbol": f"{symbol}{quote}", "interval": "1d",
                                                        "startTime": start_ms, "endTime": end_ms,
                                                        "limit": KLINES_PER_REQUEST}, timeout=30)
        if resp.status_code != 200:
            raise ValueError(f"Binance klines {symbol}{quote}: HTTP {resp.status_code} {resp.text[:120]}")
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < KLINES_PER_REQUEST:
            break
        start_ms = int(batch[-1][0]) + 24 * 3600 * 1000
        time.sleep(pause)
    df = klines_to_dataframe(rows)
    today_utc = pd.Timestamp(datetime.now(timezone.utc).date())
    return df[df.index < today_utc]   # drop the open candle


def fetch_all_crypto_daily(symbols: list, years: float = 5, end: Optional[date] = None,
                           pause: float = 0.1) -> dict:
    end = end or datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(years * 365.25))
    data = {}
    for symbol in symbols:
        try:
            df = fetch_binance_daily(symbol, start=start, end=end, pause=pause)
        except Exception as e:
            print(f"WARNING: could not fetch {symbol}: {e}")
            continue
        if df.empty:
            print(f"WARNING: no candles for {symbol} -- skipping.")
            continue
        data[symbol] = df
        time.sleep(pause)
    return data


def fetch_crypto_last_prices(symbols: list, quote: str = "USDT") -> dict:
    """Latest traded price per symbol from Binance's ticker endpoint (one
    request for the whole exchange, filtered)."""
    resp = requests.get(BINANCE_PRICE_URL, timeout=30)
    if resp.status_code != 200:
        raise ValueError(f"Binance ticker/price: HTTP {resp.status_code}")
    wanted = {f"{s}{quote}": s for s in symbols}
    return {wanted[row["symbol"]]: float(row["price"]) for row in resp.json() if row.get("symbol") in wanted}


def fetch_usdinr_rate(default: float = DEFAULT_USDINR) -> float:
    """USD/INR from yfinance (USDINR=X), last close; the default if the
    fetch fails. USDT is treated as 1 USD (small Indian premium ignored)."""
    try:
        import yfinance as yf
        hist = yf.Ticker("USDINR=X").history(period="5d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"WARNING: USD/INR fetch failed ({e}); using {default}")
    return default
