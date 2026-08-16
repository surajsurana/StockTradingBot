"""
Amihud Illiquidity Premium -- tests whether NSE stocks that are HARDEST to
trade (highest price-impact-per-rupee-traded) subsequently OUTPERFORM,
long-only. First strategy in this program selecting on TRADING-COST/
LIQUIDITY RISK, distinct from every prior price-pattern strategy and from
Betting Against Beta's (SW-009, REJECT) systematic-risk-covariance signal.

Source: Amihud, Y. (2002), "Illiquidity and Stock Returns: Cross-Section
and Time-Series Effects," Journal of Financial Markets, Vol. 5, No. 1.

=========================== DOCUMENTED RULES ===========================

- ILLIQ_i = mean(|daily return| / daily rupee volume) over a formation
  period -- the paper's own headline measure uses the PRIOR YEAR.
- Cross-sectional decile sort by ILLIQ at each formation date.
- Long the TOP decile (most illiquid) -- the paper's own long-side
  framing; the underlying test is a return-predictability regression, not
  a literal decile-sort trading rule (translating it into one is itself
  an interpretive step, per the follow-on tradeable-portfolio literature).

===================== IMPLEMENTATION ASSUMPTIONS =====================
(approved 2026-08-15/2026-08-16 -- see
swing_research/published_research_analyst.py's AMIHUD_ILLIQUIDITY_PREMIUM
record for the full disclosed reasoning behind each)

1. LONG ONLY. Same reason as every prior strategy.
2. ILLIQ FORMATION = 252 TRADING DAYS (~1 year), the paper's OWN
   preferred window -- UNCHANGED, unlike Betting Against Beta's beta
   lookback, this fits the frozen 3-year recent-period check without
   shortening.
3. RUPEE VOLUME PROXIED AS Close x Volume (no intraday VWAP available --
   standard practice in the literature itself).
4. SINGLE-VINTAGE HOLDING, not a rolling monthly regression: enters on
   the state-transition day a symbol's ILLIQ percentile first rises to
   >=90 (top decile), holds ONE position per symbol for up to 21 trading
   days (1 month, matching the tradeable-portfolio literature's monthly
   reformation cadence).
5. EXIT RULE: ONLY a 21-trading-day time-stop OR the synthetic protective
   stop below. No percentile-based early exit.
6. PROTECTIVE STOP-LOSS: 8% below entry. NOT PART OF THE ORIGINAL
   METHODOLOGY AT ALL.
7. RANK THRESHOLD: top decile = ILLIQ percentile >= 90 (most illiquid).
8. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.
9. EXECUTION-REALISM CONFIGURATION (the central methodological difference
   from every prior strategy): this strategy's ACCEPTANCE VERDICT is
   computed from execution-realism-adjusted trades (5% trailing-20-day-ADV
   position cap, ILLIQ-derived slippage cost calibrated via a disclosed,
   pre-declared anchor, next-day-open fill timing) -- see
   swing_research/execution_realism_engine.py and
   swing_research/research_director.py's run_amihud_experiment(). This
   Strategy class itself is UNAWARE of execution realism -- it produces
   ordinary Signals exactly like every other strategy; the adjustment is
   applied as a post-process on the resulting trades by the experiment
   pipeline, not by this class.

Rebalance frequency: ILLIQ percentile is computed DAILY, but entries only
fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import AMIHUD_ILLIQ_FORMATION_DAYS

ILLIQ_PERCENTILE_THRESHOLD = 90.0   # top decile (most illiquid)
HOLDING_PERIOD_DAYS = 21            # 1 month
# See fifty_two_week_high_momentum.py / short_term_reversal.py's identical
# convention: exit_signal_at() only receives the current row and
# OpenPosition's own entry_date (a calendar date), no shared trading-day
# bar index -- the 21-TRADING-day holding period is checked via an
# equivalent CALENDAR-day threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class AmihudIlliquidityStrategy(Strategy):
    name = "amihud_illiquidity"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = AMIHUD_ILLIQ_FORMATION_DAYS  # needs the full 252-day formation window

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # illiq_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_amihud_illiq_percentile_ranks(). If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "illiq_percentile" not in df.columns:
            df["illiq_percentile"] = float("nan")

        df["qualifies"] = df["illiq_percentile"] >= ILLIQ_PERCENTILE_THRESHOLD
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
            strategy_name=self.name,
            reason=(f"Trailing 252-day ILLIQ entered the top decile today "
                    f"(percentile {row.illiq_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
