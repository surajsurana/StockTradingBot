"""
Turn-of-the-Year / January Effect -- tests whether NSE SMALL-CAP stocks
earn abnormally strong returns in the first trading days of January,
long-only. Combines two building blocks already in this program:
Turn-of-the-Month's pure CALENDAR/SEASONALITY mechanism (entry/exit keyed
to a trading-day ROW-POSITION window around a specific calendar event,
computed directly from the price history's own index, no injected
eligibility column needed for correctness) and Size Premium / Banz's
point-in-time market-cap cross-sectional percentile (bottom-decile
smallest-market-cap filter, reusing
swing_research.cross_sectional.compute_market_cap_percentile_ranks() and
data/fetch_shares_outstanding.py verbatim) -- because the published
mechanism is explicitly small-cap-concentrated, unlike Turn-of-the-Month's
deliberately universe-wide application.

Source: Keim, D.B. (1983), "Size-Related Anomalies and Stock Return
Seasonality: Further Empirical Evidence," Journal of Financial Economics,
Vol. 12, No. 1. Keim documents that roughly half of the small-firm size
premium's entire annual magnitude is concentrated in the first few
trading days of January alone (historically linked to December
tax-loss-selling pressure unwinding and a subsequent early-January
rebound).

=========================== DOCUMENTED RULES ===========================

- Universe: small-cap stocks only (bottom market-cap decile) -- the
  roadmap candidate profile's own asset_class is explicitly "Single-stock
  equities (small-cap concentrated)", unlike Turn-of-the-Month's
  universe-wide application.
- Window: the first few trading days of January -- Keim's own finding
  concentrates roughly half the annual small-firm premium in the first
  five trading days of the month, with the single largest abnormal
  return on the very first trading day.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(see swing_research/published_research_analyst.py's TURN_OF_YEAR record
for the full disclosed reasoning behind each)

1. ENTRY DAY = the LAST trading day of DECEMBER (January 1st itself is
   never a trading day) -- same "enter at the Close immediately
   preceding the window you want exposure to" convention Turn-of-the-
   Month already uses for its own month-end entry, so the position is
   open for the first trading day of January's own move.
2. HOLD = exactly JANUARY_EFFECT_HOLD_TRADING_DAYS (5) trading-day ROWS
   after entry (row-position shift, same precedent as Turn-of-the-Month's
   short, weekend-sensitive horizon), NOT the generic
   HOLDING_PERIOD_CALENDAR_DAYS approximation this program's longer-hold
   strategies use. 5 trading days is sized to Keim's own "roughly half
   the annual premium in the first five trading days" finding, within
   the roadmap candidate profile's own holding_days_min=1/
   holding_days_max=10 bounds -- NOT tuned against any backtest result
   (none has been run).
3. SMALL-CAP FILTER: bottom market-cap decile (percentile <= 10),
   IDENTICAL cut, IDENTICAL compute_market_cap_percentile_ranks()
   function, and IDENTICAL current-snapshot shares-outstanding caveat as
   Size Premium / Banz -- see that strategy's module docstring for the
   full disclosed reasoning (shares outstanding is a CURRENT snapshot,
   not a historical series, an unchanged limitation inherited here).
4. NO DIVERSIFICATION HASH FIX (unlike Turn-of-the-Month): the
   bottom-decile market-cap filter already narrows the universe to
   roughly 10% of symbols before the shared engine's own
   max_units_total=10 cap applies -- the same natural narrowing Size
   Premium / Banz already relies on -- so Turn-of-the-Month's own
   alphabetical-collapse problem (documented in its module docstring)
   does not reappear here in the same form.
5. TRANSFERABILITY (disclosed, not corrected for): the mechanism is tied
   to the US tax year (calendar year, Dec 31 loss-selling deadline);
   India's capital-gains tax year ends in March, not December, so this
   test asks whether a US-calendar-specific effect also shows up in NSE
   small-caps, not whether an analogous India-fiscal-year-end effect
   exists. Per direction ("do not deviate from the published mechanism
   to make it perform better"), the published January window is
   implemented as published, not shifted to India's fiscal year-end --
   shifting it would no longer be testing Keim's own documented finding.
6. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL.
7. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.
8. min_lookback_days = 1 -- no formation window (market cap is
   point-in-time, exactly as Size Premium / Banz), same reasoning.

Entry fires on the LAST trading day of December for any symbol whose
market-cap percentile is in the bottom decile that day -- a once-a-year
event by construction (expected_trade_frequency = "Very low"), exactly
as the roadmap candidate profile describes.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

JANUARY_EFFECT_HOLD_TRADING_DAYS = 5   # Keim's own "roughly half the annual premium in the first 5 trading days"
SMALL_CAP_PERCENTILE_THRESHOLD = 10.0  # bottom decile -- same cut as Size Premium / Banz
STOP_LOSS_PCT = 0.08


class TurnOfYearStrategy(Strategy):
    name = "turn_of_year"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 1

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        df["date"] = df.index.date

        calendar_year = df.index.to_period("Y")
        last_trading_day_per_year = df.groupby(calendar_year).apply(lambda g: g.index.max())
        df["is_year_end_entry_day"] = df.index.isin(last_trading_day_per_year.values)

        # Exit day = exactly JANUARY_EFFECT_HOLD_TRADING_DAYS trading-day ROWS
        # after an is_year_end_entry_day row -- a row-position shift, not a
        # calendar-day approximation, so weekends/holidays never distort the
        # intended 5-trading-day holding period. NaN-safe: shift() naturally
        # leaves the last few rows of the dataset False (nothing to compare).
        df["is_january_exit_day"] = df["is_year_end_entry_day"].shift(
            JANUARY_EFFECT_HOLD_TRADING_DAYS).fillna(False)

        # market_cap_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_market_cap_percentile_ranks() and
        # run_turn_of_year_experiment() in research_director.py. If genuinely
        # absent (e.g. a unit test not exercising the cross-sectional
        # wiring), treat as "not small-cap" rather than crashing -- same
        # "absent column means don't qualify" convention as Size Premium /
        # Banz's own missing-column fallback.
        if "market_cap_percentile" not in df.columns:
            df["market_cap_percentile"] = float("nan")
        is_small_cap = df["market_cap_percentile"] <= SMALL_CAP_PERCENTILE_THRESHOLD
        df["qualifies_for_entry"] = df["is_year_end_entry_day"] & is_small_cap.fillna(False)

        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.qualifies_for_entry):
            return None
        entry_price = float(row.Close)
        stop_loss = entry_price * (1 - STOP_LOSS_PCT)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            confidence=float(100.0 - row.market_cap_percentile),
            strategy_name=self.name,
            reason=(f"Last trading day of December, small-cap (market-cap percentile "
                    f"{row.market_cap_percentile:.1f}) -- entering the turn-of-the-year window"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if bool(row.is_january_exit_day):
            return float(row.Close)
        return None
