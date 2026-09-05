"""
Overnight Return Anomaly -- tests whether stocks with the STRONGEST
cumulative close-to-open ("overnight") returns over a recent formation
period continue to earn strong overnight returns going forward, a
persistence/momentum effect specific to the overnight return COMPONENT
of a stock's total return, distinct from every price-pattern strategy
already in this program (all of which are built on TOTAL, Close-to-Close
returns).

Source: Lou, D., Polk, C. and Skouras, S. (2019), "A Tug of War: Overnight
Versus Intraday Expected Returns," Journal of Financial Economics, Vol.
134, No. 1 (see also the earlier related finding in Berkman, D., Koch,
P.D., Tuttle, L. and Zhang, S.J. (2012), "Paying Attention: Overnight
Returns and the Cross-Section of Stock Returns," Journal of Finance,
Vol. 67, No. 5). The papers' central finding is a genuine "tug of war":
a stock's cumulative overnight (Close-to-Open) return PERSISTS (high
past overnight return predicts high future overnight return), while its
cumulative intraday (Open-to-Close) return REVERSES over the same
horizon -- two components of the SAME total return moving in OPPOSITE
directions. This strategy tests only the overnight-persistence half.

=========================== DOCUMENTED RULES ===========================

- Daily overnight return_t = Open_t / Close_{t-1} - 1 (the return earned
  by holding from yesterday's close to today's open, capturing zero
  intraday-session exposure).
- Formation period: cumulative (compounded) overnight return over the
  prior ONE MONTH (21 trading days).
- Cross-sectional decile sort by formation-period cumulative overnight
  return at each formation date.
- Long the TOP decile (strongest cumulative overnight return) -- the
  paper's own long-side framing of the persistence effect.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(new strategy, 2026-09-05 -- Research Director's top-ranked unresearched
candidate per the regenerated roadmap, swing_research/RESEARCH_ROADMAP.md)

1. LONG ONLY. Same reason as every prior strategy -- no NSE cash SLB
   infrastructure for a genuine short.
   Estimated impact: DIRECTIONALLY UNKNOWN.
2. FORMATION PERIOD = 21 TRADING DAYS (1 month) -- this program's own
   established convention for a short-horizon anomaly (matches
   short_term_reversal.py's identical choice), not dictated by one single
   preferred window in the source papers (which examine multiple
   horizons). A restatement into this program's existing convention, not
   an independent methodological choice.
   Estimated impact: MINOR-to-MODERATE -- the papers show the persistence
   effect exists across several formation windows, but effect size is not
   guaranteed to be identical at every horizon.
3. *** THE SINGLE MOST IMPORTANT DISCLOSED GAP FOR THIS STRATEGY ***
   HOLDING PERIOD = 1 TRADING DAY, the shortest this program's
   Trade-based (single entry_price/exit_price per trade, no intra-trade
   session accounting) backtesting engine can express -- deliberately
   the shortest possible hold, to get as close as this engine can to
   isolating the overnight component alone. Combined with next-day-open
   fill timing (see run_overnight_return_experiment() in
   research_director.py), this still does NOT achieve a pure
   buy-at-close/sell-at-next-open round trip: with fill_timing=
   "next_day_open", BOTH the entry and exit price get shifted to an
   Open price (see execution_realism_engine.py's apply_execution_realism()
   -- entry_date and exit_date each resolve to the NEXT trading day's
   Open), so a 1-trading-day hold is actually filled Open(t+1) to
   Open(t+2) -- ONE FULL INTRADAY SESSION plus its flanking overnight
   moves, not overnight return alone. Per the source papers' own central
   finding, that intraday session's return is expected to move in the
   OPPOSITE direction from the overnight-persistence effect being tested
   here -- so this implementation's measured return is a NET of the
   effect under test and a same-magnitude, oppositely-signed
   contaminating effect, not the pure signal the papers isolate.
   Estimated impact: MATERIAL, DIRECTIONALLY UNKNOWN (specifically:
   LIKELY UNDERSTATES the pure overnight-only effect, since the
   intraday-reversal component the paper documents works directly
   against it here) -- this is disclosed as the primary reason this
   strategy's result should be read as a LOWER-BOUND test of the
   underlying mechanism, not a faithful isolation of it. A true
   overnight-only backtest would require a different, session-aware
   execution model this program does not have.
4. EXIT RULE: ONLY the unconditional 1-trading-day time-stop above, OR
   the synthetic protective stop below, whichever comes first.
   Deliberately NO percentile-based early exit -- same discipline
   established for every prior strategy in this program.
5. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY -- required to run this
   through this program's position-based engine at all. Given the
   1-trading-day hold, this is expected to be a near-total no-op (an 8%
   single-session move is rare) -- kept only for structural consistency
   with every other strategy, not because it is expected to bind.
   Estimated impact: NEGLIGIBLE given the short hold.
6. RANK THRESHOLD: top decile = overnight-return percentile >= 90 (this
   program's standard momentum-style threshold convention).
   Estimated impact: MINOR, a direct restatement.
7. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit) -- not documented in the source.
   Estimated impact: MINOR -- standard convention already used elsewhere.

Rebalance frequency: overnight-return percentile is computed DAILY, but
entries only fire on the qualifying state-transition day (same
single-vintage convention as every other cross-sectional strategy here).
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

OVERNIGHT_RETURN_FORMATION_DAYS = 21    # 1 month, this program's convention for short-horizon anomalies
OVERNIGHT_RETURN_PERCENTILE_THRESHOLD = 90.0   # top decile
HOLDING_PERIOD_TRADING_DAYS = 1          # shortest hold this engine supports -- see module docstring point 3
# Same calendar-day translation convention as every other single-vintage
# strategy in this program (exit_signal_at only receives entry_date, a
# calendar date, no shared trading-day bar index).
HOLDING_PERIOD_CALENDAR_DAYS = 1
STOP_LOSS_PCT = 0.08


class OvernightReturnAnomalyStrategy(Strategy):
    name = "overnight_return_anomaly"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = OVERNIGHT_RETURN_FORMATION_DAYS + 1  # +1 for the shift() the overnight return itself needs

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # overnight_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_overnight_return_percentile_ranks(). If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "overnight_percentile" not in df.columns:
            df["overnight_percentile"] = float("nan")

        df["qualifies"] = df["overnight_percentile"] >= OVERNIGHT_RETURN_PERCENTILE_THRESHOLD
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
            confidence=float(row.overnight_percentile),
            strategy_name=self.name,
            reason=(f"1-month cumulative overnight (Close-to-Open) return entered the top decile today "
                    f"(percentile {row.overnight_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
