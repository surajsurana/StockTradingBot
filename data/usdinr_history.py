"""
What one US dollar was worth in rupees on a GIVEN DAY, not today.

WHY THIS EXISTS. Every foreign-currency row on the dashboard used to convert at today's rate, which
meant the rupee profit on a trade closed three weeks ago moved every day as USD/INR moved. For a
finished trade that is simply wrong: the result stopped changing when the trade closed. Mixing
currency drift into a strategy's track record makes it impossible to tell "the strategy made money"
from "the dollar went up".

THE RULE THIS SUPPORTS:
  - a CLOSED trade converts at the rate on its exit date -- that is what it earned, and it is fixed;
  - anything still OPEN converts at today's rate -- that is genuinely what it is worth now.

WEEKENDS AND HOLIDAYS. The FX series has no row for a Saturday, and a US trade can close on a day
Indian banks are shut. Asking for such a date returns the most recent earlier rate, which is the last
price anyone could actually have dealt at -- never the next one, which would be hindsight.

FALLING BACK IS EXPLICIT. If the history cannot be fetched or a date predates the series, rate_on()
returns None rather than a guess, and the caller decides what to do (the dashboard falls back to
today's rate and says so on the row). A silently wrong exchange rate is worse than a visibly current
one, because nothing on the page would look unusual.
"""

import json
import os
from datetime import date, datetime, timedelta
from typing import Optional

SYMBOL = "USDINR=X"
CACHE_FILENAME = "usdinr_history.json"
CACHE_MAX_AGE_HOURS = 20          # refreshed roughly daily; FX history for past days never changes
FETCH_PERIOD = "5y"


def cache_path(state_dir: str) -> str:
    return os.path.join(state_dir, CACHE_FILENAME)


def load_history(state_dir: str) -> dict:
    """{"YYYY-MM-DD": rate} from the cache. An unreadable or malformed cache is an empty history, not
    an exception -- the dashboard must still render, just without per-date rates."""
    try:
        with open(cache_path(state_dir), encoding="utf-8") as f:
            payload = json.load(f)
        rates = payload.get("rates") if isinstance(payload, dict) else None
        if not isinstance(rates, dict):
            return {}
        return {str(k): float(v) for k, v in rates.items() if _is_number(v)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def cache_is_stale(state_dir: str, now: Optional[datetime] = None) -> bool:
    try:
        age = (now or datetime.now()) - datetime.fromtimestamp(os.path.getmtime(cache_path(state_dir)))
    except OSError:
        return True
    return age > timedelta(hours=CACHE_MAX_AGE_HOURS)


def rate_on(history: dict, when, fallback: Optional[float] = None) -> Optional[float]:
    """The rate on `when`, or the most recent one BEFORE it when that day has no quote (weekend,
    holiday, or a US close on an Indian holiday). Never looks forward: a rate published after the
    trade closed was not available to it.

    Returns `fallback` when the history is empty or `when` predates the series -- callers pass
    today's rate there and mark the row, rather than inventing a number."""
    if not history:
        return fallback
    try:
        target = when if isinstance(when, str) else when.isoformat()
        date.fromisoformat(target)                 # rejects junk before it can match a key
    except (TypeError, ValueError, AttributeError):
        return fallback
    if target in history:
        return history[target]
    earlier = [d for d in history if d <= target]
    return history[max(earlier)] if earlier else fallback


def refresh(state_dir: str, period: str = FETCH_PERIOD) -> dict:
    """Downloads the daily USD/INR series and writes the cache. Returns the history it wrote, or the
    existing cache unchanged if the download fails -- a failed refresh must never empty a cache that
    was serving fine."""
    existing = load_history(state_dir)
    try:
        import yfinance as yf
        frame = yf.Ticker(SYMBOL).history(period=period, auto_adjust=False)
        rates = {}
        for stamp, row in frame.iterrows():
            close = row.get("Close")
            if close is None or close != close or float(close) <= 0:      # NaN-safe
                continue
            rates[stamp.date().isoformat()] = round(float(close), 4)
    except Exception:
        return existing                            # network, yfinance, schema -- all non-fatal here
    if not rates:
        return existing
    merged = {**existing, **rates}
    os.makedirs(state_dir, exist_ok=True)
    tmp = cache_path(state_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"symbol": SYMBOL, "fetched_at": datetime.now().isoformat(), "rates": merged}, f)
    os.replace(tmp, cache_path(state_dir))         # atomic: a crash mid-write leaves the old cache
    return merged


def history_for(state_dir: str, refresh_if_stale: bool = True) -> dict:
    """The history the dashboard should use: cached, refreshed at most once a day, never raising."""
    if refresh_if_stale and cache_is_stale(state_dir):
        try:
            return refresh(state_dir)
        except Exception:
            return load_history(state_dir)
    return load_history(state_dir)
