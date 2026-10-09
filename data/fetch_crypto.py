"""
Crypto daily candles for the Pool E lane (added 2026-09-13) -- Binance's
public klines endpoint, no account or API key needed, deep history
(BTCUSDT from 2017). Prices are in USDT; the Pool E book is kept in USDT
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
# The five largest coins -- the "asset classes" a time-series rule times.
CRYPTO_MAJORS = ["BTC", "ETH", "BNB", "XRP", "SOL"]

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

# CoinDCX's OWN INR prices. The books are valued off Binance USDT x USDINR, which is a different
# market from the one the money is actually in: CoinDCX INR carries an India premium of 2-3% that
# moves on its own, so the two disagree by a little every day and by more on some days. Where the
# question is "what is this worth at the exchange I would sell it on", only the exchange can answer,
# and this is where that answer comes from.
#
# Public and unauthenticated -- the same ticker a logged-out visitor sees -- so reporting never
# depends on a credential being present or valid.
COINDCX_TICKER_URL = "https://api.coindcx.com/exchange/ticker"


def fetch_coindcx_inr_prices(symbols, timeout: int = 15) -> dict:
    """{SYMBOL: last INR price} for whichever of `symbols` CoinDCX lists an INR market for.

    A symbol with no INR market, or any failure at all, is simply absent: the caller falls back to
    its existing mark rather than showing a price nobody quoted."""
    wanted = {str(s).strip().upper() for s in (symbols or []) if str(s or "").strip()}
    if not wanted:
        return {}
    try:
        import requests
        rows = requests.get(COINDCX_TICKER_URL, timeout=timeout).json()
    except Exception:
        return {}
    out = {}
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        market = str(row.get("market", "")).upper()
        if not market.endswith("INR"):
            continue
        base = market[:-3]
        if base in wanted:
            try:
                price = float(row.get("last_price"))
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[base] = price
    return out

# WHAT "TODAY" MEANS AT COINDCX. Today's P&L is a price MINUS A REFERENCE, so marking at the
# exchange's price is only half of matching the exchange's number -- the reference has to be theirs
# too. Ours was Binance's previous UTC daily close: a different market, a different currency and a
# different day.
#
# CoinDCX's "Today" is an IST calendar day. Established by arithmetic, not assumption: on 2026-10-09
# their screen showed +Rs50.69 on 0.00031 BTC against a current value of Rs2,554.27, which implies a
# reference of Rs8,076,064 -- within 0.01% of the 00:45 IST candle, and nowhere near the UTC-day open
# (Rs8,086,188) or the previous UTC close (Rs8,120,826).
#
# NO NIGHTLY SNAPSHOT. The day's opening candle is HISTORY, so it can be asked for at any hour, it
# survives a restart, it can be recomputed for a past day, and there is no midnight job to miss.
COINDCX_CANDLES_URL = "https://public.coindcx.com/market_data/candles"
IST = timezone(timedelta(hours=5, minutes=30))


def ist_day_start(now: Optional[datetime] = None) -> datetime:
    """Midnight IST for the day `now` falls in -- the boundary CoinDCX's "Today" uses."""
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_coindcx_day_open(symbols, now: Optional[datetime] = None, timeout: int = 20) -> dict:
    """{SYMBOL: INR price at the start of today IST}, from CoinDCX's own candles.

    15-minute candles, because the IST midnight boundary (18:30 UTC) is not on a 1h or 1d boundary.
    Anything missing is simply absent and the caller keeps its existing reference -- a missing open
    must never be read as a zero."""
    wanted = [str(s).strip().upper() for s in (symbols or []) if str(s or "").strip()]
    if not wanted:
        return {}
    want_ms = int(ist_day_start(now).timestamp() * 1000)
    out = {}
    for symbol in wanted:
        try:
            import requests
            rows = requests.get(COINDCX_CANDLES_URL, timeout=timeout,
                                params={"pair": f"B-{symbol}_INR", "interval": "15m", "limit": 120}).json()
            if not isinstance(rows, list):
                continue
            hit = next((c for c in rows if int(c.get("time", 0)) == want_ms), None)
            if hit and float(hit.get("open", 0)) > 0:
                out[symbol] = float(hit["open"])
        except Exception:
            continue
    return out
