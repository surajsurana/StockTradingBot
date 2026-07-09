"""
NSE equity tick sizes vary by instrument -- most are 0.05, but some (KEI.NS
was the one that surfaced this in production: 0.50) use a wider tick.
Kite rejects any order whose price isn't a multiple of the instrument's own
tick size (a 400 InputException, not a silent failure -- but execution_engine
previously always rounded to a hardcoded 0.05, so any instrument with a
different tick size had every live order rejected).

Kite's /instruments/NSE endpoint returns a CSV of every NSE equity
instrument with its tick_size, and is a public, no-special-tier-required
endpoint (same access level as order placement and holdings, unlike live
quotes). Cached locally and refreshed every few days, since tick sizes
essentially never change day to day -- no need to re-fetch ~2000 rows on
every single order.
"""

import csv
import io
import json
import os
from datetime import datetime, timedelta

import requests

TICK_SIZE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nse_tick_sizes.json")
DEFAULT_TICK_SIZE = 0.05
CACHE_MAX_AGE_DAYS = 7


def fetch_nse_tick_sizes(api_key: str, access_token: str) -> dict:
    """Tradingsymbol (no .NS suffix) -> tick_size, for every NSE equity instrument."""
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    resp = requests.get("https://api.kite.trade/instruments/NSE", headers=headers)
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    return {
        row["tradingsymbol"]: float(row["tick_size"])
        for row in reader
        if row.get("tradingsymbol") and row.get("tick_size")
    }


def _load_cache(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return json.loads(content) if content else None


def _save_cache(cache: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def _is_stale(cache: dict, max_age_days: int) -> bool:
    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    return datetime.now() - fetched_at > timedelta(days=max_age_days)


def get_tick_size(symbol: str, api_key: str, access_token: str,
                   cache_path: str = TICK_SIZE_CACHE_PATH,
                   max_age_days: int = CACHE_MAX_AGE_DAYS) -> float:
    """
    Tick size for a symbol (yfinance-style, e.g. "KEI.NS"). Refreshes the
    cached instrument dump if missing or stale, otherwise reads straight
    from cache. Falls back to the common 0.05 default if the symbol isn't
    found or the fetch itself fails -- a wrong tick size just means the
    order might get rejected and can be retried, so this should never be
    the thing that blocks trading outright.
    """
    tradingsymbol = symbol.replace(".NS", "")
    cache = _load_cache(cache_path)

    if cache is None or _is_stale(cache, max_age_days):
        try:
            tick_sizes = fetch_nse_tick_sizes(api_key, access_token)
            cache = {"fetched_at": datetime.now().isoformat(), "tick_sizes": tick_sizes}
            _save_cache(cache, cache_path)
        except Exception as e:
            print(f"WARNING: could not refresh NSE tick size cache: {e}")
            if cache is None:
                return DEFAULT_TICK_SIZE

    return cache["tick_sizes"].get(tradingsymbol, DEFAULT_TICK_SIZE)
