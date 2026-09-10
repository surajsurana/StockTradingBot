"""
NSE equity-segment trading calendar -- the one piece of FUTURE knowledge
a live end-of-day strategy is allowed to have: whether tomorrow is a
trading day. Added 2026-09-10 for the Earnings Announcement Premium's
promotion to Pool A (SW-018): its entries and exits fire on a month's
LAST trading day, which a backtest can read off the next bar but a live
run (where today is always the final bar) cannot -- it has to be told
whether the next weekday is an exchange holiday.

Holiday list source: NSE's published 2026 trading-holiday circular, as
reproduced by two independent sites (groww.in/p/nse-holidays and
niftyscanner.in/market-calendar/2026, cross-checked 2026-09-10, 16
weekday holidays, identical on both). Weekend holidays (e.g. Independence
Day 2026-08-15, a Saturday) are omitted -- weekends are never trading
days anyway. Muhurat trading (a Sunday special session) is deliberately
NOT treated as a trading day: it is a token one-hour session, not a
full candle.

COVERAGE FALLBACK (disclosed): a year with no entry below is handled as
weekdays-only, with a printed warning. The failure mode is benign: if an
unlisted holiday falls on a month's last weekday, the real last trading
day goes unrecognised and that month's entry is MISSED (and an open
position exits one month later) -- never a trade on a wrong day. Add the
next year's list as soon as NSE publishes it (typically December).
"""

from datetime import date, timedelta

NSE_TRADING_HOLIDAYS = {
    2026: {
        date(2026, 1, 15),   # Municipal Corporation General Elections (Maharashtra)
        date(2026, 1, 26),   # Republic Day
        date(2026, 3, 3),    # Holi
        date(2026, 3, 26),   # Shri Ram Navami
        date(2026, 3, 31),   # Shri Mahavir Jayanti
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Id
        date(2026, 6, 26),   # Muharram
        date(2026, 9, 14),   # Ganesh Chaturthi
        date(2026, 10, 2),   # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali-Balipratipada
        date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
        date(2026, 12, 25),  # Christmas
    },
}

_warned_years = set()


def has_holiday_coverage(year: int) -> bool:
    return year in NSE_TRADING_HOLIDAYS


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if not has_holiday_coverage(d.year):
        if d.year not in _warned_years:
            _warned_years.add(d.year)
            print(f"WARNING: no NSE holiday list for {d.year} in deployment/nse_trading_calendar.py -- "
                  f"treating every weekday as a trading day until it is added.")
        return True
    return d not in NSE_TRADING_HOLIDAYS[d.year]


def next_trading_day(d: date) -> date:
    candidate = d + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def is_last_trading_day_of_month(d: date) -> bool:
    """True if no further trading day falls in d's calendar month. Does
    not check that d itself is a trading day -- the caller only ever asks
    about a day it has a completed candle for."""
    return next_trading_day(d).month != d.month
