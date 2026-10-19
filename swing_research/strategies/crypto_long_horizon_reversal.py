"""
Cryptocurrency Long-Horizon Reversal -- this program's first crypto-lane
REVERSAL strategy (every other crypto strategy so far is either short-horizon
momentum/trend-following or volatility-scaling, never reversal).

Source: Dobrynskaya, V. (2023), "Cryptocurrency Momentum and Reversal," The
Journal of Alternative Investments, Vol. 26, No. 1, 65-76; corroborated by
Ficura, M. and Colak, G. (2023), "Impact of Size and Volume on Cryptocurrency
Momentum and Reversal," SSRN working paper.

=========================== DOCUMENTED RULES ===========================

- Momentum dominates at SHORT horizons (up to 2-4 weeks); a significant
  REVERSAL appears once the formation/holding horizon extends beyond
  roughly one month -- past LOSERS over these longer windows subsequently
  outperform. Ficura & Colak corroborate a related horizon-dependent
  switch, also modulated by coin size and volume.
- Cross-sectional sort by formation-period return; long-short construction
  goes long past winners at short horizons, long past LOSERS at the
  longer (>1 month) horizon this strategy implements.
- The papers test sort/hold horizons from 1 week to 2 years; the reversal
  effect is reported strongest in roughly the 1-3 month band.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY (past LOSERS over the formation window) -- same disclosed
   long-only reduction of the papers' long-short construction as every
   other strategy in this program. Estimated impact: DIRECTIONALLY UNKNOWN.
2. FORMATION/HOLDING HORIZON = 60 CALENDAR DAYS (~2 months), this
   module's own conservative midpoint of the candidate's disclosed
   "roughly 1-3 month" strongest-reversal band (research_roadmap.py's
   typical_holding_period field) -- the candidate's own known_weaknesses
   field explicitly flags that the exact switch-point needs confirmation
   against the FULL papers, not assumed from the available abstracts/
   summaries, which this implementation does not have access to. 60 is
   NOT a number taken directly from either paper's own stated rule.
   Estimated impact: MODERATE -- the horizon is this program's own
   disclosed estimate, not a verified paper parameter; a materially
   different true switch-point could change both the signal's quality
   and the holding period below. See swing_research/cross_sectional.py's
   CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS.
3. BOTTOM DECILE = cross-sectional percentile <= 10 (this program's
   standard reversal-polarity convention, matching
   compute_short_term_reversal_percentile_ranks()'s own <=10 threshold).
   Estimated impact: MINOR.
4. MONTHLY CROSS-SECTIONAL REFORMATION (last UTC calendar day of each
   month), the same cadence this lane's own crypto_illiquidity_premium.py
   and crypto_idiosyncratic_volatility.py already use for a formation
   window longer than a single rebalance period. A coin still in the
   bottom decile at the next month-end is re-entered the same day (exits
   are processed before entries), which IS a monthly rebalance -- same
   convention as those two strategies. Estimated impact: MINOR.
5. HOLDING PERIOD: a position closes only at the FIRST month-end on or
   after 55 calendar days have elapsed (HOLDING_PERIOD_DAYS=55). Combined
   with the month-end-only exit gate, this always resolves to "the SECOND
   month-end after entry" (the shortest possible two consecutive calendar
   months is 59 days, e.g. Feb+Mar; the shortest single month is 28 days,
   so 55 is high enough to rule out a 1-month exit and low enough to never
   miss the 2-month one) -- i.e. ~60 calendar days held, matching this
   strategy's own 60-day formation/reversal horizon above. Estimated
   impact: MINOR, by the same reasoning as crypto_illiquidity_premium.py's
   analogous 25-day/"next month-end" choice.
6. PROTECTIVE STOP 20% below entry, NOT IN THE SOURCE -- this lane's own
   crypto_xs_momentum.py/crypto_tsmom.py/crypto_trend_timing*.py/
   crypto_illiquidity_premium.py/crypto_idiosyncratic_volatility.py
   already disclose why an equity-scaled stop is inappropriate for
   crypto's much higher daily volatility and use 20% instead. Reused here
   rather than inventing a new number. Estimated impact: MODERATE,
   one-directional.
7. POSITION SIZING: risk_pct_per_unit=0.025 against the 20% stop = 12.5%
   of the book per coin -- identical sizing convention to every other
   cross-sectional crypto strategy in this lane, not a new number invented
   for this strategy. Estimated impact: MINOR.
8. BOOK IN USDT, fractional quantities, Binance daily OHLCV -- same
   lane-wide convention as every other crypto strategy.
9. COSTS AND TAX (swing_research/crypto_costs.py): applied by
   crypto_lane.py's run_crypto_experiment_generic() exactly as for every
   other crypto strategy. This Strategy class itself produces ordinary
   pre-cost, pre-tax Signals. Estimated impact: MAJOR -- the lane's
   standing question.

Rebalance frequency: the 60-day formation-return percentile is computed
daily (via swing_research.cross_sectional.compute_crypto_long_horizon_
reversal_percentile_ranks), but entries and holding-period exits only
fire on month-end bars.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

BOTTOM_DECILE_PERCENTILE_THRESHOLD = 10.0  # past LOSERS -- see module docstring, point 3
HOLDING_PERIOD_DAYS = 55                   # resolves to "the second month-end after entry" -- point 5
STOP_LOSS_PCT = 0.20                       # crypto-scaled stop, same lane-wide convention -- point 6


class CryptoLongHorizonReversalStrategy(Strategy):
    name = "crypto_long_horizon_reversal"
    max_units = 1
    risk_pct_per_unit = 0.025
    fractional_quantities = True
    min_lookback_days = 61   # the 60-day formation window + 1, see cross_sectional.py's
                              # CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # crypto_reversal_percentile is injected by the caller (crypto_lane.py, via
        # simulate_portfolio()'s extra_columns_by_symbol) BEFORE precompute() runs -- see
        # swing_research/cross_sectional.py's compute_crypto_long_horizon_reversal_percentile_ranks().
        # If genuinely absent (e.g. a unit test not exercising the cross-sectional wiring), treat
        # as "not in the bottom decile" rather than crashing.
        if "crypto_reversal_percentile" not in df.columns:
            df["crypto_reversal_percentile"] = float("nan")

        df["is_month_end"] = (df.index + pd.Timedelta(days=1)).month != df.index.month
        df["qualifies"] = df["is_month_end"] & (df["crypto_reversal_percentile"] <= BOTTOM_DECILE_PERCENTILE_THRESHOLD)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price,
            stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.crypto_reversal_percentile), strategy_name=self.name,
            reason=(f"Trailing 60-calendar-day return in the bottom decile "
                    f"(percentile {row.crypto_reversal_percentile:.1f}, a past loser) at month-end"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if (bool(row.is_month_end) and entry_date is not None
                and (row.date - entry_date).days >= HOLDING_PERIOD_DAYS):
            return float(row.Close)
        return None
