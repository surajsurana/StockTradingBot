"""
Size Premium (Small-Cap Effect) -- tests whether NSE stocks with the
SMALLEST market capitalization subsequently OUTPERFORM, long-only. One of
the three foundational asset-pricing anomalies (alongside value and
momentum) that originally undermined pure CAPM, later formalized as the
SMB (Small Minus Big) factor in Fama-French (1992/1993).

Source: Banz, R.W. (1981), "The Relationship Between Return and Market
Value of Common Stocks," Journal of Financial Economics, Vol. 9, No. 1,
3-18.

=========================== DOCUMENTED RULES ===========================

- Market capitalization = price x shares outstanding, at each formation
  date.
- Cross-sectional decile sort by market cap at each formation date.
- Long the BOTTOM decile (smallest market cap) -- the paper's documented
  finding is an inverse relationship between size and subsequent
  risk-adjusted return. The original construction is long-short (small
  minus big); this program reduces it to long-only, the same reduction
  applied to every other factor strategy here.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(see swing_research/published_research_analyst.py's SIZE_PREMIUM_BANZ
record for the full disclosed reasoning behind each)

1. LONG ONLY. Same reason as every prior strategy.
2. SHARES OUTSTANDING = a CURRENT snapshot (data/fetch_shares_outstanding.py),
   not a historical series -- applied across the whole backtest, the same
   disclosed approximation already used by Turnover/Liquidity. Mild for a
   large, stable Nifty 500 constituent, more material for anything with a
   big historical split/bonus issue/buyback/dilution.
3. MARKET CAP IS POINT-IN-TIME (Close x shares outstanding), not a
   trailing average -- faithful to the paper's own annual size-sort at the
   formation date, unlike this program's other cross-sectional signals
   (turnover, momentum, ...) which deliberately average over a formation
   window. No formation window exists for this signal at all.
4. SINGLE-VINTAGE HOLDING: enters on the state-transition day a symbol's
   market-cap percentile first drops to <=10 (bottom decile), holds ONE
   position per symbol for ~1 year (252 trading days / 365 calendar days),
   the paper's own annual-rebalance cadence (Fama-French rebalance their
   size portfolios each June) -- same single-vintage pattern as every
   prior cross-sectional strategy in this program.
5. EXIT RULE: ONLY the 252-trading-day time-stop OR the synthetic
   protective stop below. No percentile-based early exit.
6. PROTECTIVE STOP-LOSS: 8% below entry. NOT PART OF THE ORIGINAL
   METHODOLOGY AT ALL.
7. RANK THRESHOLD: bottom decile = market-cap percentile <=10 (smallest).
8. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.

Rebalance frequency: market-cap percentile is computed DAILY, but entries
only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

SIZE_PERCENTILE_THRESHOLD = 10.0   # bottom decile (smallest market cap)
HOLDING_PERIOD_DAYS = 252          # ~1 year -- the paper's own annual rebalance cadence
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class SizePremiumBanzStrategy(Strategy):
    name = "size_premium_banz"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = 1            # no formation window: market cap is a point-in-time figure, see module docstring

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # market_cap_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_market_cap_percentile_ranks(). If genuinely absent (e.g. a
        # unit test not exercising the cross-sectional wiring), treat as
        # "not in the bottom decile" rather than crashing.
        if "market_cap_percentile" not in df.columns:
            df["market_cap_percentile"] = float("nan")

        df["qualifies"] = df["market_cap_percentile"] <= SIZE_PERCENTILE_THRESHOLD
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
            confidence=float(100.0 - row.market_cap_percentile),
            strategy_name=self.name,
            reason=(f"Market capitalization entered the bottom decile today "
                    f"(percentile {row.market_cap_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
