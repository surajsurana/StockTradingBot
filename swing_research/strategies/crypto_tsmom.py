"""
Crypto Time-Series Momentum (12-month) -- the third crypto-lane candidate
(Pool F, 2026-09-14), chosen under the rules recorded after EXP-082 (a
time-series rule on the majors, monthly turnover, judged post-tax).

Source: Moskowitz, T.J., Ooi, Y.H. and Pedersen, L.H. (2012), "Time
Series Momentum," Journal of Financial Economics 104(2), 228-250: for
each of 58 futures/forwards, the sign of the past 12-month excess return
predicts the next month's return; a strategy long every asset with a
positive trailing 12-month return and short every asset with a negative
one, rebalanced monthly, earns a large, consistent premium across asset
classes and does best in extreme markets. Crypto evidence: Liu &
Tsyvinski (2021, RFS) document time-series momentum in Bitcoin, and
several later replications find the 12-month sign rule holds for the
large coins. Mechanism (MOP): initial under-reaction and delayed
over-reaction -- the same behavioural story as cross-sectional
momentum, applied to each asset against its own past.

=========================== DOCUMENTED RULES ===========================

- At each month-end, for each asset: trailing 12-month excess return
  > 0 -> long for the next month; < 0 -> short (cash, long-only here).
- Positions scaled to a constant ex-ante volatility (40% annualised),
  so every asset contributes similar risk. Rebalanced monthly.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. ASSETS = BTC, ETH, BNB, XRP, SOL, equal 20% sleeves -- the same five
   Pool F already times with Faber's rule, so the two results are
   directly comparable. Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN.
2. LONG ONLY: a negative signal means cash, never short. Identical to
   the crypto-lane convention; MOP's short leg is dropped.
3. NO VOLATILITY SCALING. This engine sizes every position from its
   entry-to-stop distance, so MOP's 40%/sigma position scaling cannot
   be expressed without turning the stop into a volatility dial (which
   would change what the stop is). Equal sleeves instead. Estimated
   impact: MODERATE -- MOP's scaling mostly equalises risk across very
   different asset classes; among five coins of broadly similar
   volatility the difference is smaller, but real.
4. 12 MONTHS = 365 calendar days of daily bars (crypto trades every
   day); "excess" return without the risk-free rate (negligible at this
   horizon vs coin returns). Month-end = last UTC calendar day; the
   signal is read on that bar. The signal is computed on FULL history
   and handed to each walk-forward window as an extra column (warm-up,
   as for crypto_trend_timing). Estimated impact: NEGLIGIBLE.
5. PROTECTIVE STOP 20% below entry, NOT IN THE SOURCE -- same crypto-
   scaled stop as the other Pool F rules, re-entered at the next month-
   end if the signal is still positive. Estimated impact: MODERATE,
   one-directional.
6. SIZING: risk_pct_per_unit = 0.04 against the 20% stop = 20% sleeve.
7. COSTS AND TAX as for every crypto candidate (crypto_costs.py); the
   verdict is post-tax, pre-tax recorded alongside.

Distinct from crypto_trend_timing (Faber)? Both are trend rules on the
same coins and will often agree; the point of running it is to learn
whether EXP-084's PASS is a property of the trend signal or of the
specific moving-average form -- a robustness question, disclosed as
such rather than sold as diversification.

Rebalance frequency: monthly (last UTC calendar day).
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

TSMOM_LOOKBACK_DAYS = 365
STOP_LOSS_PCT = 0.20


def compute_tsmom_signal(price_history: pd.DataFrame, lookback_days: int = TSMOM_LOOKBACK_DAYS) -> pd.DataFrame:
    """Adds is_month_end (last calendar day of a month; a trailing partial
    month is not flagged) and tsmom_return (trailing lookback_days
    calendar-day return, NaN until enough history). Keeps a supplied
    tsmom_return column (warm-up from full history)."""
    df = price_history.sort_index().copy()
    df["is_month_end"] = (df.index + pd.Timedelta(days=1)).month != df.index.month
    if "tsmom_return" not in df.columns:
        close = df["Close"]
        prior = close.reindex(df.index - pd.Timedelta(days=lookback_days))
        df["tsmom_return"] = close.to_numpy() / prior.to_numpy() - 1
    return df


class CryptoTimeSeriesMomentumStrategy(Strategy):
    name = "crypto_tsmom"
    max_units = 1
    risk_pct_per_unit = 0.04
    fractional_quantities = True
    min_lookback_days = 366

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = compute_tsmom_signal(price_history)
        df["signal_long"] = df["is_month_end"] & (df["tsmom_return"] > 0)
        df["signal_flat"] = df["is_month_end"] & df["tsmom_return"].notna() & (df["tsmom_return"] <= 0)
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.signal_long):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.tsmom_return), strategy_name=self.name,
            reason=f"trailing 12-month return {float(row.tsmom_return) * 100:+.1f}% at month-end",
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if bool(row.signal_flat):
            return float(row.Close)
        return None
