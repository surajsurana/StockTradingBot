"""
Crypto Cross-Sectional Momentum -- the first crypto strategy in this
program (Pool E lane, 2026-09-13), and the first judged NET of Indian
crypto tax.

Source: Liu, Y., Tsyvinski, A. and Wu, X. (2022), "Common Risk Factors in
Cryptocurrency," The Journal of Finance 77(2): a three-factor model
(market, size, momentum) prices the cross-section of coins; the momentum
factor sorts coins on past one- to four-week returns and the three-week
horizon is the strongest, with weekly rebalancing. Earlier: Liu &
Tsyvinski (2021), "Risks and Returns of Cryptocurrency," Review of
Financial Studies -- time-series momentum in the majors at weekly
horizons. Mechanism (their reading): investor attention and momentum
trading; no cash-flow anchor pulls prices back quickly.

=========================== DOCUMENTED RULES ===========================

- Each week, rank coins by their past 3-week return.
- Long the top quintile (the paper's long-short goes short the bottom).
- Hold one week; rebalance weekly.
- Universe: large, liquid coins (the paper filters on market cap and
  price; ours is the 40 largest USDT spot pairs, data/fetch_crypto.py).

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY -- Indian spot exchanges, no shorting; same disclosure as
   every strategy in this program. Estimated impact: DIRECTIONALLY UNKNOWN.
2. REBALANCE DAY = Monday (UTC), the first bar after the week's close;
   entries fire only on Mondays, exits on the following Monday's close
   (7 calendar days). A coin still in the top quintile is re-entered the
   same day (the engine processes exits before entries), which IS a
   weekly rebalance. Estimated impact: MINOR.
3. TOP QUINTILE = cross-sectional percentile >= 80 (this program's
   standard convention). Estimated impact: MINOR.
4. PROTECTIVE STOP 20% below entry. NOT IN THE SOURCE. The program's
   usual 8% is an equity-volatility number; crypto's daily volatility is
   3-4x higher and an 8% stop would fire most weeks. 20% is the same
   "roughly 2-3 daily standard deviations" distance the 8% is for
   Nifty 500 stocks. Estimated impact: MODERATE, one-directional.
5. POSITION SIZING: 2.5% of capital risked per unit against the 20% stop
   = 12.5% of the book per coin, i.e. ~8 equal positions -- the paper's
   equal-weighted quintile of a 40-coin universe. Estimated impact: MINOR.
6. BOOK IN USDT (1,000 USDT ~ Rs.95,000 on 2026-09-13), prices from
   Binance; shown in rupees at the USD/INR rate. USDT's Indian premium is
   ignored. Quantities are FRACTIONAL (6 decimals) -- the engines' whole-
   share sizing was extended for this (Strategy.fractional_quantities),
   since 12.5% of 1,000 USDT cannot buy a whole BTC. Estimated impact:
   NEGLIGIBLE for the verdict.
7. COSTS AND TAX (swing_research/crypto_costs.py): 0.30%/side fee + 10
   bps/side spread, then India's 31.2% per-profitable-trade tax with no
   loss offset and fees not deductible. The Auditor judges the POST-TAX
   trades; pre-tax numbers are recorded alongside. Estimated impact:
   MAJOR -- this is the question the experiment exists to answer.

Rebalance frequency: weekly (Mondays UTC); the percentile is computed
daily but only read on rebalance days.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

TOP_QUINTILE_PERCENTILE = 80.0
HOLDING_DAYS = 7
STOP_LOSS_PCT = 0.20
REBALANCE_WEEKDAY = 0   # Monday


class CryptoCrossSectionalMomentumStrategy(Strategy):
    name = "crypto_xs_momentum"
    max_units = 1
    risk_pct_per_unit = 0.025
    min_lookback_days = 22
    fractional_quantities = True   # coins are divisible; a 1,000 USDT book cannot hold whole BTC

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        if "crypto_momentum_percentile" not in df.columns:
            df["crypto_momentum_percentile"] = float("nan")
        df["is_rebalance_day"] = pd.Series(df.index.weekday == REBALANCE_WEEKDAY, index=df.index)
        df["qualifies"] = df["is_rebalance_day"] & (df["crypto_momentum_percentile"] >= TOP_QUINTILE_PERCENTILE)
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev) or not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.crypto_momentum_percentile), strategy_name=self.name,
            reason=(f"3-week momentum in the top quintile (percentile {row.crypto_momentum_percentile:.1f}) "
                    f"on the weekly rebalance day"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if bool(row.is_rebalance_day) and entry_date is not None and (row.date - entry_date).days >= HOLDING_DAYS:
            return float(row.Close)
        return None
