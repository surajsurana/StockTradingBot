"""
Crypto Volatility-Managed Exposure -- the fourth crypto-lane candidate
(Pool E, 2026-09-14) and the first NON-trend rule in the lane: it is
always long, and only the SIZE of the position changes.

Source: Moreira, A. and Muir, T. (2017), "Volatility-Managed Portfolios,"
The Journal of Finance 72(4), 1611-1644: scaling a portfolio's exposure
by the inverse of its previous month's realized variance (less exposure
when volatility was high, more when it was low) raises Sharpe ratios and
alphas for the market and most factor portfolios, because volatility is
persistent month to month while expected returns are not -- so the
risk-return trade-off is worse right after volatile months. Applied to
crypto by practitioners (and in later academic work on Bitcoin) with
the same finding: volatility clusters strongly in coins.

=========================== DOCUMENTED RULES ===========================

- Each month, weight on the asset = c / (previous month's realized
  variance of daily returns); c is a constant that sets the overall
  scale (MM choose it so the managed series has the same unconditional
  volatility as the unmanaged one). Rebalanced monthly. Long only by
  construction (the weight is never negative).

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. ASSETS = BTC, ETH, BNB, XRP, SOL, each a 20% sleeve scaled by its own
   weight -- the same coins as SW-020/SW-021 so results are comparable.
2. CASH-CONSTRAINED, NO LEVERAGE: weight = min(1, (TARGET_VOL / sigma)^2)
   with sigma = previous month's realized daily volatility annualised
   (sqrt(365)) and TARGET_VOL = 60%: the weight reaches 1 (the full
   sleeve) only when a coin's realized volatility drops to 60% a year
   and is smaller above it. 60% is a disclosed choice (roughly BTC's
   long-run volatility); MM's c is a variance-matching constant that has
   no cash-constrained analogue. Estimated impact: MODERATE, one-
   directional -- a lower target means more cash, less return AND less
   drawdown; the ranking of months (which is the paper's mechanism) does
   not depend on it.
3. MONTHLY REBALANCE = exit at every month-end close and re-enter the
   same bar at the new weight (the engine cannot resize an open
   position). That books a taxable gain every profitable month and pays
   fees monthly -- exactly the paper's turnover, and the reason this
   candidate sits at the lane's turnover limit. Estimated impact: MAJOR
   for costs and tax; NONE for the signal. Weights within 10% of the
   current one are left alone (no exit/re-entry) to avoid paying fees
   for a rounding change. Estimated impact: MINOR.
4. PROTECTIVE STOP 20% below entry, NOT IN THE SOURCE (lane convention).
5. SIZING: risk_pct_per_unit = 0.04 against the 20% stop = the 20%
   sleeve, multiplied by the weight via Signal.size_multiplier -- an
   engine capability added for this rule (default 1.0 everywhere else).
6. COSTS AND TAX per crypto_costs.py; the verdict is post-tax.

Rebalance frequency: monthly (last UTC calendar day), position size
only; the coin is always held at some weight unless its weight falls
below MIN_WEIGHT.
"""

from typing import Optional

import numpy as np
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

TARGET_VOL = 0.60         # annualised; weight = min(1, (TARGET_VOL / sigma)^2)
VOL_LOOKBACK_DAYS = 30    # "previous month" of daily returns
MIN_WEIGHT = 0.10         # below this the sleeve is not worth the fees
REBALANCE_BAND = 0.10     # re-enter only if the weight moved by more than this (relative)
STOP_LOSS_PCT = 0.20


def compute_vol_weight(price_history: pd.DataFrame, target_vol: float = TARGET_VOL,
                       lookback_days: int = VOL_LOOKBACK_DAYS) -> pd.DataFrame:
    """Adds is_month_end and vol_weight = min(1, (target/sigma)^2) where
    sigma is the trailing `lookback_days` realized daily log-return
    volatility, annualised with sqrt(365). Keeps a supplied vol_weight
    column (warm-up from full history)."""
    df = price_history.sort_index().copy()
    df["is_month_end"] = (df.index + pd.Timedelta(days=1)).month != df.index.month
    if "vol_weight" not in df.columns:
        r = np.log(df["Close"]).diff()
        sigma = r.rolling(lookback_days).std() * np.sqrt(365)
        df["vol_weight"] = np.minimum(1.0, (target_vol / sigma) ** 2)
    return df


class CryptoVolManagedStrategy(Strategy):
    name = "crypto_vol_managed"
    max_units = 1
    risk_pct_per_unit = 0.04
    fractional_quantities = True
    min_lookback_days = VOL_LOOKBACK_DAYS + 1

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = compute_vol_weight(price_history)
        df["decision_day"] = df["is_month_end"] & df["vol_weight"].notna()
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.decision_day) or float(row.vol_weight) < MIN_WEIGHT:
            return None
        entry_price = float(row.Close)
        w = float(row.vol_weight)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=w, strategy_name=self.name, size_multiplier=w,
            reason=f"month-end: previous-month volatility gives weight {w:.2f} of the sleeve",
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if not bool(row.decision_day):
            return None
        w = float(row.vol_weight)
        if w < MIN_WEIGHT:
            return float(row.Close)
        entry_w = float(getattr(open_position, "size_multiplier", 0.0) or 0.0)
        if entry_w > 0 and abs(w / entry_w - 1) <= REBALANCE_BAND:
            return None                       # weight barely moved: hold, no fees
        return float(row.Close)               # rebalance: exit here, re-enter this bar at the new weight
