"""
Price Delay Factor (Slow Information Diffusion) -- the US lane's first
GENUINELY NEW factor mechanism (as opposed to a cross-market port of an
already-researched India strategy), per the roadmap profile added
2026-10-03 (swing_research/research_roadmap.py, key="us_price_delay_factor").

Source: Hou, K. and Moskowitz, T.J. (2005), "Market Frictions, Price
Delay, and the Cross-Section of Expected Returns," The Review of
Financial Studies, Vol. 18, No. 3, 981-1020. For each stock, regress its
own WEEKLY return on the contemporaneous market return AND several weeks
of LAGGED market returns, over a rolling ~3-year window:

    D1 = 1 - R2_restricted / R2_unrestricted

where R2_unrestricted comes from regressing on the contemporaneous AND
lagged market terms, and R2_restricted from the contemporaneous term
alone. D1 is the fraction of a stock's return variation explained by the
market that arrives with a measurable LAG -- i.e. how slowly the stock's
price incorporates market-wide information. Stocks with the highest
delay (attributed by the authors mainly to investor-recognition
frictions -- smaller, less-followed names) earn a return premium not
explained by size, liquidity or microstructure effects the authors
directly tested against.

See swing_research/cross_sectional.py's compute_price_delay_score() /
compute_price_delay_percentile_ranks() for the estimator itself (a
batched rolling multi-lag OLS, the first rolling-regression-with-
multiple-lagged-terms machinery in this codebase -- the existing
single-lag CAPM-style regressions behind Jensen's Alpha / Betting
Against Beta do not extend to this directly, per the roadmap's own
known_weaknesses note).

=========================== DOCUMENTED RULES ===========================

- Each week, estimate D1 from a rolling ~3-year (156-week) window of the
  stock's own weekly return regressed on the contemporaneous AND 4
  lagged weekly market returns, vs. the contemporaneous term alone.
- Sort into deciles; the top decile (highest delay) earns the premium
  (the paper's long side -- see roadmap `direction`). Rebalance monthly,
  equal weights, held one month.

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY, the high-delay top decile only (the paper's spread is
   long-short, and the roadmap `direction` field already discloses this
   as "a disclosed reduction from its long-short construction").
   Estimated impact: DIRECTIONALLY UNKNOWN, standard for this program.
2. NUM_LAGS = 4 weekly lagged market terms (PRICE_DELAY_NUM_LAGS in
   cross_sectional.py). DISCLOSED ASSUMPTION: the paper's primary D1
   specification; a longer lag count is reported only as a robustness
   check there. Estimated impact: MINOR -- D1 is a relative (decile)
   ranking, not an absolute threshold, so a different lag count would
   shift absolute delay values but not necessarily the cross-sectional
   ordering much.
3. ROLLING WINDOW = 156 weeks (~3 years), the paper's own window.
   Estimated impact: NONE on rules (documented), but this materially
   shortens the usable backtest window -- the walk-forward judgement
   does not start until this warm-up has elapsed (see
   run_price_delay_factor_us_experiment's warm_up_days).
4. TOP DECILE = cross-sectional percentile >= 90 among frozen S&P 500
   names with a valid estimate (this program's usual decile convention,
   e.g. max_effect, high_volume_return_premium). Estimated impact:
   NEGLIGIBLE.
5. MARKET = the S&P 500 index (^GSPC, data.fetch_historical.
   fetch_sp500_index), aligned to each stock's own trading days and
   forward-filled, same convention as compute_downside_beta_score.
   Estimated impact: NEGLIGIBLE (an equal-weighted composite of the same
   universe would be a defensible alternative, not used here).
6. STATE-TRANSITION ENTRY: a stock is bought at the close of the first
   day it enters the top decile (was not in it the day before), held 21
   trading days (30 calendar days), then sold at the close -- this
   program's single-vintage convention (max_effect,
   high_volume_return_premium, downside_beta) in place of the paper's
   month-end portfolio formation. Estimated impact: MINOR,
   DIRECTIONALLY UNKNOWN (entries spread across the month instead of
   clustering at month-end).
7. 8% protective stop and 1% risk-per-unit sizing -- none in the source,
   all standard here, same as downside_beta. Estimated impact:
   MODERATE, one-directional for the stop (high-delay stocks are
   smaller/less-followed names per the authors' own attribution, and
   may gap more than the broad universe).
8. WARM-UP: the estimator needs 156 weeks (~3 years) of weekly returns
   PLUS NUM_LAGS weeks before a single delay value can be formed, so the
   experiment runner fetches extra history and starts the walk-forward
   judgement warm_up_days after the data start
   (swing_research/research_director.run_price_delay_factor_us_experiment).
   Estimated impact: NONE on rules; avoids judging a blind warm-up
   window the way EXP-083 did for downside beta before that fix.

Rebalance frequency: monthly per position (single vintage, 21 trading
days); the percentile is recomputed daily from the weekly regression
(forward-filled between weekly re-estimates, see
compute_price_delay_score).
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

PRICE_DELAY_PERCENTILE_THRESHOLD = 90.0   # top decile = highest price delay (the paper's premium side)
HOLDING_PERIOD_DAYS = 21                  # one month
HOLDING_PERIOD_CALENDAR_DAYS = round(HOLDING_PERIOD_DAYS / 252 * 365.25)
STOP_LOSS_PCT = 0.08


class PriceDelayFactorStrategy(Strategy):
    name = "price_delay_factor"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 756   # ~156 weeks * 5 trading days, the rolling regression's own warm-up

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        if "price_delay_percentile" not in df.columns:
            df["price_delay_percentile"] = float("nan")
        df["qualifies"] = df["price_delay_percentile"] >= PRICE_DELAY_PERCENTILE_THRESHOLD
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev) or not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=float(row.price_delay_percentile), strategy_name=self.name,
            reason=(f"Price delay entered the top decile today (percentile "
                    f"{row.price_delay_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
