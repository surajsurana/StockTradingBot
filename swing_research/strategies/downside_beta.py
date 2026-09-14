"""
Downside Beta -- the next roadmap candidate (2026-09-14), after the two
multi-year-holding candidates above it (Long-Term Reversal, Sehgal's
Indian contrarian) were ruled out per direction on 2026-09-06 for a
3-5 year holding period that does not fit this program's cadence.

Source: Ang, A., Chen, J. and Xing, Y. (2006), "Downside Risk," The
Review of Financial Studies 19(4), 1191-1239. Downside beta is the
stock's beta to the market computed ONLY on days the market falls below
its average return:

    beta_minus = cov(r_i, r_m | r_m < mu_m) / var(r_m | r_m < mu_m)

estimated from DAILY returns over the past 12 months. Stocks with high
downside beta earn a premium of roughly 6% per year over low-downside-
beta stocks (their Table 2-3; quintile portfolios formed monthly, held
one month, equal-weighted), and the premium is not explained by regular
beta, size, book-to-market, momentum or coskewness. Mechanism:
investors who care about downside losses (disappointment aversion, Gul
1991) demand compensation for stocks that fall MORE when the market
falls -- a risk premium, not a mispricing.

NOTE ON DIRECTION: the roadmap profile (swing_research/research_roadmap.py)
listed this candidate as "long-only bottom-decile (lowest downside
beta)". That is the defensive reading, not the paper's finding -- the
premium accrues to HIGH downside beta. This implementation follows the
source: long the TOP quintile. The roadmap text was corrected the same
day, and the mismatch is disclosed here so the record shows it.

=========================== DOCUMENTED RULES ===========================

- Each month, estimate beta_minus from the past 12 months of daily
  returns (market days below the period's mean market return).
- Sort into quintiles; the top quintile (highest beta_minus) earns the
  premium. Hold one month; re-form monthly. Equal weights.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY, the high-beta_minus quintile only (the paper's spread is
   long-short). Estimated impact: DIRECTIONALLY UNKNOWN, standard.
2. TOP QUINTILE = cross-sectional percentile >= 80 among Nifty 500
   names with a valid 252-day estimate. The paper uses quintiles, so
   this program's usual decile convention is NOT applied. Estimated
   impact: NEGLIGIBLE.
3. DAILY LOG RETURNS, no risk-free subtraction (daily T-bill yield is
   ~0.02%, inside rounding). Market = Nifty 50 (^NSEI), aligned to each
   stock's own trading days and forward-filled. Estimated impact: NEGLIGIBLE.
4. THE DOWNSIDE CONDITION uses each 252-day window's OWN mean market
   return (exact per window, computed with sliding windows), not a fixed
   zero threshold. Estimated impact: NEGLIGIBLE (mean daily return is a
   few bps).
5. STATE-TRANSITION ENTRY: a stock is bought at the close of the first
   day it enters the top quintile (was not in it the day before), held
   21 trading days (30 calendar days), then sold at the close -- this
   program's single-vintage convention (max_effect, high_volume_return_
   premium) in place of the paper's month-end portfolio formation.
   Estimated impact: MINOR, DIRECTIONALLY UNKNOWN (entries spread across
   the month instead of clustering at month-end).
6. AT MOST 10 POSITIONS, ranked by confidence (the percentile itself),
   8% protective stop and 1% risk-per-unit sizing -- none in the source,
   all standard here. Estimated impact: MODERATE, one-directional for
   the stop (high-downside-beta stocks are exactly the ones that gap).
7. WARM-UP: the estimate needs 252 trading days, so the experiment
   runner fetches one extra year of history and starts the walk-forward
   judgement one year after the data start (swing_research/
   research_director.run_downside_beta_experiment). Estimated impact:
   NONE on rules; removes the blind first window EXP-083 suffered.

Rebalance frequency: monthly per position (single vintage, 21 trading
days); the percentile is recomputed daily.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

DOWNSIDE_BETA_PERCENTILE_THRESHOLD = 80.0   # top quintile = highest downside beta (the paper's premium side)
HOLDING_PERIOD_DAYS = 21                    # one month
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class DownsideBetaStrategy(Strategy):
    name = "downside_beta"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 253   # 252 daily returns

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        if "downside_beta_percentile" not in df.columns:
            df["downside_beta_percentile"] = float("nan")
        df["qualifies"] = df["downside_beta_percentile"] >= DOWNSIDE_BETA_PERCENTILE_THRESHOLD
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev) or not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.downside_beta_percentile), strategy_name=self.name,
            reason=(f"Downside beta entered the top quintile today (percentile "
                    f"{row.downside_beta_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
