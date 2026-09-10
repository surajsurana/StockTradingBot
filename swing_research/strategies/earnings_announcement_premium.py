"""
Earnings Announcement Premium -- tests whether stocks rise, predictably,
in the month in which they are scheduled to report quarterly earnings.

Source: Frazzini, A. and Lamont, O.A. (2007), "The Earnings Announcement
Premium and Trading Volume," NBER Working Paper 13090 (full text read
2026-09-07 before implementation); international confirmation in Barber,
B.M., De George, E.T., Lehavy, R. and Trueman, B. (2013), "The earnings
announcement premium around the globe," Journal of Financial Economics
108(1). F&L (US, 1973-2004): a portfolio of stocks expected to announce
this month beats non-announcers by 61 bps/month (t > 5, Sharpe 0.94,
present in every 10-year sub-period, unrelated to market/size/value/
momentum); stocks whose past volume is concentrated in announcement
months earn 153 bps/month (~18%/yr long-short) while low-concentration
stocks earn an insignificant 39 bps. Barber et al. (46 countries,
1990-2009): announcement months beat other months by ~11%/yr globally;
INDIA's own within-country coefficient is +0.287%/month, t = 0.63 --
positive but NOT significant (97 months, ~459 firms/month). Disclosed up
front: the global evidence is strong, the India-specific evidence is
weak, and the volume-concentration refinement has never been tested on
NSE -- this experiment is that test.

Mechanism (F&L): the announcement is a scheduled, attention-grabbing
event; attention-constrained individual investors who rarely short are
net BUYERS of any stock in the news, and volume predictably surges at
the announcement -- the price pressure lifts the stock before, at, and
after the release (F&L Table VII: ~25 bps in the 10 days before, ~21 bps
in the 3-day window, ~30 bps in the days after; "a 3-day window misses
most of the premium"). Not PEAD (SW-007, Pool A): PEAD trades the
POST-announcement drift conditional on the SURPRISE sign, entering after
the news; this strategy enters BEFORE the announcement, unconditionally,
and holds through it -- the two never overlap in time on the same event.

=========================== DOCUMENTED RULES ===========================

- Expected announcer for month t: the firm announced in the same
  calendar month of the previous year (93% accurate among firms with
  exactly 4 announcements in the prior 12 months -- F&L's stated
  restriction, applied here).
- On the LAST TRADING DAY of month t-1, buy every stock expected to
  announce in month t; hold until the LAST TRADING DAY of month t; then
  re-form for the next month (a ~1-calendar-month hold that buys ~2
  weeks before the expected announcement and sells ~2 weeks after).
- Cross-sectional refinement: sort by the volume concentration ratio --
  the share of the stock's total volume over the previous 4 years that
  occurred in its actual announcement months, lagged 3 months; the
  high-concentration group carries the premium.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(new strategy, 2026-09-07 -- first swing candidate sourced by literature
search rather than the roadmap, per explicit direction after the intraday
program was paused on cost grounds)

1. LONG ONLY: the expected-announcer leg only; F&L's short leg (expected
   non-announcers) is dropped -- same reason as every prior strategy, no
   NSE cash SLB infrastructure. F&L's long leg alone: expected announcers
   earn ~1.9%/month excess (high-concentration group) vs ~0.4% in other
   months.
   Estimated impact: DIRECTIONALLY UNKNOWN.
2. ANNOUNCEMENT DATES from yfinance's Ticker.get_earnings_dates() history
   (data/fetch_earnings_calendar.py), reported rows only, date component
   only -- an unofficial source (disclosed in that module). Dates within
   7 days of each other collapsed to one event.
   Estimated impact: MINOR -- F&L's own Compustat dates carry the same
   day-level ambiguity; the strategy only needs the MONTH.
3. "EXACTLY 4 announcements in the prior 12 months" applied literally,
   counted over the trailing 365 days ending on the formation day.
   Estimated impact: MINOR, a direct restatement.
4. VOLUME CONCENTRATION RATIO as the ENTRY RANKING (Signal.confidence),
   not as a hard cut: this program's engine holds at most 10 positions
   universe-wide, so ranking all qualifying announcers by their ratio and
   taking the top of the list IS F&L's high-concentration group in
   practice; a stock without a computable ratio (insufficient history)
   ranks last (confidence 0), never excluded outright. Ratio computed
   from as few as 24 months / 8 announcement months of history instead of
   F&L's full 48 -- a disclosed warm-up relaxation.
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- with ~150-250
   expected announcers in a typical results month, which 10 get picked is
   decided by this ranking.
5. MONTH-END DETECTION from the trading calendar itself (a day is the
   month's last trading day if the next bar's month differs). This uses
   the exchange holiday calendar, which is published in advance, not any
   price information. The final bar of a series has no next bar: in
   research it is never treated as a month end; in live paper trading
   the strategy is constructed with is_last_trading_day_fn =
   deployment/nse_trading_calendar.is_last_trading_day_of_month, which
   answers from NSE's published holiday list (added 2026-09-10 for the
   SW-018 promotion -- a calendar lookup, not a rule change).
   Estimated impact: NEGLIGIBLE.
6. EXIT RULE: ONLY the next month-end close after entry, OR the
   protective stop below. No early exit on any signal.
7. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL.
   Estimated impact: MODERATE, one-directional -- earnings months are
   the most volatile months (F&L: cross-sectional volatility 14.7% vs
   13.7%), so an 8% stop will fire more often here than elsewhere.
8. POSITION SIZING: this program's standard 1% risk-per-unit (with the
   8% stop, ~12.5% of equity per position, at most 10 positions). F&L
   are value-weighted across hundreds of announcers.
   Estimated impact: MODERATE -- 10 equal positions vs a broad VW
   portfolio changes dispersion, not the sign of the effect.

Rebalance frequency: features computed DAILY, but entries and exits only
ever fire on a month's last trading day.
"""

