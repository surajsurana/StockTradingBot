"""
Minervini Trend Template Filter -- named "Filter" deliberately (per
explicit approval 2026-08-03), NOT "SEPA" or "VCP breakout": this
implements Mark Minervini's 8-criterion Trend Template exactly as
documented, but the ENTRY TRIGGER is a disclosed mechanical simplification,
not his real VCP (Volatility Contraction Pattern) base/pivot methodology --
which has no canonical, publicly-documented numeric thresholds (confirmed
during the original published-swing-research pass: "any parameterization
would be our own invention, not Minervini's stated rule," confidence 3/10).
This experiment tests whether the Trend Template's objective SCREENING
criteria have edge on NSE, not whether real-world SEPA (which layers
skilled, partly-discretionary pivot selection on top of the Template) does.

Source: *Trade Like a Stock Market Wizard* (2013), *Think & Trade Like a
Champion* (2016), Mark Minervini.

=========================== DOCUMENTED RULES ===========================
(corroborated consistently across independent sources in the original
research pass -- see swing_research/strategy_library/ for full citations)

The 8 Trend Template criteria (ALL must pass):
  1. Price above both the 150-day and 200-day moving average.
  2. 150-day MA above the 200-day MA.
  3. 200-day MA trending up for at least ~1 month.
  4. 50-day MA above both the 150-day and 200-day MA.
  5. Price above the 50-day MA.
  6. Price at least 30% above its 52-week low.
  7. Price within 25% of its 52-week high.
  8. Relative Strength ranking >= 70th percentile vs. the universe.
Stop-loss: 7-8% maximum from entry.
Position sizing: 1.25-2.5% of equity at risk per trade.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(NOT verbatim documented -- our own disclosed interpretation; see each
field's "estimated impact" for how much this could move the result)

1. ENTRY TRIGGER: fires the day a symbol TRANSITIONS from not satisfying
   to satisfying all 8 criteria (state-transition convention already used
   elsewhere in this codebase -- strategies/mean_reversion.py's "fires only
   the day it *becomes* oversold," Turtle's own exit rule). Real Minervini
   waits for a VCP base to form and buys a breakout from its pivot, often
   well AFTER the Template first qualifies.
   Estimated impact: LIKELY MATERIAL. A raw Template-qualification day is
   probably noisier and less-timed than a real VCP pivot breakout -- this
   could understate the real methodology's edge (buying before a proper
   base has formed, into more whipsaw) rather than overstate it. Treat any
   REJECT verdict on this experiment as inconclusive about real SEPA, not
   as a refutation of it.
2. EXIT RULE: close below the 50-day MA (signal exit) or the stop-loss,
   whichever comes first. The 50-day MA is already load-bearing in the
   Template itself (criteria 4-5), so using its violation as the exit is
   consistent with the documented framework's own structure -- but it is
   OUR interpretation, not a verbatim Minervini rule (his exit discipline
   is the least-documented part of the whole system in every source found).
   Estimated impact: MODERATE. A tighter/looser exit rule would directly
   change holding period and win rate; 50-day MA is a reasonably standard,
   defensible choice but not verified as Minervini's own.
3. STOP-LOSS: fixed 8% (top of the documented 7-8% range).
   Estimated impact: MINOR. Within the documented range either way.
4. POSITION SIZING: 1.25% of equity at risk per trade (bottom of the
   documented 1.25-2.5% range, conservative default), SINGLE UNIT --
   pyramiding ("into confirmed winners") is NOT implemented this round,
   since the exact trigger/sizing for it is undocumented anywhere found
   and inventing one would violate the "no invented hybrid rules" mandate.
   Estimated impact: MODERATE, one-directional. Omitting pyramiding can
   only UNDERSTATE this methodology's real return relative to a full
   implementation (real Minervini adds to winners), never overstate it.
5. RELATIVE STRENGTH: IBD's own RS Rating formula is proprietary and not
   publicly documented in exact form. Substituted with a transparent,
   commonly-cited open approximation (see swing_research/cross_sectional.py's
   compute_rs_score() -- a recency-weighted blend of trailing 3/6/9/12-month
   returns), converted to a cross-sectional percentile vs. the frozen universe.
   Estimated impact: LIKELY MATERIAL but DIRECTIONALLY UNKNOWN. This is the
   single biggest fidelity gap in the whole implementation -- IBD's real RS
   Rating incorporates additional smoothing/normalization details that
   aren't public. Criterion 8 (the RS gate itself) may pass a meaningfully
   different set of symbols than the real Minervini/IBD screen would.

52-week high/low and the 200-day-MA "trending up" check both use Close-
based rolling windows (not intraday High/Low) for consistency with every
other indicator in this program -- a minor, low-impact convention choice,
not flagged as a major assumption.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

MA_SHORT, MA_MEDIUM, MA_LONG = 50, 150, 200
LOOKBACK_52W = 252
TREND_UP_LOOKBACK = 21          # ~1 trading month, for the 200-day MA "trending up" check
RS_PERCENTILE_THRESHOLD = 70.0
LOW_52W_MIN_PCT_ABOVE = 0.30     # criterion 6: >= 30% above 52-week low
HIGH_52W_MAX_PCT_BELOW = 0.25    # criterion 7: within 25% of 52-week high
STOP_LOSS_PCT = 0.08


class MinerviniTrendTemplateFilterStrategy(Strategy):
    name = "minervini_trend_template_filter"
    max_units = 1                 # no pyramiding this round -- see module docstring
    risk_pct_per_unit = 0.0125     # 1.25% of equity per trade (documented range: 1.25-2.5%)
    min_lookback_days = LOOKBACK_52W  # the binding constraint (252) vs. ma200_trending_up's 221

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        close = df["Close"]

        df["ma50"] = close.rolling(MA_SHORT).mean()
        df["ma150"] = close.rolling(MA_MEDIUM).mean()
        df["ma200"] = close.rolling(MA_LONG).mean()
        df["ma200_trending_up"] = df["ma200"] > df["ma200"].shift(TREND_UP_LOOKBACK)
        df["low_52w"] = close.rolling(LOOKBACK_52W).min()
        df["high_52w"] = close.rolling(LOOKBACK_52W).max()

        # rs_percentile is injected by the caller (research_director, via
        # simulate_portfolio()'s extra_columns_by_symbol) BEFORE precompute()
        # runs -- see swing_research/cross_sectional.py. If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring), treat
        # as "criterion 8 fails" rather than crashing.
        if "rs_percentile" not in df.columns:
            df["rs_percentile"] = float("nan")

        df["qualifies"] = (
            (close > df["ma150"]) & (close > df["ma200"])
            & (df["ma150"] > df["ma200"])
            & df["ma200_trending_up"]
            & (df["ma50"] > df["ma150"]) & (df["ma50"] > df["ma200"])
            & (close > df["ma50"])
            & (close >= df["low_52w"] * (1 + LOW_52W_MIN_PCT_ABOVE))
            & (close >= df["high_52w"] / (1 + HIGH_52W_MAX_PCT_BELOW))
            & (df["rs_percentile"] >= RS_PERCENTILE_THRESHOLD)
        )
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
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
            confidence=float(row.rs_percentile),
            strategy_name=self.name,
            reason=(f"Trend Template qualified today (RS pctile {row.rs_percentile:.1f}), "
                    f"did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if pd.isna(row.ma50):
            return None
        if float(row.Close) < float(row.ma50):
            return float(row.Close)
        return None
