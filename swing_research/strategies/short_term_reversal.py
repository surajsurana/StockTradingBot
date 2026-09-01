"""
Short-Term Reversal -- tests whether stocks with the WORST returns over a
short (1-month) formation period subsequently OUTPERFORM over the next
month, the mirror-image effect of every momentum strategy in this
program.

*** NOT THE SAME STRATEGY AS strategies/mean_reversion.py *** -- that
existing production strategy is a per-symbol RSI-oversold + Bollinger-
Band technical signal. This strategy is a cross-sectional decile sort on
prior 1-month return, from a completely different academic source and a
completely different mechanism. Named `short_term_reversal` specifically
to avoid any confusion with the unrelated production `mean_reversion`
strategy.

Source: Jegadeesh, N. (1990), "Evidence of Predictable Behavior of
Security Returns," *The Journal of Finance*, Vol. 45, No. 3. Documents
significant NEGATIVE autocorrelation in individual stock returns at short
(weekly-to-monthly) lags -- the opposite sign from the 3-12 month
momentum effect (Jegadeesh & Titman 1993). The paper's headline result is
at the ONE-MONTH lag.

=========================== DOCUMENTED RULES ===========================

- Formation period: prior ONE MONTH return (the paper's headline,
  most-replicated lag).
- Cross-sectional decile sort by formation-period return at each
  formation date.
- Long the BOTTOM decile (worst performers / "losers"); the paper's
  zero-cost portfolio shorts the top decile (best performers / "winners")
  -- the reverse polarity of every momentum strategy already tested in
  this program.
- Holding period: one month, standard Jegadeesh-style overlapping-
  portfolio construction (new position formed every month).

===================== IMPLEMENTATION ASSUMPTIONS =====================
(approved 2026-08-05 -- mirrors 52-Week High Momentum's and Cross-
Sectional Momentum's own approved precedent wherever the underlying
methodological gap is the same, with polarity reversed and horizon
shortened)

1. LONG ONLY. Same reason as every prior strategy -- no NSE cash SLB
   infrastructure for a genuine short.
   Estimated impact: DIRECTIONALLY UNKNOWN.
2. FORMATION PERIOD = 21 TRADING DAYS (1 month), the paper's own headline
   lag.
   Estimated impact: MINOR -- a direct restatement of the documented
   specification.
3. SINGLE-VINTAGE HOLDING, not the paper's overlapping-portfolio
   construction: enters on the state-transition day a symbol's formation-
   return percentile first drops to <=10 (bottom decile), holds ONE
   position per symbol for up to 21 trading days (1 month), no monthly
   rebalancing into new overlapping vintages.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN --
   identical reasoning to every prior cross-sectional strategy's own
   single-vintage deviation. Note the much shorter holding period (1
   month vs. 6 months for the momentum strategies) means meaningfully
   more trade turnover -- a genuine behavioral difference, not just a
   weaker/stronger version of the same thing.
4. EXIT RULE: ONLY a 21-trading-day time-stop (the direct single-vintage
   analogue of the paper's 1-month holding period) OR the synthetic
   protective stop below, whichever comes first. Deliberately NO
   percentile-based early exit -- same discipline established for every
   prior strategy in this program.
   Estimated impact: N/A for the time-stop itself.
5. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source
   paper is a factor-return study with zero position-level risk
   management, required to run this through this program's position-
   based backtesting engine at all. Worth noting: since this strategy
   buys stocks that just fell sharply, entries are inherently closer (in
   percentage terms) to a plausible further-decline scenario than a
   momentum strategy's entries -- the SAME 8% convention is used
   regardless, disclosed here as a point of interpretive difference, not
   a rule change.
   Estimated impact: MODERATE, one-directional.
6. RANK THRESHOLD: bottom decile = formation-return percentile <= 10 (a
   direct restatement of "bottom decile" into this program's existing
   0-100 percentile convention -- note the inverted direction vs. every
   momentum strategy's >=90 threshold; this is a mirror image by design).
   Estimated impact: MINOR, a direct restatement of the documented rule.
7. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit) -- not documented in the source.
   Estimated impact: MINOR -- standard convention already used elsewhere.

Rebalance frequency: formation-return percentile is computed DAILY, but
entries only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

SHORT_TERM_REVERSAL_FORMATION_DAYS = 21   # 1 month
REVERSAL_PERCENTILE_THRESHOLD = 10.0      # bottom decile (inverted vs. momentum strategies' 90)
HOLDING_PERIOD_DAYS = 21                  # 1 month
# See fifty_two_week_high_momentum.py / cross_sectional_momentum.py's
# identical convention: exit_signal_at() only receives the current row and
# OpenPosition's own entry_date (a calendar date), no shared trading-day
# bar index -- the 21-TRADING-day holding period is checked via an
# equivalent CALENDAR-day threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class ShortTermReversalStrategy(Strategy):
    name = "short_term_reversal"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = SHORT_TERM_REVERSAL_FORMATION_DAYS  # needs the full 21-day formation window

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # reversal_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_short_term_reversal_percentile_ranks(). If genuinely
        # absent (e.g. a unit test not exercising the cross-sectional
        # wiring), treat as "not in the bottom decile" rather than crashing
        # -- NaN >= threshold is always False, so this must be checked
        # explicitly since we're testing <= here.
        if "reversal_percentile" not in df.columns:
            df["reversal_percentile"] = float("nan")

        df["qualifies"] = df["reversal_percentile"] <= REVERSAL_PERCENTILE_THRESHOLD
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
            confidence=100.0 - float(row.reversal_percentile),
            strategy_name=self.name,
            reason=(f"1-month formation return entered the bottom decile today "
                    f"(percentile {row.reversal_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
