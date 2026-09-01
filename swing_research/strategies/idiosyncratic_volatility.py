"""
Idiosyncratic Volatility Anomaly -- tests whether NSE stocks with the
LOWEST idiosyncratic (residual, market-model-adjusted) volatility
subsequently OUTPERFORM a plain cross-sectional benchmark, long-only.
Same RISK-BASED family as Betting Against Beta (SW-009, REJECTED after
genuine regime decay in its recent-period/robustness checks) -- a related
but structurally distinct signal (residual volatility, not systematic
beta), tested independently on its own merits.

Source: Ang, A., Hodrick, R.J., Xing, Y. and Zhang, X. (2006), "The
Cross-Section of Volatility and Expected Returns," The Journal of Finance,
Vol. 61, No. 1. Stocks with high idiosyncratic volatility earn
anomalously LOW subsequent returns -- the opposite of what a risk premium
would predict -- attributed to lottery-preference/limits-to-arbitrage
effects that keep high-idio-vol stocks persistently overpriced.

=========================== DOCUMENTED RULES ===========================

- Idiosyncratic volatility = std dev of the residuals from a regression of
  daily stock returns on a factor model, over the trailing month.
- The paper's PRIMARY specification regresses on the FAMA-FRENCH 3-FACTOR
  model (market, SMB, HML).
- Quintile-sorted, portfolios held 1 month (monthly rebalance), long the
  LOWEST-idio-vol quintile, short the HIGHEST (long-short in the original).

===================== IMPLEMENTATION ASSUMPTIONS =====================
(see swing_research/published_research_analyst.py's
IDIOSYNCRATIC_VOLATILITY_ANOMALY record for the full disclosed reasoning
behind each)

1. SINGLE-FACTOR (CAPM/market-model) RESIDUAL VOLATILITY instead of the
   paper's primary 3-factor (market+SMB+HML) construction. SMB/HML need
   point-in-time market-cap and book-to-market data this platform has
   already confirmed unavailable. NOT an invented substitute -- the
   paper's own robustness section reports results are qualitatively
   unchanged using a single-factor market-model residual.
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- a
   bigger fidelity gap than most prior adaptations, since it drops two of
   the three regressors the paper's own headline result is built on.
2. FORMATION WINDOW = 21 trading days (~1 month), matching the paper's own
   monthly re-formation -- NO shortening needed, unlike Betting Against
   Beta's beta lookback (this window already fits comfortably inside the
   frozen 3-year recent-period check).
3. LONG ONLY. No NSE SLB infrastructure for a genuine short (same reason
   as every prior strategy in this program).
   Estimated impact: measures only the long, low-idio-vol leg's own
   return, not the paper's own long-short spread.
4. SINGLE-VINTAGE HOLDING, not the paper's continuous monthly rebalancing
   into overlapping positions: enters on the state-transition day a
   symbol's idio-vol percentile first drops to <=10 (bottom decile),
   holds ONE position per symbol for up to 21 trading days (1 month).
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- same
   reasoning as every prior cross-sectional strategy's own single-vintage
   deviation.
5. EXIT RULE: ONLY a 21-trading-day time-stop OR the synthetic protective
   stop below, whichever comes first. No percentile-based early exit --
   same discipline established for every prior strategy in this program.
6. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL.
7. RANK THRESHOLD: bottom decile = idio-vol percentile <= 10 (lowest
   idiosyncratic volatility), this program's existing 0-100 percentile
   convention, same "long the calmest decile" polarity as MAX Effect and
   Betting Against Beta.
8. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.

KNOWN INTERACTION RISK (disclosed in the roadmap candidate profile before
this implementation began): the idio-vol measure is known in the
literature to interact with short-term reversal if not separately
controlled for -- this platform does not attempt that control, so any
observed edge here should be interpreted with that caveat rather than as
proof of a "pure" idiosyncratic-volatility effect.

Rebalance frequency: idio-vol percentile is computed DAILY, but entries
only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import IVOL_FORMATION_DAYS

IVOL_PERCENTILE_THRESHOLD = 10.0   # bottom decile (lowest idiosyncratic volatility)
HOLDING_PERIOD_DAYS = 21           # 1 month, matching the paper's monthly rebalance cadence
# Same convention as betting_against_beta.py / fifty_two_week_high_momentum.py:
# exit_signal_at() only receives the current row and OpenPosition's own
# entry_date (a calendar date), no shared trading-day bar index -- the
# 21-TRADING-day holding period is checked via an equivalent CALENDAR-day
# threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class IdiosyncraticVolatilityStrategy(Strategy):
    name = "idiosyncratic_volatility"
    max_units = 1                     # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    min_lookback_days = IVOL_FORMATION_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # idio_vol_percentile is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see swing_research/cross_sectional.py's
        # compute_idiosyncratic_volatility_percentile_ranks(). If genuinely
        # absent (e.g. a unit test not exercising the cross-sectional
        # wiring), treat as "not in the bottom decile" rather than crashing.
        if "idio_vol_percentile" not in df.columns:
            df["idio_vol_percentile"] = float("nan")

        df["qualifies"] = df["idio_vol_percentile"] <= IVOL_PERCENTILE_THRESHOLD
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
            confidence=100.0 - float(row.idio_vol_percentile),
            strategy_name=self.name,
            reason=(f"Idiosyncratic volatility entered the bottom decile today "
                    f"(percentile {row.idio_vol_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
