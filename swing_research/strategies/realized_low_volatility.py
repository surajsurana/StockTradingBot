"""
Realized Low Volatility -- the Nifty100 Low Volatility 30 index
methodology applied as a strategy (roadmap key nifty_low_volatility_30,
score 7.45; researched 2026-09-14 as the next equity candidate after
the two multi-year-holding entries were ruled out by direction).

Source: NSE Indices Limited, "Nifty100 Low Volatility 30 Index
Methodology" (index launched 2016, base date 2005): from the Nifty 100,
select the 30 stocks with the lowest volatility, where volatility is the
standard deviation of daily price returns (log-normal) over the last one
year; weight each by the inverse of its volatility; reconstitute
semi-annually (with a quarterly rebalance of weights). Academic root of
the effect: Ang, Hodrick, Xing & Zhang (2006) and Baker, Bradley &
Wurgler (2011), "Benchmarks as Limits to Arbitrage" -- low-volatility
stocks earn returns comparable to or above high-volatility stocks with
far lower risk, because benchmark-constrained institutions overpay for
volatile "lottery" names and cannot lever up the calm ones.

=========================== DOCUMENTED RULES ===========================

- Universe: Nifty 100 constituents.
- Volatility = standard deviation of daily log returns over 1 year.
- Select the 30 lowest-volatility stocks; weight by inverse volatility.
- Reconstitute semi-annually.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. UNIVERSE = this program's frozen Nifty 500 universe, not the Nifty
   100 (no point-in-time Nifty 100 membership is held here). The bottom
   decile of a 457-stock universe by volatility (~45 names, of which at
   most 10 are held) stands in for "30 of 100". Estimated impact:
   MODERATE, DIRECTIONALLY UNKNOWN -- the Nifty 500 tail includes calmer
   mid-caps the index would never hold.
2. SELECTION = cross-sectional percentile <= 10 (lowest realized
   volatility), ranked by lowest volatility first (Signal.confidence =
   100 - percentile) for the 10 slots. Estimated impact: MINOR.
3. NO INVERSE-VOLATILITY WEIGHTING: this engine sizes from the stop
   distance, so all 10 positions carry equal risk (with an 8% stop that
   is equal notional). Estimated impact: MINOR -- within the calmest
   decile the volatilities are close, so inverse-vol weights are near
   equal anyway.
4. HOLDING = 126 trading days (183 calendar days, one reconstitution
   period), single vintage: a stock is bought at the close of the first
   day it enters the bottom decile and sold at the close 126 trading
   days later; it re-qualifies only by re-entering the decile. The
   index's quarterly weight rebalance is not reproduced (see 3).
   Estimated impact: MINOR.
5. 8% PROTECTIVE STOP and 1% risk-per-unit sizing, at most 10 positions
   -- this program's conventions, not in the methodology. Estimated
   impact: MODERATE, one-directional for the stop (though a low-vol
   name is the least likely to hit an 8% stop inside six months).
6. WARM-UP: the estimate needs 252 trading days, so the runner fetches
   one extra year and starts the walk-forward judgement one year after
   the data start (as for downside_beta). Estimated impact: NONE on the
   rules.

Distinct from Idiosyncratic Volatility (SW-012, INCONCLUSIVE): that
strategy ranks on RESIDUAL volatility after a market-model regression
and holds one month; this ranks on TOTAL volatility, no regression, and
holds six months. Same risk-based family as Betting Against Beta
(SW-009, REJECT) -- disclosed as a close cousin, not an independent test.

Rebalance frequency: semi-annual per position; the percentile is
recomputed daily.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

LOW_VOL_PERCENTILE_THRESHOLD = 10.0   # bottom decile = lowest realized volatility
HOLDING_PERIOD_DAYS = 126             # one semi-annual reconstitution period
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class RealizedLowVolatilityStrategy(Strategy):
    name = "nifty_low_volatility_30"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 253

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        if "realized_vol_percentile" not in df.columns:
            df["realized_vol_percentile"] = float("nan")
        df["qualifies"] = df["realized_vol_percentile"] <= LOW_VOL_PERCENTILE_THRESHOLD
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev) or not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=100.0 - float(row.realized_vol_percentile), strategy_name=self.name,
            reason=(f"1-year realized volatility entered the lowest decile today "
                    f"(percentile {row.realized_vol_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
