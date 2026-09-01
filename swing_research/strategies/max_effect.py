"""
MAX Effect (Lottery-Demand Anomaly) -- tests whether stocks with an
extreme recent single-day return get overpriced by lottery-seeking
investors and subsequently UNDERPERFORM, so that the CALMEST stocks
(lowest recent maximum daily return) subsequently OUTPERFORM.

Source: Bali, T.G., Cakici, N. and Whitelaw, R.F. (2011), "Maxing Out:
Stocks as Lotteries and the Cross-Section of Expected Returns," *Journal
of Financial Economics*, Vol. 99, No. 2. Central finding: MAX, the single
highest daily return a stock experienced within the past month, negatively
predicts that stock's future return -- a behavioral (gambling-preference)
story, genuinely distinct from every mechanism already tested in this
program (not momentum, not short-horizon reversal, not risk-based beta,
not liquidity, not an earnings event).

=========================== DOCUMENTED RULES ===========================

- MAX, each formation date: the single HIGHEST daily return the stock
  experienced within the trailing ONE MONTH (the paper's primary,
  most-cited specification, "MAX(1)"). The paper also reports a MAX(5)
  robustness variant (average of the 5 highest days in the month) --
  qualitatively similar, slightly weaker significance, included in the
  paper specifically to show the effect isn't driven by a single
  possibly-noisy day. NOT implemented here -- see IMPLEMENTATION
  ASSUMPTIONS #2 below.
- Cross-sectional decile sort by MAX at each formation date.
- Long the BOTTOM decile (lowest MAX -- calmest, least lottery-like
  stocks); the paper's zero-cost portfolio shorts the top decile (highest
  MAX -- most lottery-like stocks).
- Holding period: one month, standard monthly-rebalance construction (a
  new cross-sectional sort every month).
- The paper's regressions show the effect SURVIVES controlling for size,
  book-to-market, momentum, and short-term reversal -- i.e. it is
  documented as NOT simply a repackaging of an effect already in this
  program's portfolio. It is, however, HIGHLY correlated with
  idiosyncratic volatility (a separate, not-yet-implemented roadmap
  candidate) -- disclosed here as a known conceptual-overlap risk for a
  future strategy, not a concern for THIS strategy standing alone.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(2026-08-23 -- mirrors Short-Term Reversal's own approved precedent
wherever the underlying methodological gap is the same)

1. LONG ONLY. Same reason as every prior strategy -- no NSE cash SLB
   infrastructure for a genuine short.
   Estimated impact: DIRECTIONALLY UNKNOWN.
2. MAX(1) ONLY, not the paper's MAX(5) robustness variant.
   Estimated impact: MINOR -- MAX(1) is the paper's own headline,
   primary-table specification, not a weaker substitute; MAX(5) is
   disclosed as an available, not-yet-tested robustness check should this
   strategy's results warrant a closer look.
3. FORMATION PERIOD = 21 TRADING DAYS (~1 month), a direct restatement of
   the paper's own "within the past month" window into this program's
   trading-day convention (identical restatement already used by Short-
   Term Reversal's own 1-month formation).
   Estimated impact: MINOR.
4. SINGLE-VINTAGE HOLDING, not the paper's overlapping-portfolio monthly-
   rebalance construction: enters on the state-transition day a symbol's
   MAX percentile first drops to <=10 (bottom decile), holds ONE position
   per symbol for up to 21 trading days (1 month), no monthly rebalancing
   into new overlapping vintages.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN --
   identical reasoning to every prior cross-sectional strategy's own
   single-vintage deviation.
5. EXIT RULE: ONLY a 21-trading-day time-stop (the direct single-vintage
   analogue of the paper's 1-month holding period) OR the synthetic
   protective stop below, whichever comes first. Deliberately NO
   percentile-based early exit -- same discipline established for every
   prior strategy in this program.
   Estimated impact: N/A for the time-stop itself.
6. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source
   paper is a factor-return study with zero position-level risk
   management, required to run this through this program's position-based
   backtesting engine at all.
   Estimated impact: MODERATE, one-directional.
7. RANK THRESHOLD: bottom decile = MAX percentile <= 10 (a direct
   restatement of "bottom decile" into this program's existing 0-100
   percentile convention).
   Estimated impact: MINOR, a direct restatement of the documented rule.
8. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit) -- not documented in the source.
   Estimated impact: MINOR -- standard convention already used elsewhere.

Rebalance frequency: MAX percentile is computed DAILY, but entries only
fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

MAX_EFFECT_FORMATION_DAYS = 21     # 1 month
MAX_EFFECT_PERCENTILE_THRESHOLD = 10.0   # bottom decile = lowest MAX (calmest stocks)
HOLDING_PERIOD_DAYS = 21           # 1 month
# See fifty_two_week_high_momentum.py / short_term_reversal.py's identical
# convention: exit_signal_at() only receives the current row and
# OpenPosition's own entry_date (a calendar date), no shared trading-day
# bar index -- the 21-TRADING-day holding period is checked via an
# equivalent CALENDAR-day threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class MaxEffectStrategy(Strategy):
    name = "max_effect"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = MAX_EFFECT_FORMATION_DAYS  # needs the full 21-day formation window

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # max_effect_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_max_effect_percentile_ranks(). If genuinely absent (e.g.
        # a unit test not exercising the cross-sectional wiring), treat as
        # "not in the bottom decile" rather than crashing -- NaN <= threshold
        # is always False in pandas, so this is already safe, but the
        # explicit column is added for the same clarity as every sibling
        # strategy's own precompute().
        if "max_effect_percentile" not in df.columns:
            df["max_effect_percentile"] = float("nan")

        df["qualifies"] = df["max_effect_percentile"] <= MAX_EFFECT_PERCENTILE_THRESHOLD
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
            confidence=100.0 - float(row.max_effect_percentile),
            strategy_name=self.name,
            reason=(f"Trailing 1-month MAX (single highest daily return) entered the bottom decile "
                    f"today (percentile {row.max_effect_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
