"""
Per-symbol, per-day earnings-announcement features for the Earnings
Announcement Premium strategy (swing_research/strategies/
earnings_announcement_premium.py), built from a symbol's own history of
past announcement DATES (data/fetch_earnings_calendar.py's
get_announcement_date_history()) plus its own daily volume. Joined onto
each symbol's OHLCV frame through simulate_portfolio()'s
extra_columns_by_symbol, the same channel every cross-sectional signal
in swing_research/cross_sectional.py uses -- no engine change needed.

Everything here is computed from information available strictly on or
before each day: a day's features only ever look at announcement dates
<= that day and volume in months that ended before the lag window.

Three columns, straight from Frazzini & Lamont (2007):

expected_announcer_next_month  -- F&L's "previous year's announcement
    month" forecast: True on day d if the symbol announced in the SAME
    calendar month as the month AFTER d's month, one year earlier (an
    announcement in July 2024 makes the stock an expected announcer for
    July 2025, so the flag is True through June 2025 -- the strategy
    only acts on it at June's last trading day).

announcements_prior_12m  -- number of announcements in the trailing 365
    days ending on d; F&L restrict the strategy to firms with EXACTLY 4
    in the prior 12 months (their 93%-accurate forecasting subsample).
    Dates within 7 calendar days of an earlier one are collapsed first
    (yfinance occasionally lists a board-meeting date and its revision
    as two rows for one result).

volume_concentration_ratio  -- F&L's cross-sectional sort variable: the
    share of a stock's total volume over the trailing 4 years that
    occurred in its actual announcement months, lagged 3 months (their
    guard against Gervais et al.'s high-frequency volume effects). High
    concentration stocks earn the bulk of the premium (153 bps/month vs
    39 for low). Computed once per calendar month from monthly volume
    sums, then carried onto every trading day of that month. NaN until
    at least VCR_MIN_MONTHS of volume history and
    VCR_MIN_ANNOUNCEMENT_MONTHS announcement months are available -- a
    disclosed relaxation of F&L's full 48-month requirement so that a
    10-year price history yields ~8 tradeable years rather than ~6.
"""

import bisect
from datetime import date, timedelta

import pandas as pd

VCR_WINDOW_MONTHS = 48
VCR_LAG_MONTHS = 3
VCR_MIN_MONTHS = 24
VCR_MIN_ANNOUNCEMENT_MONTHS = 8
DEDUPE_WINDOW_DAYS = 7
FEATURE_COLUMNS = ("expected_announcer_next_month", "announcements_prior_12m", "volume_concentration_ratio")


def dedupe_announcement_dates(dates: list, window_days: int = DEDUPE_WINDOW_DAYS) -> list:
    """Sorted dates with any date falling within window_days after a kept
    date dropped -- one real result event, one date."""
    kept = []
    for d in sorted(set(dates)):
        if not kept or (d - kept[-1]).days > window_days:
            kept.append(d)
    return kept


def _next_month(year: int, month: int) -> tuple:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def compute_announcement_features(price_history: pd.DataFrame, announcement_dates: list) -> pd.DataFrame:
    """One symbol. Returns a DataFrame indexed exactly like price_history
    with the three FEATURE_COLUMNS (see module docstring)."""
    idx = price_history.index
    dates = dedupe_announcement_dates(announcement_dates or [])
    announcement_months = {(d.year, d.month) for d in dates}

    # expected_announcer_next_month: same calendar month next year as a past announcement.
    day_dates = [ts.date() for ts in idx]
    expected = []
    prior_12m = []
    for d in day_dates:
        ny, nm = _next_month(d.year, d.month)
        expected.append((ny - 1, nm) in announcement_months)
        window_start = d - timedelta(days=365)
        # count of announcement dates in (window_start, d] -- dates is sorted
        prior_12m.append(bisect.bisect_right(dates, d) - bisect.bisect_right(dates, window_start))

    # volume_concentration_ratio: monthly, lagged, carried onto the days of its month.
    monthly_volume = price_history["Volume"].resample("ME").sum() if len(idx) else pd.Series(dtype=float)
    month_keys = [(ts.year, ts.month) for ts in monthly_volume.index]
    is_announcement_month = pd.Series([k in announcement_months for k in month_keys],
                                      index=monthly_volume.index, dtype=float)
    vol = monthly_volume.astype(float)
    total_roll = vol.rolling(VCR_WINDOW_MONTHS, min_periods=VCR_MIN_MONTHS).sum().shift(VCR_LAG_MONTHS)
    ann_vol_roll = (vol * is_announcement_month).rolling(VCR_WINDOW_MONTHS, min_periods=VCR_MIN_MONTHS).sum() \
        .shift(VCR_LAG_MONTHS)
    ann_count_roll = is_announcement_month.rolling(VCR_WINDOW_MONTHS, min_periods=VCR_MIN_MONTHS).sum() \
        .shift(VCR_LAG_MONTHS)
    vcr_monthly = ann_vol_roll / total_roll
    vcr_monthly[(ann_count_roll < VCR_MIN_ANNOUNCEMENT_MONTHS) | (total_roll <= 0)] = float("nan")
    vcr_by_month = {k: v for k, v in zip(month_keys, vcr_monthly.tolist())}
    vcr_daily = [vcr_by_month.get((d.year, d.month), float("nan")) for d in day_dates]

    return pd.DataFrame({
        "expected_announcer_next_month": expected,
        "announcements_prior_12m": prior_12m,
        "volume_concentration_ratio": vcr_daily,
    }, index=idx)


def compute_announcement_features_by_symbol(data: dict, announcement_dates_by_symbol: dict) -> dict:
    """{symbol: features DataFrame} for every symbol in `data`; a symbol
    with no known announcement history gets all-False / 0 / NaN features
    (never qualifies), not an error."""
    features = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        features[symbol] = compute_announcement_features(df.sort_index(), announcement_dates_by_symbol.get(symbol, []))
    return features
