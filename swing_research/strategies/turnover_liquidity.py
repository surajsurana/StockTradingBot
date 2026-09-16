"""
Turnover / Liquidity Anomaly -- tests whether NSE stocks that trade the
SMALLEST fraction of their own shares outstanding (least "turned over",
hardest to accumulate or exit in size) subsequently OUTPERFORM, long-only.
A second, independent operationalization of the same broad liquidity-
premium family already tested via Amihud Illiquidity (SW-010, price-impact
based) -- this one uses raw share turnover instead, the paper's own
alternative liquidity proxy.

Source: Datar, V.T., Naik, N.Y. and Radcliffe, R. (1998), "Liquidity and
Stock Returns: An Alternative Test," Journal of Financial Markets, Vol. 1,
No. 2.

=========================== DOCUMENTED RULES ===========================

- Turnover_i = trailing average(daily Volume / shares outstanding) -- the
  paper's own liquidity proxy, lower turnover = less liquid.
- Cross-sectional decile sort by Turnover at each formation date.
- Long the BOTTOM decile (lowest turnover, most illiquid) -- the paper's
  documented finding is an inverse relationship between turnover and
  subsequent return.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(see swing_research/published_research_analyst.py's TURNOVER_LIQUIDITY
record for the full disclosed reasoning behind each)

1. LONG ONLY. Same reason as every prior strategy.
2. FORMATION WINDOW = 21 trading days (~1 month), reusing this program's
   own established cross-sectional default (see swing_research/
   cross_sectional.py's TURNOVER_FORMATION_DAYS) rather than a paper-
   specific window, since the paper's exact formation period was not
   independently re-verified beyond its existence for this implementation.
3. SHARES OUTSTANDING = a CURRENT snapshot (data/fetch_shares_outstanding.py),
   not a historical series -- applied across the whole backtest. The
   central disclosed approximation of this strategy: mild for a large,
   stable Nifty 500 constituent, more material for anything with a big
   past split, bonus issue, buyback or follow-on dilution.
4. SINGLE-VINTAGE HOLDING: enters on the state-transition day a symbol's
   turnover percentile first drops to <=10 (bottom decile), holds ONE
   position per symbol for up to 21 trading days (1 month, matching the
   paper's own monthly-rebalance construction), same pattern as every
   prior cross-sectional strategy in this program.
5. EXIT RULE: ONLY the 21-trading-day time-stop OR the synthetic
   protective stop below. No percentile-based early exit.
6. PROTECTIVE STOP-LOSS: 8% below entry. NOT PART OF THE ORIGINAL
   METHODOLOGY AT ALL.
7. RANK THRESHOLD: bottom decile = turnover percentile <=10 (least
   liquid).
8. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.

Rebalance frequency: turnover percentile is computed DAILY, but entries
only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import TURNOVER_FORMATION_DAYS

TURNOVER_PERCENTILE_THRESHOLD = 10.0   # bottom decile (least liquid)
HOLDING_PERIOD_DAYS = 21               # 1 month
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class TurnoverLiquidityStrategy(Strategy):
    name = "turnover_liquidity"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = TURNOVER_FORMATION_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # turnover_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_turnover_percentile_ranks(). If genuinely absent (e.g. a
        # unit test not exercising the cross-sectional wiring), treat as
        # "not in the bottom decile" rather than crashing.
        if "turnover_percentile" not in df.columns:
            df["turnover_percentile"] = float("nan")

        df["qualifies"] = df["turnover_percentile"] <= TURNOVER_PERCENTILE_THRESHOLD
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date

        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev):
            return None  # either indicators not ready yet, or already qualifying yesterday (not a transition)
        if not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        stop_loss = entry_price * (1 - STOP_LOSS_PCT)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            confidence=float(100.0 - row.turnover_percentile),
            strategy_name=self.name,
            reason=(f"Trailing 21-day average turnover entered the bottom decile today "
                    f"(percentile {row.turnover_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
