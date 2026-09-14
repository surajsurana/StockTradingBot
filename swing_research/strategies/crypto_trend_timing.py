"""
Crypto Trend Timing (Faber's 10-month moving-average rule) -- the second
crypto-lane candidate (Pool E, 2026-09-13), chosen to satisfy the rules
recorded after EXP-082: a TIME-SERIES rule on the majors with turnover
measured in trades per year, not per week, judged post-tax.

Source: Faber, M.T. (2007), "A Quantitative Approach to Tactical Asset
Allocation," The Journal of Wealth Management 9(4), 69-79 (updated 2013,
SSRN 962461): for each asset class, at each month-end, hold the asset if
its price is above its 10-month simple moving average, otherwise hold
cash. Across US stocks, foreign stocks, bonds, commodities and REITs
1973-2012 the rule kept buy-and-hold's return while cutting drawdown by
roughly half; it trades only 3-4 times a year per asset. Supporting:
Hurst, Ooi & Pedersen (2017), "A Century of Evidence on Trend-Following
Investing," Journal of Portfolio Management 44(1) -- trend rules on
long horizons work across every asset class for 137 years. Mechanism:
slow reaction to information and herding produce trends; a long
moving-average filter captures the trend while sidestepping the deepest
drawdowns.

=========================== DOCUMENTED RULES ===========================

- Decisions ONLY at month-end, on month-end prices.
- Buy when the month-end price is above the 10-month SMA of month-end
  prices; sell (to cash) when it is below.
- Equal allocation across the assets timed; each asset is either fully
  held or fully in cash. No stop-loss, no target.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. ASSETS = the five largest coins by market cap (BTC, ETH, BNB, XRP,
   SOL), each an equal 20% sleeve. Faber timed asset CLASSES; treating
   the majors as the "classes" of crypto is the reading closest to the
   paper. Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN.
2. MONTH-END = the last calendar day of the month in UTC (crypto trades
   every day); the SMA is of the 10 prior month-end closes INCLUDING the
   current one, exactly as Faber computes it. Estimated impact: NEGLIGIBLE.
   WARM-UP (added after EXP-083, 2026-09-13): the SMA is computed on the
   full history and handed to each walk-forward window as an extra
   column, so a window is not blind for its first ten months -- EXP-083
   showed the windowed cold start removed 28% of every 3-year window,
   including the 2023-24 up-leg from the out-of-sample window. Same
   mechanism as the cross-sectional ranks every other strategy receives;
   no rule changed. Estimated impact: MODERATE, in the direction of MORE
   trading time per window (both up- and down-legs).
3. PROTECTIVE STOP 20% below entry, NOT IN THE SOURCE -- this program
   sizes every position from an entry-to-stop distance, and 20% is the
   crypto-scaled equivalent of the equity books' 8% (see
   crypto_xs_momentum.py, assumption 4). A stopped-out coin is re-entered
   at the next month-end if still above its SMA. Estimated impact:
   MODERATE, one-directional (Faber's own rule would ride a >20% dip if
   the month-end close stayed above the SMA).
4. POSITION SIZING: risk_pct_per_unit=0.04 against the 20% stop = 20% of
   the book per coin = Faber's equal 1/5 sleeve. Estimated impact: MINOR.
5. LONG ONLY: identical to the source (Faber's rule is long-or-cash).
6. BOOK IN USDT, fractional quantities, costs and India's 31.2%
   no-offset tax applied before the audit -- swing_research/crypto_costs.py.
   Estimated impact: MAJOR by construction; far smaller than EXP-082's
   because turnover is ~3-4 round trips per coin per year.

Rebalance frequency: monthly (last UTC calendar day of each month).
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

SMA_MONTHS = 10
STOP_LOSS_PCT = 0.20
CRYPTO_MAJORS = ["BTC", "ETH", "BNB", "XRP", "SOL"]


def compute_month_end_sma(price_history: pd.DataFrame, months: int = SMA_MONTHS) -> pd.DataFrame:
    """Adds is_month_end (the bar dated the last calendar day of a month;
    a trailing partial month is NOT flagged, so a data or walk-forward
    window ending mid-month makes no decision on its last bar) and
    sma_month_end (the 10-month SMA of month-end closes, on month-end rows
    only). If the frame already carries sma_month_end -- the runner
    computes it on FULL history and passes it as an extra column, so a
    walk-forward window's first ten months are not blind (see
    swing_research/crypto_lane.py: run_crypto_trend_timing_experiment)
    -- it is kept as is."""
    df = price_history.sort_index().copy()
    df["is_month_end"] = (df.index + pd.Timedelta(days=1)).month != df.index.month
    if "sma_month_end" not in df.columns:   # else: warm-up column supplied from full history (see runner)
        month_end_close = df.loc[df["is_month_end"], "Close"]
        df["sma_month_end"] = month_end_close.rolling(months).mean().reindex(df.index)
    return df


class CryptoTrendTimingStrategy(Strategy):
    name = "crypto_trend_timing"
    max_units = 1
    risk_pct_per_unit = 0.04
    fractional_quantities = True
    min_lookback_days = 305   # ten month-ends

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = compute_month_end_sma(price_history)
        df["above_sma"] = df["is_month_end"] & (df["Close"] > df["sma_month_end"])
        df["below_sma"] = df["is_month_end"] & df["sma_month_end"].notna() & (df["Close"] < df["sma_month_end"])
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.above_sma):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(entry_price / float(row.sma_month_end) - 1) if row.sma_month_end else 1.0,
            strategy_name=self.name,
            reason=f"month-end close {entry_price:.2f} above its {SMA_MONTHS}-month SMA {float(row.sma_month_end):.2f}",
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if bool(row.below_sma):
            return float(row.Close)
        return None
