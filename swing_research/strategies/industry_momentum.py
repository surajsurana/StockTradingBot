"""
Industry Momentum -- tests whether INDUSTRIES with the strongest trailing
returns continue to outperform, bought via their constituent stocks,
rather than ranking individual stocks by their own trailing return.

Source: Moskowitz, T.J. and Grinblatt, M. (1999), "Do Industries Explain
Momentum?", The Journal of Finance, Vol. 54, No. 4. The paper's central
finding is that a large share of the well-documented individual-stock
momentum effect (Jegadeesh & Titman 1993, already implemented in this
program as Cross-Sectional Momentum) is attributable to momentum AT THE
INDUSTRY LEVEL -- buying stocks in recently-strong industries captures
most of the same premium, with different turnover/risk characteristics
than picking individual past winners.

=========================== DOCUMENTED RULES ===========================

- Formation-period return: for EACH INDUSTRY, the industry portfolio's
  cumulative return over the prior J months (the paper tests J = 1, 6,
  12; J=6 is the specification shared with this program's existing
  Cross-Sectional Momentum headline result).
- Cross-sectional decile sort of INDUSTRIES (not individual stocks) by
  formation-period return at each formation date.
- Long the stocks belonging to the top-decile industries (past winning
  industries); the paper's zero-cost portfolio shorts stocks in the
  bottom-decile industries.
- Holding period K months (K=6, matching the headline J=6/K=6
  specification already used for Cross-Sectional Momentum).
- Standard overlapping-portfolio construction: a new K-month portfolio
  formed every month, K simultaneous vintages held at once.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(mirrors Cross-Sectional Momentum's own approved precedent wherever the
underlying methodological gap is identical -- see that strategy's module
docstring, swing_research/strategies/cross_sectional_momentum.py, for the
original reasoning this implementation reuses verbatim)

1. LONG ONLY. Same reason as every prior strategy in this program -- NSE
   cash equities lack the SLB infrastructure for a genuine multi-month
   short.
   Estimated impact: DIRECTIONALLY UNKNOWN -- omits the short side
   entirely, doesn't bias the long side's own measured performance.
2. J = 6 MONTHS (126 TRADING DAYS) FORMATION, the same headline
   specification already used for this program's Cross-Sectional
   Momentum, so the two strategies are directly comparable on formation
   horizon alone.
   Estimated impact: MINOR -- a direct restatement of a documented,
   tested specification (J=6 is one of the paper's own tested horizons),
   not a deviation.
3. EQUAL-WEIGHTED (not value/cap-weighted) INDUSTRY PORTFOLIOS. The
   paper's own industry portfolios are value-weighted; this program has
   no reliable point-in-time market-cap series (see Size Premium / Banz's
   own disclosed point-in-time-market-cap difficulty) to build a cap-
   weighted industry return from, so each industry's formation return is
   instead an equal-weighted average of its constituent stocks' own J=6-
   month formation returns -- see
   swing_research/cross_sectional.py's compute_industry_momentum_percentile_ranks().
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- equal-weighting
   over-weights small/illiquid constituents relative to the paper's cap-
   weighted construction; could move an industry's measured momentum in
   either direction depending on whether its largest or smallest members
   led that industry's recent move.
4. COARSE NSE-SECTOR MAP, NOT FAMA-FRENCH INDUSTRIES. Industry
   membership is this program's existing sector_map (research_lab's
   load_sector_map(), the SAME mapping already used for Turtle's
   correlation-group position caps) -- a disclosed adaptation, coarser
   than the Fama-French-style industry classification the original paper
   uses. Stocks with no sector-map entry are excluded from every
   industry's average AND never qualify for entry themselves (treated as
   "not in a ranked industry", not silently defaulted into one).
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- a coarser
   grouping mixes sub-industries with different momentum profiles into
   one bucket, which could dilute or occasionally invert a true finer-
   grained industry signal.
5. SINGLE-VINTAGE HOLDING, not the paper's overlapping-portfolio
   construction: enters on the state-transition day a symbol's INDUSTRY's
   formation-return percentile first crosses >=90 (top decile), holds ONE
   position per symbol for up to 126 trading days (K=6 months), no
   monthly rebalancing into new overlapping vintages. Identical
   structural adaptation to Cross-Sectional Momentum and 52-Week High
   Momentum, approved for the identical underlying reason.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN --
   identical reasoning to those two strategies' own single-vintage
   deviation: a single-vintage system realizes ONE entry point's path
   per qualifying episode, with materially higher variance than the
   paper's smoothed multi-vintage average.
6. NO SKIP PERIOD between formation and holding, same discipline
   established for Cross-Sectional Momentum -- not part of this paper's
   own headline specification either.
   Estimated impact: MINOR-to-MODERATE, DIRECTIONALLY UNKNOWN -- mirrors
   Cross-Sectional Momentum's own identical assumption and impact
   estimate.
7. EXIT RULE: ONLY a 126-trading-day time-stop (the direct single-vintage
   analogue of the paper's K=6-month holding period) OR the synthetic
   protective stop below, whichever comes first. Deliberately NO
   percentile-based early exit -- same discipline established for Cross-
   Sectional Momentum and 52-Week High Momentum.
   Estimated impact: N/A for the time-stop itself (direct analogue of the
   documented holding period).
8. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy in this program. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL
   -- the source paper is a factor-return study with zero position-level
   risk management, required to run this through this program's
   position-based backtesting engine at all.
   Estimated impact: MODERATE, one-directional -- can only reduce maximum
   single-position loss relative to "no stop at all," never increase it.
9. RANK THRESHOLD: top decile = industry formation-return percentile
   >= 90 (a direct restatement of "top decile" into this program's
   existing 0-100 percentile convention, already used by every other
   cross-sectional strategy in this program).
   Estimated impact: MINOR, a direct restatement of the documented rule.
10. POSITION SIZING: reuses this program's standard risk_pct_per_unit
    convention (1% of equity per unit) -- not documented in the source (a
    factor portfolio has no per-position sizing rule), needed for the
    same reason as the protective stop.
    Estimated impact: MINOR -- standard convention already used elsewhere
    in this program.

Rebalance frequency: each industry's formation-return percentile is
computed DAILY (the natural output of the vectorized cross-sectional
rank, swing_research/cross_sectional.py's
compute_industry_momentum_percentile_ranks()), but entries only fire on
the qualifying state-transition day for a given STOCK (i.e. the day its
own industry's percentile first crosses the threshold), not every day a
stock happens to already sit in a top-decile industry.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

INDUSTRY_MOMENTUM_FORMATION_DAYS = 126       # J=6 months
INDUSTRY_MOMENTUM_PERCENTILE_THRESHOLD = 90.0  # top decile of INDUSTRIES
HOLDING_PERIOD_DAYS = 126                     # K=6 months
# See cross_sectional_momentum.py's identical convention/rationale:
# exit_signal_at() only receives the current row and OpenPosition's own
# entry_date (a calendar date), no shared trading-day bar index -- the
# 126-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class IndustryMomentumStrategy(Strategy):
    name = "industry_momentum"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = INDUSTRY_MOMENTUM_FORMATION_DAYS  # needs the full 126-day formation window

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # industry_momentum_percentile is injected by the caller
        # (research_director, via simulate_portfolio()'s
        # extra_columns_by_symbol) BEFORE precompute() runs -- see
        # swing_research/cross_sectional.py's
        # compute_industry_momentum_percentile_ranks(). If genuinely
        # absent (e.g. a unit test not exercising the cross-sectional
        # wiring, or a symbol with no sector-map entry), treat as "not in
        # a top-decile industry" rather than crashing.
        if "industry_momentum_percentile" not in df.columns:
            df["industry_momentum_percentile"] = float("nan")

        df["qualifies"] = df["industry_momentum_percentile"] >= INDUSTRY_MOMENTUM_PERCENTILE_THRESHOLD
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
            confidence=float(row.industry_momentum_percentile),
            strategy_name=self.name,
            reason=(f"this stock's industry's 6-month formation return entered the top decile "
                    f"today (industry percentile {row.industry_momentum_percentile:.1f}), did "
                    f"not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
