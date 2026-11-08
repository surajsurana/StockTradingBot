"""
Jensen's Alpha Selection (Nifty Alpha 50 / Nifty200 Alpha 30 methodology) --
tests whether stocks with the highest regression-intercept risk-adjusted
outperformance continue to outperform, using the same underlying signal
NSE Indices Limited publishes for its own live Nifty Alpha 50 / Nifty200
Alpha 30 Indices.

Source: Nifty Alpha 50 / Nifty200 Alpha 30 Index Methodology (NSE Indices
Limited, official methodology document) -- see
swing_research.research_roadmap's own CandidateProfile
(key="nifty_alpha_jensens") for the full evidence_sufficiency_note on this
being a product/institutional methodology, not a peer-reviewed academic
paper.

=========================== DOCUMENTED RULES ===========================

- Jensen's Alpha: the INTERCEPT of a CAPM-style regression of each stock's
  returns against the market (Nifty), i.e. risk-adjusted outperformance
  NOT explained by market beta.
- Universe: Nifty 100/200 constituents.
- Selects (and weights) the top stocks by Jensen's Alpha.
- Semi-annual reconstitution.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(mirrors betting_against_beta.py's and nifty_momentum_30_style.py's own
approved adaptation pattern for the identical underlying reasons -- this
program trades individual position rules, not a cap-weighted index-fund
portfolio. See swing_research/published_research_analyst.py's
JENSENS_ALPHA record for the full disclosed reasoning behind each.)

1. PLAIN (UNSHRUNK) OLS REGRESSION, not Betting Against Beta's (SW-009,
   REJECT) shrink-toward-1.0 beta estimator -- selecting on the regression's
   INTERCEPT, as this methodology documents, is a different economic claim
   from BAB's SLOPE-based selection, so it is estimated with a plain
   single-factor OLS regression, not BAB's paper-specific shrinkage.
   Estimated impact: N/A -- a direct restatement of this methodology's own
   construction, not a deviation borrowed from BAB.
2. REGRESSION LOOKBACK = 1 YEAR (252 trading days), using PLAIN (non-
   overlapping) daily returns -- same structural reason as Betting Against
   Beta's own approved lookback shortening (this program's frozen 3-year
   recent-period check cannot absorb a multi-year warm-up with any days
   left to trade), resolved the SAME way here for the SAME reason, per the
   roadmap candidate's own known_weaknesses note that this tension "would
   need to be resolved again here before implementation, not assumed
   solved." See swing_research/cross_sectional.py's
   NIFTY_ALPHA_JENSENS_LOOKBACK_DAYS for the full comment.
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- a 1-year regression
   window is noisier/more regime-reactive than a multi-year estimate would
   be.
3. RAW DAILY RETURNS, not returns in excess of a risk-free rate, for the
   regression -- no risk-free-rate time series is integrated anywhere in
   this platform (identical disclosed simplification to BAB's own
   assumption #4).
   Estimated impact: NEGLIGIBLE at daily frequency.
4. LONG ONLY. No NSE SLB infrastructure for a genuine short (same reason as
   every prior strategy in this program), and the source is itself a
   long-only index by construction.
   Estimated impact: N/A -- a direct restatement of the source's own
   long-only construction.
5. SINGLE-VINTAGE HOLDING, not the index's semi-annual whole-portfolio
   reconstitution: enters on the state-transition day a symbol's Jensen's
   Alpha percentile first crosses >=90 (top decile, standing in for the
   index's own "top 30-50 of 100-200" selection), holds ONE position per
   symbol for up to 126 trading days (approximately the index's own
   6-month semi-annual rebalance cadence), no portfolio-level
   reconstitution. Same convention as nifty_momentum_30_style.py.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- same
   reasoning as every prior cross-sectional strategy's own single-vintage
   deviation.
6. RANK THRESHOLD: top decile = Jensen's Alpha percentile >= 90, restated
   into this program's existing 0-100 percentile convention, same cutoff
   as nifty_momentum_30_style.py's own "top 30 of Nifty 200" stand-in.
   Estimated impact: MINOR-to-MODERATE -- a slightly tighter cutoff than
   the index's own ~top-15-30%, erring toward the stronger-signal side of
   the same underlying score.
7. EXIT RULE: ONLY a 126-trading-day time-stop (standing in for the
   semi-annual reconstitution) OR the synthetic protective stop below,
   whichever comes first. Deliberately NO percentile-based early exit --
   same discipline established for every prior cross-sectional strategy in
   this program.
8. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the index has
   no position-level risk management, only portfolio construction/
   weighting rules.
9. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.
10. UNIVERSE: whatever universe this experiment is run against (not
    necessarily the exact live Nifty 100/200 constituent list) -- same
    simplification every other ported cross-sectional strategy in this
    program already discloses.

Rebalance frequency: the Jensen's Alpha percentile is computed DAILY, but
entries only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import NIFTY_ALPHA_JENSENS_LOOKBACK_DAYS

NIFTY_ALPHA_JENSENS_PERCENTILE_THRESHOLD = 90.0   # top decile (highest Jensen's Alpha)
HOLDING_PERIOD_DAYS = 126                   # ~6 months, the index's own semi-annual cadence
# Same convention as nifty_momentum_30_style.py / betting_against_beta.py:
# exit_signal_at() only receives the current row and OpenPosition's own
# entry_date (a calendar date), no shared trading-day bar index -- the
# 126-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class NiftyAlphaJensensStrategy(Strategy):
    name = "nifty_alpha_jensens"
    max_units = 1                     # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = NIFTY_ALPHA_JENSENS_LOOKBACK_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # nifty_alpha_jensens_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_nifty_alpha_jensens_percentile_ranks(). If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "nifty_alpha_jensens_percentile" not in df.columns:
            df["nifty_alpha_jensens_percentile"] = float("nan")

        df["qualifies"] = df["nifty_alpha_jensens_percentile"] >= NIFTY_ALPHA_JENSENS_PERCENTILE_THRESHOLD
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
            confidence=float(row.nifty_alpha_jensens_percentile),
            strategy_name=self.name,
            reason=(f"Jensen's Alpha entered the top decile today "
                    f"(percentile {row.nifty_alpha_jensens_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