from typing import Callable, Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

REQUIRED_PRIOR_YEAR_ANNOUNCEMENTS = 4
STOP_LOSS_PCT = 0.08
# 24 months of monthly volume + the 3-month lag before a concentration ratio
# first exists (swing_research/announcement_features.py), in trading days.
MIN_LOOKBACK_TRADING_DAYS = round(27 / 12 * 252)


class EarningsAnnouncementPremiumStrategy(Strategy):
    name = "earnings_announcement_premium"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = MIN_LOOKBACK_TRADING_DAYS

    def __init__(self, is_last_trading_day_fn: Optional[Callable[[object], bool]] = None):
        # None (research): the series' final bar is never a month end.
        # Live: deployment/nse_trading_calendar.is_last_trading_day_of_month,
        # so today -- always the final bar in a daily run -- can be one.
        self.is_last_trading_day_fn = is_last_trading_day_fn

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # Injected by the caller (research_director, via simulate_portfolio()'s
        # extra_columns_by_symbol) from swing_research/announcement_features.py.
        # Absent (e.g. a unit test not exercising that wiring) -> never qualifies.
        if "expected_announcer_next_month" not in df.columns:
            df["expected_announcer_next_month"] = False
        if "announcements_prior_12m" not in df.columns:
            df["announcements_prior_12m"] = 0
        if "volume_concentration_ratio" not in df.columns:
            df["volume_concentration_ratio"] = float("nan")

        months = pd.Series(df.index.month, index=df.index)
        is_month_end = months.ne(months.shift(-1))
        if len(is_month_end):
            # The series' final bar has no next bar: unknown (False) in research,
            # answered from the NSE holiday calendar in live paper trading.
            final_date = df.index[-1].date()
            is_month_end.iloc[-1] = (bool(self.is_last_trading_day_fn(final_date))
                                     if self.is_last_trading_day_fn is not None else False)
        df["is_month_end"] = is_month_end.values

        df["qualifies"] = (df["is_month_end"]
                           & df["expected_announcer_next_month"].fillna(False).astype(bool)
                           & (df["announcements_prior_12m"].fillna(0) == REQUIRED_PRIOR_YEAR_ANNOUNCEMENTS))
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev) or not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        vcr = row.volume_concentration_ratio
        confidence = 0.0 if pd.isna(vcr) else float(vcr)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price,
            stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=confidence, strategy_name=self.name,
            reason=(f"Expected to announce next month (announced in the same month last year; "
                    f"{int(row.announcements_prior_12m)} announcements in the prior 12 months); "
                    f"volume concentration ratio "
                    f"{'n/a' if pd.isna(vcr) else f'{float(vcr):.3f}'} -- month-end entry"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if bool(row.is_month_end) and entry_date is not None and row.date > entry_date:
            return float(row.Close)
        return None
