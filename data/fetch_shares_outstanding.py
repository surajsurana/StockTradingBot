"""
Shares-outstanding SNAPSHOT fetcher, via yfinance's Ticker.fast_info["shares"]
-- built for the Turnover / Liquidity Anomaly candidate (Datar, Naik &
Radcliffe, 1998), swing_research/strategies/turnover_liquidity.py, whose
turnover measure is Volume / shares outstanding.

DISCLOSED LIMITATION (the roadmap candidate's own known_weaknesses, see
swing_research/research_roadmap.py's turnover_liquidity entry): this
platform has no historical shares-outstanding SERIES, only whatever
yfinance reports as the CURRENT count. A real backtest therefore applies
TODAY's share count across the whole price history -- mild for a large,
stable Nifty 500 constituent, more material for any stock with a big past
split, bonus issue, buyback or follow-on dilution between the backtest's
start date and today. Not correctable without a paid/official historical
shares-outstanding feed, which this program does not have -- flagged, not
silently assumed away.

Confirmed live (2026-09-17) that fast_info["shares"] returns a plausible
value for RELIANCE.NS/TCS.NS/HDFCBANK.NS (within ~0.02% of Ticker.info's
own sharesOutstanding field, itself just a slower call to the same
underlying data) -- fast_info is used here since it is the lighter call.

Cache pattern deliberately mirrors data/fetch_earnings_calendar.py's
on-disk JSON cache (fetched_at + per-symbol payload, cache-first with an
optional max-age refresh) -- same reasoning: yfinance is not officially
rate-limited or SLA-backed, so a full ~457-symbol universe scan should be
done once and reused, not repeated per experiment run. Unlike
get_earnings_dates(), fast_info has not been observed to leak memory at
scale, so this module does not need that one's chunked-subprocess
isolation -- if that changes, the same mitigation could be applied here.
"""

import json
import os
from datetime import date
from typing import Optional

import yfinance as yf

SHARES_OUTSTANDING_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "shares_outstanding_cache.json")


def fetch_shares_outstanding(symbols: list) -> dict:
    """{symbol: float shares outstanding} for whatever symbols yfinance
    returns a positive value for. Symbols that fail or return nothing are
    silently skipped, same soft-fail convention as fetch_all()."""
    result = {}
    for symbol in symbols:
        try:
            shares = yf.Ticker(symbol).fast_info.get("shares")
        except Exception:
            continue
        if shares and float(shares) > 0:
            result[symbol] = float(shares)
    return result


def _read_cache_payload(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_shares_outstanding_cache(path: str = SHARES_OUTSTANDING_CACHE_PATH) -> dict:
    payload = _read_cache_payload(path)
    if payload is None:
        return {}
    return {symbol: float(v) for symbol, v in payload.get("shares_by_symbol", {}).items()}


def shares_outstanding_cache_age_days(path: str = SHARES_OUTSTANDING_CACHE_PATH,
                                      today: Optional[date] = None) -> Optional[int]:
    payload = _read_cache_payload(path)
    if payload is None or not payload.get("fetched_at"):
        return None
    return ((today or date.today()) - date.fromisoformat(payload["fetched_at"])).days


def save_shares_outstanding_cache(shares_by_symbol: dict, path: str = SHARES_OUTSTANDING_CACHE_PATH) -> None:
    payload = {
        "source": "yfinance Ticker.fast_info['shares'] -- a CURRENT snapshot, not a historical series",
        "fetched_at": date.today().isoformat(),
        "symbol_count": len(shares_by_symbol),
        "shares_by_symbol": {symbol: v for symbol, v in sorted(shares_by_symbol.items())},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)


def get_shares_outstanding(symbols: list, path: str = SHARES_OUTSTANDING_CACHE_PATH,
                           refresh: bool = False, fetch_fn=None,
                           max_age_days: Optional[int] = None, today: Optional[date] = None) -> dict:
    """Cache-first: symbols already cached are served from disk; only the
    missing ones (or all, with refresh=True) are fetched, then the cache
    is rewritten. fetch_fn is injectable for tests. max_age_days: if the
    cache is older than this, every requested symbol is re-fetched (this
    is a snapshot, so "current" only means "as of the last refresh")."""
    if max_age_days is not None and not refresh:
        age = shares_outstanding_cache_age_days(path, today=today)
        if age is not None and age > max_age_days:
            print(f"Shares-outstanding cache is {age} days old (> {max_age_days}) -- refreshing all "
                  f"{len(symbols)} symbol(s) via yfinance...")
            refresh = True
    cached = {} if refresh else load_shares_outstanding_cache(path)
    missing = [s for s in symbols if s not in cached]
    if missing:
        fetch = fetch_fn or fetch_shares_outstanding
        cached.update(fetch(missing))
        save_shares_outstanding_cache(cached, path)
    return {s: cached[s] for s in symbols if s in cached}
