"""
High-Volume Return Premium -- tests whether stocks with an unusually
large recent trading-volume SHOCK (relative to their own typical volume,
not raw volume level) subsequently appreciate over the following month.

Source: Gervais, S., Kaniel, R. and Mingelgrin, D.H. (2001), "The
High-Volume Return Premium," The Journal of Finance, Vol. 56, No. 3.
Verified formation/holding periods (2026-09-06, before implementation):
stocks experiencing unusually high (low) trading volume over a day or a
week tend to appreciate (depreciate) over the course of the following
MONTH -- a genuinely short formation window (days-to-a-week) and a
1-month holding period, distinct in cadence from every long-horizon
candidate on the roadmap (e.g. the deliberately-deferred Long-Term
De Bondt-Thaler Reversal, which holds for 3-5 YEARS -- explicitly ruled
out per direct instruction, 2026-09-06, for being operationally
mismatched with this program's days-to-months cadence).

Mechanism: the paper attributes this to an INVESTOR RECOGNITION /
VISIBILITY effect (Merton, 1987's framework) -- a volume shock draws
attention to a stock, temporarily expanding its investor base and
demand, which takes time to fully play out. First strategy in this
program selecting on a VOLUME metric alone (Amihud, SW-010, uses volume
as the DENOMINATOR of an illiquidity/return ratio, a materially
different construction -- that's a liquidity-cost proxy, this is an
attention/visibility proxy).

=========================== DOCUMENTED RULES ===========================

- Volume shock = a stock's recent (day-to-week) trading volume relative
  to its own typical (longer-run) trading volume -- NOT raw volume
  level, which cannot be compared across stocks of very different sizes.
- Cross-sectional decile sort by this shock ratio at each formation date.
- Long the TOP decile (largest positive volume shock) -- the paper's own
  long-side finding.
- Holding period: approximately ONE MONTH.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(new strategy, 2026-09-06 -- Research Director's next unresearched
candidate per the regenerated roadmap, after Long-Term Reversal was
explicitly deferred for its 3-5 year holding period)

1. LONG ONLY. Same reason as every prior strategy -- no NSE cash SLB
   infrastructure for a genuine short.
   Estimated impact: DIRECTIONALLY UNKNOWN.
2. VOLUME SHOCK CONSTRUCTION: recent window = 5 trading days (1 week,
   the paper's own upper-bound recent-window language), baseline window
   = 252 trading days (1 year, ending immediately before the recent
   window, no overlap) -- this program's own reasoned, disclosed
   operationalization of "recent vs. typical" volume, since the paper's
   own precise ratio construction was not independently reproduced here
   (an interpretive step, same category as Amihud's regression-to-decile-
   sort translation, SW-010).
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- a different
   choice of recent/baseline window lengths could plausibly move both
   which stocks qualify and the measured effect size.
3. FORMATION-TO-ENTRY: state-transition day only (percentile first
   crosses >=90), single-vintage, no pyramiding -- same convention as
   every prior cross-sectional strategy.
4. HOLDING PERIOD = 21 TRADING DAYS (1 month), this program's standard
   restatement of "approximately one month" into a single-vintage,
   fixed-length hold instead of the paper's own (unspecified precisely)
   evaluation-window methodology.
   Estimated impact: MINOR, a direct restatement of the paper's own
   headline holding period.
5. EXIT RULE: ONLY the unconditional 21-trading-day time-stop above, OR
   the synthetic protective stop below, whichever comes first.
   Deliberately NO percentile-based early exit -- same discipline
   established for every prior strategy in this program.
6. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- required to
   run this through this program's position-based engine at all.
   Estimated impact: MODERATE, one-directional.
7. RANK THRESHOLD: top decile = volume-shock percentile >= 90 (this
   program's standard convention).
   Estimated impact: MINOR, a direct restatement.
8. POSITION SIZING: reuses this program's standard risk_pct_per_unit
   convention (1% of equity per unit) -- not documented in the source.
   Estimated impact: MINOR -- standard convention already used elsewhere.

Rebalance frequency: volume-shock percentile is computed DAILY, but
entries only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

HIGH_VOLUME_PERCENTILE_THRESHOLD = 90.0   # top decile
HOLDING_PERIOD_DAYS = 21                  # 1 month
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class HighVolumeReturnPremiumStrategy(Strategy):
    name = "high_volume_return_premium"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    # Needs the full baseline window PLUS the recent window PLUS the shift
    # gap between them (see cross_sectional.py's compute_volume_shock_score):
    # 252 (baseline) + 5 (recent) trading days before a score first exists.
    min_lookback_days = 252 + 5

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # volume_shock_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_volume_shock_percentile_ranks(). If genuinely absent
        # (e.g. a unit test not exercising the cross-sectional wiring),
        # treat as "not in the top decile" rather than crashing.
        if "volume_shock_percentile" not in df.columns:
            df["volume_shock_percentile"] = float("nan")

        df["qualifies"] = df["volume_shock_percentile"] >= HIGH_VOLUME_PERCENTILE_THRESHOLD
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
            confidence=float(row.volume_shock_percentile),
            strategy_name=self.name,
            reason=(f"Volume shock (recent vs. typical trading activity) entered the top decile today "
                    f"(percentile {row.volume_shock_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
