"""
Cross-Sectional Momentum -- tests whether stocks with the highest returns
over a J-month formation period continue to outperform over a subsequent
K-month holding period, ranked cross-sectionally against the universe.

Source: Jegadeesh, N. and Titman, S. (1993), "Returns to Buying Winners
and Selling Losers: Implications for Stock Market Efficiency," *The
Journal of Finance*, Vol. 48, No. 1. The foundational cross-sectional
momentum paper -- the origin of the "momentum" anomaly itself, and the
paper 52-Week High Momentum's own source (George & Hwang 2004) explicitly
built on and partly subsumed.

=========================== DOCUMENTED RULES ===========================

- Formation-period return: cumulative return over the prior J months
  (the paper tests J = 3, 6, 9, 12).
- Cross-sectional decile sort by formation-period return at each
  formation date.
- Long the top decile (past winners); the paper's zero-cost portfolio
  shorts the bottom decile (past losers).
- Holding period K months (also tested at K=3,6,9,12); J=6, K=6 is the
  specification the paper highlights as generating the strongest, most-
  cited result.
- Standard overlapping-portfolio construction: a new K-month portfolio
  formed every month, K simultaneous vintages held at once.
- The paper notes a 1-week skip between formation and holding as a
  refinement that avoids some short-term bid-ask/reversal contamination
  -- NOT part of the headline J=6/K=6 result.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(approved 2026-08-04 -- deliberately mirrors 52-Week High Momentum's own
approved precedent wherever the underlying methodological gap is
identical -- see the approved implementation plan for the full NSE-
adaptation table)

1. LONG ONLY. Same reason as every prior strategy in this program -- NSE
   cash equities lack the SLB infrastructure for a genuine multi-month
   short.
   Estimated impact: DIRECTIONALLY UNKNOWN -- omits the short side
   entirely, doesn't bias the long side's own measured performance.
2. J = 6 MONTHS (126 TRADING DAYS) FORMATION, the paper's own most-cited
   specification.
   Estimated impact: MINOR -- a direct restatement of the documented
   headline specification, not a deviation.
3. SINGLE-VINTAGE HOLDING, not the paper's overlapping-portfolio
   construction: enters on the state-transition day a symbol's formation-
   return percentile first crosses >=90 (top decile), holds ONE position
   per symbol for up to 126 trading days (K=6 months), no monthly
   rebalancing into new overlapping vintages.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- IDENTICAL
   reasoning to 52-Week High Momentum's own single-vintage deviation (see
   swing_research/strategies/fifty_two_week_high_momentum.py's module
   docstring): a single-vintage system realizes ONE entry point's path
   per qualifying episode, with materially higher variance than the
   paper's smoothed multi-vintage average.
4. NO SKIP PERIOD between formation and holding. The paper's own headline
   J=6/K=6 result does not require one -- it's noted as a refinement, not
   the primary specification. Adding one not in the primary spec would be
   an invented refinement, not a faithful replication of the paper's main
   finding.
   Estimated impact: MINOR-to-MODERATE, DIRECTIONALLY UNKNOWN -- the
   paper suggests a skip period reduces short-term reversal
   contamination, so omitting it could very slightly understate net
   momentum performance, but this is explicitly a secondary refinement in
   the source material, not the headline result being tested.
5. EXIT RULE: ONLY a 126-trading-day time-stop (the direct single-vintage
   analogue of the paper's K=6-month holding period) OR the synthetic
   protective stop below, whichever comes first. Deliberately NO
   percentile-based early exit -- same discipline established for
   52-Week High Momentum, avoiding a hybrid of the paper plus an invented
   exit rule.
   Estimated impact: N/A for the time-stop itself (direct analogue of the
   documented holding period).
6. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy in this program. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL
   -- the source paper is a factor-return study with zero position-level
   risk management, required to run this through this program's
   position-based backtesting engine at all.
   Estimated impact: MODERATE, one-directional -- can only reduce maximum
   single-position loss relative to "no stop at all," never increase it.
7. RANK THRESHOLD: top decile = formation-return percentile >= 90 (a
   direct restatement of "top decile" into this program's existing 0-100
   percentile convention, already used by Minervini's RS percentile and
   52-Week High Momentum's nearness percentile).
   Estimated impact: MINOR, a direct restatement of the documented rule.
8. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit) -- not documented in the source (a
   factor portfolio has no per-position sizing rule), needed for the same
   reason as the protective stop.
   Estimated impact: MINOR -- standard convention already used elsewhere
   in this program.

Rebalance frequency: formation-return percentile is computed DAILY (the
natural output of the vectorized cross-sectional rank,
swing_research/cross_sectional.py's compute_momentum_percentile_ranks()),
but entries only fire on the qualifying state-transition day, not every
day a symbol happens to already be in the top decile.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

MOMENTUM_FORMATION_DAYS = 126           # J=6 months
MOMENTUM_PERCENTILE_THRESHOLD = 90.0    # top decile
HOLDING_PERIOD_DAYS = 126               # K=6 months
# See fifty_two_week_high_momentum.py's identical convention/rationale:
# exit_signal_at() only receives the current row and OpenPosition's own
# entry_date (a calendar date), no shared trading-day bar index -- the
# 126-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class CrossSectionalMomentumStrategy(Strategy):
    name = "cross_sectional_momentum"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = MOMENTUM_FORMATION_DAYS  # needs the full 126-day formation window

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # momentum_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_momentum_percentile_ranks(). If genuinely absent (e.g. a
        # unit test not exercising the cross-sectional wiring), treat as
        # "not in the top decile" rather than crashing.
        if "momentum_percentile" not in df.columns:
            df["momentum_percentile"] = float("nan")

        df["qualifies"] = df["momentum_percentile"] >= MOMENTUM_PERCENTILE_THRESHOLD
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
            reason=(f"6-month formation return entered the top decile today "
                    f"(percentile {row.momentum_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
