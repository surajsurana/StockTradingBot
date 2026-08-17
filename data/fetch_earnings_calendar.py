"""
Forward earnings-announcement detection, via yfinance's
Ticker.get_earnings_dates() -- gives both the NEXT scheduled announcement
AND a history of past actual/estimate/surprise rows. Confirmed live-tested
2026-08-17 against RELIANCE.NS/TCS.NS/HDFCBANK.NS: ~24 quarters of
Reported EPS history available (back to 2020) -- a DIFFERENT yfinance
method from Ticker.earnings_history (the one checked during the original
2026-08-05 PEAD deferral investigation, which found only ~4-5 quarters).
This finding does NOT reopen that deferral decision -- Research Verdict
for SW-007 stays NOT_YET_EVALUATED, per explicit direction -- but it is
what makes a FORWARD-ONLY detection pipeline (this module) viable where a
full historical multi-year backtest still is not attempted here.

RELIABILITY DISCLOSURE (per explicit direction -- "do not rely on
scraped/unofficial data without documenting limitations"): yfinance is
NOT an official data source -- it is an unofficial aggregator/scraper of
Yahoo Finance, no SLA, no documented retention guarantee. It is, however,
already the ONLY data source integrated anywhere in this program (used
for every other strategy's price/volume data), so using it here is
consistent with existing practice, not a new, additional dependency. A
stronger, OFFICIAL alternative (NSE's own corporate-announcements feed)
is NOT integrated here and would be a separate, future data-source
project -- flagged, not built.

TIMING DISCLOSURE: get_earnings_dates() timestamps (e.g.
"2026-07-17 09:00:00-04:00") APPEAR to carry a time component, but this
has NOT been verified as a genuine, NSE-confirmed before/after-market
announcement time -- it may be a generic default Yahoo attaches uniformly.
This module does NOT trust the time component at all -- only the DATE is
used, and see deployment/pead_forward_engine.py's own conservative
same-day exclusion rule for how lookahead is avoided regardless of
whether a real announcement was actually before or after that day's close.

CHECK FREQUENCY: intended to be called once per trading day (same cadence
as every other paper-trading strategy), scanning the full frozen universe
(~457 symbols) -- one yfinance call per symbol, so a full scan is slower
than the existing single fetch_all() OHLCV call and has NOT been load-
tested against yfinance's own (undocumented) rate limits at this scale.
Disclosed, not silently assumed to be fine.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

# How many trailing quarterly rows to request -- needs at least 12 valid
# (non-NaN Reported EPS) quarters for a faithful seasonal-random-walk SUE
# calculation (current quarter + 8 trailing YoY-difference quarters, each
# of which itself needs a same-quarter-prior-year comparison -- see
# deployment/pead_signal.py's compute_sue() docstring). Requested with
# margin (16) since some quarters may be missing/NaN in practice.
EARNINGS_HISTORY_LIMIT = 16


@dataclass
class EarningsEvent:
    symbol: str
    announcement_date: date
    reported_eps: float
    eps_estimate: Optional[float]
    surprise_pct: Optional[float]
    trailing_actual_eps: list   # [most recent .. oldest], for SUE calculation -- see pead_signal.py


def fetch_recent_earnings_events(symbols: list, as_of_date: date, lookback_days: int = 10) -> list:
    """
    For each symbol, checks whether it REPORTED (Reported EPS is a real,
    non-NaN number -- not merely a forward-scheduled estimate) within the
    trailing `lookback_days` window ending at as_of_date. Returns one
    EarningsEvent per such REAL announcement found -- callers are
    responsible for tracking which (symbol, announcement_date) pairs
    they've already processed, so the same real-world event isn't acted
    on twice across multiple daily runs within the same lookback window
    (see deployment/pead_forward_engine.py).

    Symbols with no earnings activity in the window, or where the
    yfinance call itself fails (network error, delisted symbol, etc.),
    are silently skipped -- same soft-fail convention as
    data/fetch_historical.fetch_all().
    """
    window_start = as_of_date - timedelta(days=lookback_days)
    events = []

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.get_earnings_dates(limit=EARNINGS_HISTORY_LIMIT)
        except Exception:
            continue
        if df is None or df.empty:
            continue

        df = df.sort_index(ascending=False)   # most recent first, defensive (already yfinance's own order)
        reported = df[df["Reported EPS"].notna()]
        if reported.empty:
            continue

        most_recent = reported.iloc[0]
        announcement_date = most_recent.name.date()   # DATE ONLY -- the time component is not trusted, see module docstring
        if not (window_start <= announcement_date <= as_of_date):
            continue

        trailing_actual_eps = [float(v) for v in reported["Reported EPS"].tolist()]
        eps_estimate = most_recent.get("EPS Estimate")
        surprise_pct = most_recent.get("Surprise(%)")

        events.append(EarningsEvent(
            symbol=symbol, announcement_date=announcement_date,
            reported_eps=float(most_recent["Reported EPS"]),
            eps_estimate=float(eps_estimate) if pd.notna(eps_estimate) else None,
            surprise_pct=float(surprise_pct) if pd.notna(surprise_pct) else None,
            trailing_actual_eps=trailing_actual_eps,
        ))

    return events
