"""
Cryptocurrency Illiquidity Premium (Amihud-style) -- the crypto-lane
application of this program's own already-tested India-equity Amihud
Illiquidity Premium (SW-010, PASS with a conflicting robustness REJECT),
now backed by independent crypto-specific supporting literature rather
than an untested extrapolation.

Source: Ali, A., Peng, S. and Shams, S., "Unravelling cross-sectional
patterns in cryptocurrencies: a four-factor asset pricing model," China
Accounting and Finance Review, Vol. 27, No. 4, 493 (2025) -- their 'CIHML'
crypto illiquidity factor, built the same way as Amihud (2002)'s original
measure; corroborated by Zhang, W. and Li, Y., "Liquidity risk and
expected cryptocurrency returns," International Journal of Finance &
Economics, Vol. 28 (2023), 472-492.

=========================== DOCUMENTED RULES ===========================

- ILLIQ_i = mean(|daily return| / daily dollar volume) over a formation
  period (Amihud's own headline measure uses the prior year).
- Cross-sectional decile sort by ILLIQ at each formation date.
- Long the TOP decile (most illiquid) -- the disclosed long-only
  reduction of each paper's long-short factor-portfolio construction,
  the same interpretive step already used by this program's equity
  Amihud strategy.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY. Same disclosed reason as every strategy in this program.
2. ILLIQ FORMATION = 365 CALENDAR DAYS, not the equity lane's 252
   TRADING days (swing_research/cross_sectional.py's
   AMIHUD_ILLIQ_FORMATION_DAYS) -- crypto trades every day, so 252 bars
   would be ~8.3 months, not the paper's annual window. 365 is the same
   "every day is a bar, so 12 months = 365 days" convention this lane's
   own crypto_tsmom.py already discloses for its 12-month lookback.
   Estimated impact: MINOR -- a slightly longer formation window than
   the equity strategy's, smoothing the ILLIQ estimate a bit more.
3. DOLLAR VOLUME PROXIED AS Close x Volume (USDT), the exact same
   proxy already established for the equity strategy and reused
   verbatim via execution_realism_engine.compute_trailing_illiq() --
   no new formula, just this strategy's own formation window passed in.
4. MONTHLY CROSS-SECTIONAL REFORMATION (last UTC calendar day of each
   month), not the equity strategy's any-day state-transition entry --
   this is the standard crypto cross-sectional factor-sort cadence this
   lane's own crypto_xs_momentum.py and crypto_tsmom.py already use
   (weekly and monthly respectively); a monthly academic factor-sort is
   the natural match for an annual-formation signal. A coin still in
   the top decile at the next month-end is re-entered the same day
   (exits are processed before entries), which IS a monthly rebalance --
   identical convention to crypto_xs_momentum.py's weekly one. Estimated
   impact: MINOR -- Amihud's paper itself re-estimates its cross-section
   monthly despite each ILLIQ value looking back a full year.
5. TOP DECILE = ILLIQ percentile >= 90, matching the equity strategy's
   own threshold and this candidate's own disclosed direction field.
6. HOLDING PERIOD: a position closes only at the FIRST month-end on or
   after 25 calendar days have elapsed (HOLDING_PERIOD_DAYS=25,
   this candidate's own disclosed holding_days_min) -- every month has
   at least 28 days, so this always resolves to "the next month-end,"
   never an early or doubled-up exit. Estimated impact: NEGLIGIBLE.
7. PROTECTIVE STOP 20% below entry, NOT IN THE SOURCE -- the equity
   strategy's 8% is an EQUITY-volatility number; this lane's own
   crypto_xs_momentum.py / crypto_tsmom.py / crypto_trend_timing*.py
   already disclose why an equity-scaled stop is inappropriate for
   crypto's much higher daily volatility and use 20% instead. Reused
   here rather than inventing a third number. Estimated impact:
   MODERATE, one-directional.
8. POSITION SIZING: risk_pct_per_unit=0.025 against the 20% stop =
   12.5% of the book per coin -- identical sizing convention to
   crypto_xs_momentum.py (same universe, same decile-scale qualifying
   count), not a new number invented for this strategy.
9. BOOK IN USDT, fractional quantities, Binance daily OHLCV -- same
   lane-wide convention as every other crypto strategy.
10. COSTS AND TAX (swing_research/crypto_costs.py): applied by
    crypto_lane.py's run_crypto_experiment_generic() exactly as for
    every other crypto strategy. This Strategy class itself produces
    ordinary pre-cost, pre-tax Signals.

Rebalance frequency: ILLIQ percentile is computed daily (via
swing_research.cross_sectional.compute_crypto_illiq_percentile_ranks),
but entries and holding-period exits only fire on month-end bars.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

ILLIQ_PERCENTILE_THRESHOLD = 90.0   # top decile (most illiquid)
HOLDING_PERIOD_DAYS = 25            # this candidate's own disclosed holding_days_min;
                                      # combined with the is_month_end gate below, always
                                      # resolves to "the next month-end" (every month has >=28 days)
STOP_LOSS_PCT = 0.20                 # crypto-scaled stop, same convention as every other
                                      # strategy in this lane -- see module docstring, point 7


class CryptoIlliquidityPremiumStrategy(Strategy):
    name = "crypto_illiquidity_premium"
    max_units = 1
    risk_pct_per_unit = 0.025
    fractional_quantities = True
    min_lookback_days = 366   # the 365-day ILLIQ formation window, see module docstring point 2

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # crypto_illiq_percentile is injected by the caller (crypto_lane.py,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_crypto_illiq_percentile_ranks(). If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "crypto_illiq_percentile" not in df.columns:
            df["crypto_illiq_percentile"] = float("nan")

        df["is_month_end"] = (df.index + pd.Timedelta(days=1)).month != df.index.month
        df["qualifies"] = df["is_month_end"] & (df["crypto_illiq_percentile"] >= ILLIQ_PERCENTILE_THRESHOLD)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price,
            stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.crypto_illiq_percentile), strategy_name=self.name,
            reason=(f"Trailing 365-day Amihud ILLIQ in the top decile "
                    f"(percentile {row.crypto_illiq_percentile:.1f}) at month-end"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if (bool(row.is_month_end) and entry_date is not None
                and (row.date - entry_date).days >= HOLDING_PERIOD_DAYS):
            return float(row.Close)
        return None
