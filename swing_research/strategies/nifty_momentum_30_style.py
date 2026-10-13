"""
Risk-Adjusted Blended Momentum (Nifty200 Momentum 30 methodology) -- tests
whether stocks with the highest risk-adjusted, dual-horizon momentum score
continue to outperform, using the identical scoring construction NSE
Indices Limited publishes for its own live Nifty200 Momentum 30 Index.

Source: Nifty200 Momentum 30 Index Methodology (NSE Indices Limited,
official methodology document, nsearchives.nseindia.com), live since 2019
with multiple AMCs (SBI, HDFC, etc.) running index funds/ETFs tracking it.
A product/institutional methodology, not a peer-reviewed academic paper --
see swing_research.research_roadmap's own CandidateProfile
(key="nifty_momentum_30_style") evidence_sufficiency_note for that
distinction.

=========================== DOCUMENTED RULES ===========================

- A "Normalised Momentum Score" blends the stock's 6-month AND 12-month
  Close-to-Close return, EACH divided by the SAME trailing 12-month
  standard deviation of daily returns (a Sharpe-ratio-like risk
  adjustment) -- NOT two separately-windowed volatilities.
- The two risk-adjusted ratios are each standardised into a cross-
  sectional Z-score, averaged 50%/50% into a "Weighted Average Z Score",
  then passed through an asymmetric transform (1+Z for Z>=0, 1/(1-Z) for
  Z<0) that matters for the index's own free-float-market-cap x score
  PORTFOLIO WEIGHTING.
- Universe: Nifty 200 constituents, with >=1 year of listing history and
  derivatives-segment availability.
- Selects the top 30 stocks by Normalised Momentum Score.
- Semi-annual reconstitution (June/December), with a rank-based buffer to
  reduce turnover.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(mirrors cross_sectional_momentum.py's and fifty_two_week_high_momentum.py's
own approved single-vintage/no-skip-exit adaptation pattern, for the
identical underlying reason -- this program trades individual position
rules, not a cap-weighted index-fund portfolio)

1. LONG ONLY. Same reason as every prior strategy in this program -- NSE
   cash equities lack the SLB infrastructure for a genuine short, and the
   source is itself a long-only index by construction, not a long-short
   factor study.
   Estimated impact: N/A -- a direct restatement of the source's own
   long-only construction, not a deviation.
2. BLENDED RATIO, NOT Z-SCORE-THEN-TRANSFORM. The 6-month and 12-month
   risk-adjusted return ratios are blended 50%/50% directly (see
   swing_research/cross_sectional.py's compute_nifty_momentum_30_score()),
   then cross-sectionally percentile-ranked ONCE -- not each ratio
   Z-scored first, averaged, then passed through the index's own
   asymmetric (1+Z / 1/(1-Z)) transform.
   Estimated impact: MINOR -- the Z-score-then-transform construction
   exists in the source to produce a continuous PORTFOLIO-WEIGHTING
   value; a decile-cutoff entry rule only needs the same relative
   ORDERING, which a direct blend-then-percentile-rank preserves closely
   (both are monotonic transforms of each leg), same discipline already
   applied to compute_rs_score()'s own weighted blend-then-rank
   construction.
3. SINGLE-VINTAGE HOLDING, not the index's semi-annual whole-portfolio
   reconstitution: enters on the state-transition day a symbol's blended
   score percentile first crosses >=90 (top decile, standing in for "top
   30 of 200" -- roughly the same ~15% cutoff), holds ONE position per
   symbol for up to 126 trading days (approximately the index's own
   6-month semi-annual rebalance cadence), no portfolio-level
   reconstitution.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN --
   identical reasoning to Cross-Sectional Momentum's own single-vintage
   deviation: a single-vintage system realizes ONE entry point's path per
   qualifying episode, with materially higher variance than the index's
   smoothed, diversified 30-stock portfolio.
4. RANK THRESHOLD: top decile = blended-score percentile >= 90, standing
   in for the index's own "top 30 of the Nifty 200" selection (~15%),
   restated into this program's existing 0-100 percentile convention
   (already used by every other cross-sectional strategy here).
   Estimated impact: MINOR-to-MODERATE -- 90th percentile is a slightly
   tighter cutoff than the index's own ~top-15%, erring toward the
   stronger-signal side of the same underlying score.
5. EXIT RULE: ONLY a 126-trading-day time-stop (standing in for the
   semi-annual reconstitution) OR the synthetic protective stop below,
   whichever comes first. Deliberately NO percentile-based early exit --
   same discipline established for Cross-Sectional Momentum and 52-Week
   High Momentum.
   Estimated impact: N/A for the time-stop itself (direct analogue of the
   documented semi-annual cadence).
6. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy in this program. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL
   -- the index has no position-level risk management, only portfolio
   construction/weighting rules.
   Estimated impact: MODERATE, one-directional -- can only reduce maximum
   single-position loss relative to "no stop at all," never increase it.
7. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit) -- not documented in the source (an
   index has no per-position sizing rule, only a market-cap x score
   weighting scheme this program's position-based engine has no
   equivalent of).
   Estimated impact: MINOR -- standard convention already used elsewhere
   in this program.
8. UNIVERSE: whatever universe this experiment is run against (not
   necessarily the exact live Nifty 200 constituent list), and no
   derivatives-availability or minimum-listing-history filter -- the same
   simplification every other ported cross-sectional strategy in this
   program already discloses.
   Estimated impact: MINOR-to-MODERATE, DIRECTIONALLY UNKNOWN.

Rebalance frequency: the blended score percentile is computed DAILY (the
natural output of the vectorized cross-sectional rank,
swing_research/cross_sectional.py's compute_nifty_momentum_30_percentile_ranks()),
but entries only fire on the qualifying state-transition day, not every
day a symbol happens to already be in the top decile.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

MOMENTUM_30_PERCENTILE_THRESHOLD = 90.0    # stands in for "top 30 of Nifty 200" (~top 15%)
HOLDING_PERIOD_DAYS = 126                  # ~6 months, the index's own semi-annual cadence
# See cross_sectional_momentum.py's identical convention/rationale:
# exit_signal_at() only receives the current row and OpenPosition's own
# entry_date (a calendar date), no shared trading-day bar index -- the
# 126-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class NiftyMomentum30StyleStrategy(Strategy):
    name = "nifty_momentum_30_style"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = 252          # needs the full 12-month volatility/return window

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # nifty_momentum_30_percentile is injected by the caller
        # (research_director, via simulate_portfolio()'s
        # extra_columns_by_symbol) BEFORE precompute() runs -- see
        # swing_research/cross_sectional.py's
        # compute_nifty_momentum_30_percentile_ranks(). If genuinely
        # absent (e.g. a unit test not exercising the cross-sectional
        # wiring), treat as "not in the top decile" rather than crashing.
        if "nifty_momentum_30_percentile" not in df.columns:
            df["nifty_momentum_30_percentile"] = float("nan")

        df["qualifies"] = df["nifty_momentum_30_percentile"] >= MOMENTUM_30_PERCENTILE_THRESHOLD
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
            confidence=float(row.nifty_momentum_30_percentile),
            strategy_name=self.name,
            reason=(f"Risk-adjusted blended momentum score entered the top decile today "
                    f"(percentile {row.nifty_momentum_30_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
