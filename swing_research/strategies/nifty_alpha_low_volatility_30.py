"""
Combined Alpha + Low-Volatility Screen (Nifty Alpha Low-Volatility 30
methodology) -- a real, separately-published NSE index combining the
Jensen's Alpha selection (nifty_alpha_jensens, already implemented in this
program) with a low-volatility screen/weighting overlay (nifty_low_volatility_30,
already implemented as realized_low_volatility.py) into one combined score.

Source: Nifty Alpha Low-Volatility 30 Index Methodology (NSE Indices
Limited, launched 2017) -- see swing_research.research_roadmap's own
CandidateProfile (key="nifty_alpha_low_volatility_30") for the full
evidence_sufficiency_note on this being a product/institutional
methodology, not a peer-reviewed academic paper, and for why its
diversification value against this program's already-tested history is
the lowest of its batch (it touches BOTH the Alpha-family and the
volatility/beta-family tags already present).

=========================== DOCUMENTED RULES ===========================

- Combines the Alpha selection (Jensen's Alpha, the INTERCEPT of a
  CAPM-style regression of each stock's returns against the market) with
  a low-volatility screen (standard deviation of daily returns over the
  last one year) into one combined score.
- Universe: Nifty 100 constituents (the index's own documented universe).
- Selects (and weights) the top stocks by the combined score.
- Semi-annual reconstitution.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(mirrors nifty_alpha_jensens.py's and realized_low_volatility.py's own
approved adaptation pattern for the identical underlying reasons -- this
program trades individual position rules, not a cap-weighted index-fund
portfolio. See swing_research/published_research_analyst.py's
NIFTY_ALPHA_LOW_VOLATILITY_30 record for the full disclosed reasoning
behind each.)

1. COMBINATION METHOD: each leg (Jensen's Alpha, negated realized
   volatility) is percentile-ranked cross-sectionally FIRST, the two
   percentiles are averaged 50/50, and the average is percentile-ranked
   ONE more time -- see swing_research/cross_sectional.py's
   compute_nifty_alpha_low_volatility_30_percentile_ranks() for the full
   disclosed reasoning (a percentile-rank stand-in for the official
   methodology's cross-sectional Z-score-then-sum, since this program has
   no Z-score convention and the two legs' raw units are not comparable).
   Estimated impact: MINOR-to-MODERATE, DIRECTIONALLY UNKNOWN.
2. BOTH LEGS REUSE THEIR OWN STRATEGY'S EXISTING LOOKBACK (252 trading
   days each, NIFTY_ALPHA_JENSENS_LOOKBACK_DAYS / REALIZED_VOL_LOOKBACK_DAYS)
   -- not re-derived here, same structural reason each already discloses
   (this program's frozen 3-year recent-period check cannot absorb a
   multi-year warm-up with any days left to trade). Estimated impact:
   N/A -- a direct reuse of each leg's own already-approved lookback.
3. RAW DAILY RETURNS for both legs, not returns in excess of a risk-free
   rate -- no risk-free-rate time series is integrated anywhere in this
   platform (identical disclosed simplification to every prior strategy
   needing a market-model regression). Estimated impact: NEGLIGIBLE at
   daily frequency.
4. LONG ONLY. No NSE SLB infrastructure for a genuine short, and the
   source is itself a long-only index by construction. Estimated impact:
   N/A -- a direct restatement of the source's own long-only construction.
5. SINGLE-VINTAGE HOLDING, not the index's semi-annual whole-portfolio
   reconstitution: enters on the state-transition day a symbol's combined
   percentile first crosses >=90 (top decile), holds ONE position per
   symbol for up to 126 trading days (the index's own 6-month semi-annual
   rebalance cadence), no portfolio-level reconstitution. Same convention
   as nifty_alpha_jensens.py / realized_low_volatility.py.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- same
   reasoning as every prior cross-sectional strategy's own single-vintage
   deviation.
6. RANK THRESHOLD: top decile = combined percentile >= 90, restated into
   this program's existing 0-100 percentile convention, same cutoff as
   nifty_alpha_jensens.py's own top-decile threshold.
   Estimated impact: MINOR-to-MODERATE -- a slightly tighter cutoff than
   the index's own ~top-30-of-100 selection, erring toward the
   stronger-signal side of the same underlying score.
7. EXIT RULE: ONLY a 126-trading-day time-stop (standing in for the
   semi-annual reconstitution) OR the synthetic protective stop below,
   whichever comes first. Deliberately NO percentile-based early exit --
   same discipline established for every prior cross-sectional strategy.
8. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the index has
   no position-level risk management, only portfolio construction/
   weighting rules.
9. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.
10. UNIVERSE: whatever universe this experiment is run against (not
    necessarily the exact live Nifty 100 constituent list) -- same
    simplification every other ported cross-sectional strategy in this
    program already discloses.

Rebalance frequency: the combined percentile is computed DAILY, but
entries only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import (
    NIFTY_ALPHA_JENSENS_LOOKBACK_DAYS, REALIZED_VOL_LOOKBACK_DAYS,
)

NIFTY_ALPHA_LOW_VOLATILITY_30_PERCENTILE_THRESHOLD = 90.0   # top decile (highest combined score)
HOLDING_PERIOD_DAYS = 126                   # ~6 months, the index's own semi-annual cadence
# Same convention as nifty_alpha_jensens.py / realized_low_volatility.py:
# exit_signal_at() only receives the current row and OpenPosition's own
# entry_date (a calendar date), no shared trading-day bar index -- the
# 126-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08
# The strategy needs both legs' rolling windows full -- same (max of the
# two, they're equal) lookback each leg already discloses on its own.
MIN_LOOKBACK_DAYS = max(NIFTY_ALPHA_JENSENS_LOOKBACK_DAYS, REALIZED_VOL_LOOKBACK_DAYS) + 1


class NiftyAlphaLowVolatility30Strategy(Strategy):
    name = "nifty_alpha_low_volatility_30"
    max_units = 1                     # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = MIN_LOOKBACK_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # nifty_alpha_low_volatility_30_percentile is injected by the caller
        # (research_director, via simulate_portfolio()'s extra_columns_by_symbol)
        # BEFORE precompute() runs -- see swing_research/cross_sectional.py's
        # compute_nifty_alpha_low_volatility_30_percentile_ranks(). If genuinely
        # absent (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "nifty_alpha_low_volatility_30_percentile" not in df.columns:
            df["nifty_alpha_low_volatility_30_percentile"] = float("nan")

        df["qualifies"] = (
            df["nifty_alpha_low_volatility_30_percentile"] >= NIFTY_ALPHA_LOW_VOLATILITY_30_PERCENTILE_THRESHOLD
        )
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
            confidence=float(row.nifty_alpha_low_volatility_30_percentile),
            strategy_name=self.name,
            reason=(f"Combined Alpha + Low-Volatility score entered the top decile today "
                    f"(percentile {row.nifty_alpha_low_volatility_30_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
