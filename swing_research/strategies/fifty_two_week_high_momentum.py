"""
52-Week High Momentum -- tests whether a stock's nearness to its own
52-week high, cross-sectionally ranked against the universe, predicts
future returns.

Source: George, T.J. and Hwang, C-Y. (2004), "The 52-Week High and
Momentum Investing," *The Journal of Finance*, Vol. 59, No. 5. Central
finding: nearness to the 52-week high is a BETTER predictor of future
returns than standard past-return (Jegadeesh-Titman) momentum -- stocks
near their 52-week high continue to outperform, stocks far from it
continue to underperform.

=========================== DOCUMENTED RULES ===========================

- Nearness ratio, each formation date: ratio = Price / 52-week-high price.
- Cross-sectional decile sort by nearness ratio at each formation date.
- Long the top decile (nearest to the 52-week high); the paper's factor
  construction shorts the bottom decile.
- Holding period K, tested at K=3,6,9,12 months in the paper; K=6 months
  is the most commonly cited/replicated specification.
- Standard Jegadeesh-Titman OVERLAPPING PORTFOLIO construction: a new
  K-month position initiated every month, K simultaneous 1/K-weighted
  "vintages" held at any time, realized return = equal-weighted average
  across active vintages.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(approved 2026-08-04 -- see the approved implementation plan for the full
NSE-adaptation table)

1. LONG ONLY. Same reason as Turtle -- NSE cash equities lack the SLB
   infrastructure for a genuine multi-month short.
   Estimated impact: DIRECTIONALLY UNKNOWN -- omits the short side
   entirely, doesn't bias the long side's own measured performance.
2. SINGLE-VINTAGE HOLDING, not the paper's overlapping-portfolio
   construction: enters on the state-transition day a symbol's nearness
   percentile first crosses >=90 (top decile), holds ONE position per
   symbol for up to 126 trading days (K=6 months), no monthly rebalancing
   into new overlapping vintages.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN. A
   single-vintage system realizes ONE entry point's path per qualifying
   episode, with materially higher variance than the paper's smoothed
   multi-vintage average (which blends many staggered entry points).
   Could realize a better OR worse outcome than the academic average on
   any given episode -- this is the single largest structural deviation
   from the source methodology in this experiment.
3. EXIT RULE: ONLY a 126-trading-day time-stop (the direct single-vintage
   analogue of the paper's K=6-month holding period) OR the synthetic
   protective stop below, whichever comes first. Deliberately NO
   percentile-based early exit -- an earlier draft of the implementation
   plan included one; removed per explicit direction before
   implementation began, since it would have been an invented rule with
   no basis in the source paper, hybridizing the baseline replication.
   Any percentile-based-exit variant is reserved for a future, separately
   labeled research iteration, never folded into this baseline.
   Estimated impact: N/A for the time-stop itself (direct analogue of the
   documented holding period).
4. PROTECTIVE STOP-LOSS: 8% below entry, same convention as Minervini.
   NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source paper is a
   factor-return study, not a trading system, and has no position-level
   risk management whatsoever. Required to run this through this
   program's position-based backtesting engine at all.
   Estimated impact: MODERATE, one-directional -- can only reduce
   maximum single-position loss relative to "no stop at all" (which isn't
   backtestable here), never increase it.
5. RANK THRESHOLD: top decile = nearness percentile >= 90 (an exact
   translation of "top decile" into this program's existing 0-100
   percentile convention, same units already used by Minervini's RS
   percentile).
   Estimated impact: MINOR, a direct restatement of the documented rule.
6. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit, same as Turtle's base rate) --
   not documented in the source (a factor portfolio has no per-position
   sizing rule), needed for the same reason as the protective stop.
   Estimated impact: MINOR -- standard convention already used elsewhere
   in this program.

Rebalance frequency: nearness percentile is computed DAILY (the natural
output of the vectorized cross-sectional rank,
swing_research/cross_sectional.py's compute_52w_high_nearness_percentile_ranks()),
but entries only fire on the qualifying state-transition day, not every
day a symbol happens to already be in the top decile -- this does not
change the substance of "enter when a stock enters the top decile,"
only the mechanical timing precision (daily vs. the paper's monthly
formation dates).
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

LOOKBACK_52W = 252
NEARNESS_PERCENTILE_THRESHOLD = 90.0   # top decile
HOLDING_PERIOD_DAYS = 126               # K=6 months, ~21 trading days/month
# exit_signal_at() only ever receives the current row and the OpenPosition's
# own entry_date (a calendar date) -- there is no per-symbol trading-day bar
# index shared between entry and the current row available through this
# interface (see swing_research/base.py's Strategy contract), so the
# 126-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead. Disclosed implementation detail, not a rule change:
# 126 trading days / (252 trading days per year) x 365.25 calendar days.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class FiftyTwoWeekHighMomentumStrategy(Strategy):
    name = "fifty_two_week_high_momentum"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = LOOKBACK_52W  # needs the full 252-day rolling high

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        close = df["Close"]

        df["high_52w"] = close.rolling(LOOKBACK_52W).max()
        df["nearness_ratio"] = close / df["high_52w"]

        # nearness_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_52w_high_nearness_percentile_ranks(). If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "nearness_percentile" not in df.columns:
            df["nearness_percentile"] = float("nan")

        df["qualifies"] = df["nearness_percentile"] >= NEARNESS_PERCENTILE_THRESHOLD
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)

        # Calendar date as an ordinary column -- exit_signal_at() needs it
        # to compare against OpenPosition's own entry_date (see
        # HOLDING_PERIOD_CALENDAR_DAYS above for why this is calendar days,
        # not a trading-day bar index).
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
            reason=(f"52-week-high nearness entered the top decile today "
                    f"(percentile {row.nearness_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
