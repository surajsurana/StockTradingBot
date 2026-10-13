"""
Cryptocurrency Idiosyncratic Volatility Factor -- the crypto-lane
cross-sectional risk-based strategy (2026-10-12): the first crypto
candidate that ranks coins by a RISK measure (residual, market-model-
adjusted volatility) rather than by price-return momentum/reversal/trend.

Source: Zhang, W. and Li, Y., "Is idiosyncratic volatility priced in
cryptocurrency markets?", Research in International Business and Finance,
Vol. 54 (2020), Elsevier.

=========================== DOCUMENTED RULES ===========================

- For each coin, regress daily returns on a crypto-market-factor proxy
  (a Fama-MacBeth-style market model); the regression RESIDUAL's
  volatility is that coin's idiosyncratic volatility (IVOL), net of
  common crypto-market risk.
- Cross-sectionally sort coins by IVOL. The paper finds a POSITIVE
  IVOL-return relationship in crypto -- the OPPOSITE sign from the
  well-known equity-market "IVOL puzzle" (this program's own
  idiosyncratic_volatility.py, which longs the LOWEST-idio-vol decile).
- Monthly portfolio sorts and rebalancing (the paper's own Fama-MacBeth
  regression cadence).

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY, the TOP decile (HIGHEST idiosyncratic volatility) -- the
   paper's own documented sign for crypto, a disclosed reduction from its
   long-short portfolio-sort construction, same convention as every other
   candidate in this program.
2. SINGLE-FACTOR (CAPM/market-model) RESIDUAL VOLATILITY, the same
   closed-form sigma_coin x sqrt(1 - rho^2) identity already established
   for the equity lane's own idiosyncratic_volatility.py (for a
   single-regressor OLS, Var(residual) = Var(Y) x (1 - rho^2)) -- the
   regression machinery this candidate's own mechanism field says to
   reuse (already built for Betting Against Beta / the equity
   Idiosyncratic Volatility Anomaly).
3. CRYPTO-MARKET-FACTOR PROXY: an equal-weighted index built from this
   experiment's own universe (swing_research/cross_sectional.py's
   compute_crypto_market_factor_close()) -- this platform has no
   separately-fetched "crypto market index" data source, unlike the
   equity lane's fetched Nifty 50 Close series. See that function's own
   docstring for the disclosed self-weighting this implies.
   Estimated impact: MINOR, symmetric across the cross-section.
4. IVOL FORMATION = 30 CALENDAR DAYS (~1 month), not the equity lane's
   21 TRADING days (swing_research/cross_sectional.py's
   CRYPTO_IVOL_FORMATION_DAYS) -- crypto trades every day, so 21 bars
   would be ~3 weeks, not a full month; 30 is the same "every day is a
   bar" conversion this lane's own CRYPTO_ILLIQ_FORMATION_DAYS (252->365)
   already establishes. Estimated impact: MINOR.
5. MONTHLY CROSS-SECTIONAL REFORMATION (last UTC calendar day of each
   month), matching the paper's own monthly Fama-MacBeth cadence and this
   lane's own crypto_illiquidity_premium.py / crypto_tsmom.py monthly
   reformation. A coin still in the top decile at the next month-end is
   re-entered the same day (exits are processed before entries), which
   IS a monthly rebalance -- identical convention to those strategies.
   Estimated impact: MINOR.
6. TOP DECILE = IVOL percentile >= 90, this program's standard
   cross-sectional threshold, matching this candidate's own disclosed
   direction field.
7. HOLDING PERIOD: a position closes only at the FIRST month-end on or
   after 25 calendar days have elapsed (HOLDING_PERIOD_DAYS=25, this
   candidate's own disclosed holding_days_min) -- every month has at
   least 28 days, so this always resolves to "the next month-end," never
   an early or doubled-up exit. Estimated impact: NEGLIGIBLE.
8. PROTECTIVE STOP 20% below entry, NOT IN THE SOURCE -- this lane's own
   crypto_xs_momentum.py / crypto_illiquidity_premium.py / crypto_tsmom.py
   already disclose why an equity-scaled 8% stop is inappropriate for
   crypto's much higher daily volatility and use 20% instead. Reused here
   rather than inventing a third number. Estimated impact: MODERATE,
   one-directional.
9. POSITION SIZING: risk_pct_per_unit=0.025 against the 20% stop =
   12.5% of the book per coin -- identical sizing convention to
   crypto_xs_momentum.py / crypto_illiquidity_premium.py (same universe,
   same decile-scale qualifying count), not a new number invented for
   this strategy.
10. BOOK IN USDT, fractional quantities, Binance daily OHLCV -- same
    lane-wide convention as every other crypto strategy.
11. COSTS AND TAX (swing_research/crypto_costs.py): applied by
    crypto_lane.py's run_crypto_experiment_generic() exactly as for every
    other crypto strategy. This Strategy class itself produces ordinary
    pre-cost, pre-tax Signals.

Rebalance frequency: IVOL percentile is computed daily (via
swing_research.cross_sectional.compute_crypto_idiosyncratic_volatility_
percentile_ranks), but entries and holding-period exits only fire on
month-end bars.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import CRYPTO_IVOL_FORMATION_DAYS

IVOL_PERCENTILE_THRESHOLD = 90.0   # top decile (highest idiosyncratic volatility)
HOLDING_PERIOD_DAYS = 25            # this candidate's own disclosed holding_days_min; combined
                                      # with the is_month_end gate below, always resolves to "the
                                      # next month-end" (every month has >=28 days)
STOP_LOSS_PCT = 0.20                 # crypto-scaled stop, same convention as every other
                                      # strategy in this lane -- see module docstring, point 8


class CryptoIdiosyncraticVolatilityStrategy(Strategy):
    name = "crypto_idiosyncratic_volatility"
    max_units = 1
    risk_pct_per_unit = 0.025
    fractional_quantities = True
    min_lookback_days = CRYPTO_IVOL_FORMATION_DAYS + 1   # the 30-day IVOL formation window, see
                                                           # module docstring point 4

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # crypto_ivol_percentile is injected by the caller (crypto_lane.py,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_crypto_idiosyncratic_volatility_percentile_ranks(). If
        # genuinely absent (e.g. a unit test not exercising the
        # cross-sectional wiring), treat as "not in the top decile"
        # rather than crashing.
        if "crypto_ivol_percentile" not in df.columns:
            df["crypto_ivol_percentile"] = float("nan")

        df["is_month_end"] = (df.index + pd.Timedelta(days=1)).month != df.index.month
        df["qualifies"] = df["is_month_end"] & (df["crypto_ivol_percentile"] >= IVOL_PERCENTILE_THRESHOLD)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price,
            stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.crypto_ivol_percentile), strategy_name=self.name,
            reason=(f"Trailing 30-day idiosyncratic volatility in the top decile "
                    f"(percentile {row.crypto_ivol_percentile:.1f}) at month-end"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if (bool(row.is_month_end) and entry_date is not None
                and (row.date - entry_date).days >= HOLDING_PERIOD_DAYS):
            return float(row.Close)
        return None
