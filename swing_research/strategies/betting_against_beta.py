"""
Betting Against Beta -- tests whether NSE stocks with the LOWEST estimated
systematic risk (beta) subsequently OUTPERFORM a plain cross-sectional
benchmark, long-only and unlevered. First RISK-BASED (not price-pattern-
based) strategy in this program.

Source: Frazzini, A. and Pedersen, L.H. (2014), "Betting Against Beta,"
The Journal of Financial Economics, Vol. 111, No. 1. Leverage-constrained
investors overpay for high-beta stocks to get leveraged-like market
exposure without borrowing, compressing high-beta expected returns and
leaving low-beta stocks underpriced relative to their risk.

=========================== DOCUMENTED RULES ===========================

- Beta estimator (the paper's own specific construction, NOT a plain OLS
  regression beta): beta_hat = rho_hat x (sigma_hat_i / sigma_hat_m).
  rho_hat (correlation) from OVERLAPPING 3-day log returns; sigma_hat
  (volatility, stock and market) from 1-day log returns. Shrunk toward the
  cross-sectional mean of 1.0: beta = 0.6 x beta_hat + 0.4 x 1.
- The paper's own factor is long (1/beta_L) x [low-beta portfolio], short
  (1/beta_H) x [high-beta portfolio], each leg rescaled to its own beta=1
  at formation -- a leverage-scaled, beta-neutral long-short construction.
- Rebalanced monthly.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(approved 2026-08-15 -- see swing_research/published_research_analyst.py's
BETTING_AGAINST_BETA record for the full disclosed reasoning behind each)

1. LONG ONLY, UNLEVERED. No margin/leverage infrastructure anywhere in
   this platform, no NSE SLB infrastructure for a genuine short. This
   measures only the long, unlevered, low-beta STOCK-SELECTION return --
   NOT the paper's own leverage-scaled, beta-neutral factor return.
   Estimated impact: LIKELY UNDERSTATES the paper's own headline result.
2. VOLATILITY/CORRELATION LOOKBACK = 1 YEAR (252 trading days), shortened
   from the paper's preferred 5-year (minimum 3-year) sigma window --
   structurally required to fit this program's frozen 3-year
   recent-period check (a 3-5yr warm-up would consume the entire
   recent-period slice with zero days left to trade). Correlation kept at
   the paper's own 1-year window (unchanged).
   Estimated impact: MODERATE, DIRECTIONALLY UNKNOWN -- a shorter window
   is noisier/more regime-reactive than the paper's own preferred estimate.
3. CORRELATION ESTIMATOR kept faithful to the paper's overlapping 3-day
   log returns (not simplified to plain daily-return correlation).
   Estimated impact: MINOR positive -- preserves the paper's own
   thin-trading correction, relevant given NSE's wide liquidity range.
4. RAW DAILY RETURNS (not excess-of-risk-free) for beta estimation -- no
   risk-free-rate time series integrated in this platform.
   Estimated impact: NEGLIGIBLE at daily frequency.
5. SINGLE-VINTAGE HOLDING, not the paper's continuous monthly rebalancing
   into overlapping positions: enters on the state-transition day a
   symbol's shrunk-beta percentile first drops to <=10 (bottom decile),
   holds ONE position per symbol for up to 21 trading days (1 month,
   matching the paper's monthly rebalance cadence).
   Estimated impact: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- same
   reasoning as every prior cross-sectional strategy's own single-vintage
   deviation.
6. EXIT RULE: ONLY a 21-trading-day time-stop OR the synthetic protective
   stop below, whichever comes first. No percentile-based early exit --
   same discipline established for every prior strategy in this program.
7. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL.
8. RANK THRESHOLD: bottom decile = shrunk-beta percentile <= 10 (lowest
   beta), this program's existing 0-100 percentile convention.
9. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.

Rebalance frequency: shrunk-beta percentile is computed DAILY, but entries
only fire on the qualifying state-transition day.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.cross_sectional import BETA_LOOKBACK_DAYS, BETA_CORRELATION_RETURN_LAG_DAYS

BETA_PERCENTILE_THRESHOLD = 10.0   # bottom decile (lowest beta)
HOLDING_PERIOD_DAYS = 21           # 1 month, matching the paper's monthly rebalance cadence
# See fifty_two_week_high_momentum.py / short_term_reversal.py's identical
# convention: exit_signal_at() only receives the current row and
# OpenPosition's own entry_date (a calendar date), no shared trading-day
# bar index -- the 21-TRADING-day holding period is checked via an
# equivalent CALENDAR-day threshold instead.
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class BettingAgainstBetaStrategy(Strategy):
    name = "betting_against_beta"
    max_units = 1                     # single-vintage, no pyramiding -- see module docstring
    risk_pct_per_unit = 0.01
    # BETA_LOOKBACK_DAYS (sigma/rho window) plus BETA_CORRELATION_RETURN_LAG_DAYS
    # (the overlapping-return construction itself needs a few extra prior
    # days before its own rolling window can start) -- see
    # swing_research/cross_sectional.py's compute_shrunk_beta_score().
    min_lookback_days = BETA_LOOKBACK_DAYS + BETA_CORRELATION_RETURN_LAG_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # beta_percentile is injected by the caller (research_director, via
        # simulate_portfolio()'s extra_columns_by_symbol) BEFORE precompute()
        # runs -- see swing_research/cross_sectional.py's
        # compute_shrunk_beta_percentile_ranks(). If genuinely absent (e.g. a
        # unit test not exercising the cross-sectional wiring), treat as
        # "not in the bottom decile" rather than crashing.
        if "beta_percentile" not in df.columns:
            df["beta_percentile"] = float("nan")

        df["qualifies"] = df["beta_percentile"] <= BETA_PERCENTILE_THRESHOLD
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
            confidence=100.0 - float(row.beta_percentile),
            strategy_name=self.name,
            reason=(f"Shrunk beta entered the bottom decile today "
                    f"(percentile {row.beta_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
