"""
MAX Effect / Lottery-Demand Anomaly (Crypto) -- the crypto lane's first
behavioural/lottery-demand strategy (2026-11-16): every existing crypto
candidate (implemented or not) is either price-momentum/trend-based,
volatility-time-scaled, or residual-volatility-ranked
(crypto_idiosyncratic_volatility); none measures a single extreme-return
lottery payoff the way MAX does.

Source: Grobys, K. and Junttila, J. (2021), "Speculation and lottery-like
demand in cryptocurrency markets," Journal of International Financial
Markets, Institutions and Money, Vol. 71, 101289 (DOI
10.1016/j.intfin.2021.101289); corroborated at an intraday horizon by
Yadav, M. (2025), "Intraday lottery demands in cryptocurrency market,"
Studies in Economics and Finance (Emerald), DOI 10.1108/SEF-07-2024-0461.
The underlying MAX measure is Bali, T.G., Cakici, N. and Whitelaw, R.F.
(2011), "Maxing Out: Stocks as Lotteries and the Cross-Section of Expected
Returns," Journal of Financial Economics, Vol. 99, No. 2 -- the same
citation already registered for this program's India implementation
(max_effect.py, PASS, paper trading).

*** CENTRAL DISCLOSED CAVEAT (see research_roadmap.py's known_weaknesses
field for this candidate) *** -- the sign of the crypto MAX effect is
GENUINELY HORIZON-DEPENDENT and contested in the literature. At WEEKLY
(Grobys & Junttila 2021) and INTRADAY/hourly (Yadav 2025) horizons,
high-MAX coins UNDERPERFORM -- the same sign as this program's India
strategy. But at DAILY/MONTHLY horizons -- the cadence the India
max_effect.py strategy actually trades on -- at least two other studies
(Li, Urquhart, Wang & Zhang 2021; Ozdamar, Akdeniz & Sensoy 2021) find the
OPPOSITE sign ("MAX momentum," not MAX reversal). This is why the
rebalance cadence below is pinned to the WEEKLY horizon the negative-sign
studies actually tested, deliberately NOT the India sibling's monthly
cadence -- copying that cadence here could easily test the wrong,
already-contradicted sign. This implementation does not adjust the
mechanism to try to force a particular verdict.

=========================== DOCUMENTED RULES ===========================

- MAX, each formation date: the single highest daily return a coin
  experienced within a trailing window (see IMPLEMENTATION ASSUMPTIONS #2
  for the exact window used here).
- Cross-sectional QUINTILE sort by MAX at a WEEKLY rebalance (the paper's
  own documented grouping is quintiles, not this program's usual decile).
- Long the bottom quintile (lowest MAX -- calmest, least lottery-like
  coins); the paper's high-MAX-minus-low-MAX spread (high-MAX coins
  underperform) is a disclosed long-only reduction, same convention as
  every candidate in this program.
- Universe: 20 large, liquid coins (Grobys & Junttila) / top 100 most
  liquid coins (Yadav) -- both closely match this platform's own
  CRYPTO_UNIVERSE (40 large, liquid USDT pairs, data/fetch_crypto.py).

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY (bottom quintile, lowest MAX) -- same disclosed long-only
   reduction as every strategy in this program. Estimated impact:
   DIRECTIONALLY UNKNOWN.
2. MAX FORMATION = 30 CALENDAR DAYS (~1 month), the same 21-TRADING-day ->
   30-CALENDAR-day conversion already established for this lane's own
   crypto_idiosyncratic_volatility.py / crypto_short_term_reversal.py
   (CRYPTO_MAX_EFFECT_FORMATION_DAYS, swing_research/cross_sectional.py)
   -- a direct restatement of the India/US max_effect.py's own "within the
   past month" MAX(1) window. The candidate's own mechanism field
   describes a "trailing window" without naming its exact length, and
   separately names the paper's rebalance cadence as weekly; those need
   not be the same number -- this lane's own crypto_xs_momentum.py already
   uses a 3-week formation window with a 1-week hold, the identical
   "formation window != hold period" pattern reused here. Estimated
   impact: MINOR, a direct restatement of the India sibling's own
   specification.
3. WEEKLY REBALANCE, REBALANCE DAY = Monday (UTC), the first bar after the
   week's close -- the SAME convention as this lane's own
   crypto_xs_momentum.py (the other weekly-rebalance cross-sectional
   crypto strategy): entries fire only on Mondays, exits on the following
   Monday's close (7 calendar days). A coin still in the bottom quintile is
   re-entered the same day (the engine processes exits before entries),
   which IS a weekly rebalance -- matching the candidate's own disclosed
   "High -- weekly full-book rotation" expected trade frequency, unlike
   this lane's other cross-sectional strategies' single-vintage,
   state-transition designs. Estimated impact: MINOR.
4. BOTTOM QUINTILE = cross-sectional percentile <=20, NOT this program's
   usual decile cutoff -- the paper's own documented rule is a quintile
   sort, explicitly named in the mechanism field (research_roadmap.py).
   Estimated impact: MINOR, a direct restatement of the documented rule.
5. PROTECTIVE STOP-LOSS: 20% below entry -- NOT the India sibling's 8%,
   which this lane's own crypto_xs_momentum.py/crypto_tsmom.py/crypto_
   trend_timing*.py/crypto_illiquidity_premium.py/crypto_idiosyncratic_
   volatility.py/crypto_long_horizon_reversal.py/crypto_short_term_
   reversal.py already disclose is an equity-volatility number
   inappropriate for crypto's much higher daily volatility; 20% is reused
   from those strategies rather than inventing a new number. NOT PART OF
   THE ORIGINAL METHODOLOGY AT ALL -- the source papers are factor-return
   studies with zero position-level risk management, same disclosed
   reason as every sibling strategy. Estimated impact: MODERATE,
   one-directional.
6. POSITION SIZING: risk_pct_per_unit=0.025 (12.5% of the book per coin
   against the 20% stop) -- identical sizing convention to every other
   cross-sectional crypto strategy in this lane, not a new number invented
   for this strategy. Estimated impact: MINOR.
7. BOOK IN USDT, fractional quantities, Binance daily OHLCV -- same
   lane-wide convention as every other crypto strategy.
8. COSTS AND TAX (swing_research/crypto_costs.py): applied by
   crypto_lane.py's run_crypto_experiment_generic() exactly as for every
   other crypto strategy. This Strategy class itself produces ordinary
   pre-cost, pre-tax Signals. Estimated impact: MAJOR -- the lane's
   standing question, and especially pointed here given weekly (not
   monthly) full-book rotation implies far more taxable events per year.

Rebalance frequency: weekly (Mondays UTC); the MAX percentile is computed
daily but only read on rebalance days.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

BOTTOM_QUINTILE_PERCENTILE = 20.0   # bottom quintile = lowest MAX (calmest coins) -- see module
                                     # docstring point 4: a quintile, not this module's usual decile
HOLDING_DAYS = 7
STOP_LOSS_PCT = 0.20
REBALANCE_WEEKDAY = 0   # Monday


class CryptoMaxEffectStrategy(Strategy):
    name = "crypto_max_effect"
    max_units = 1
    risk_pct_per_unit = 0.025
    min_lookback_days = 31   # CRYPTO_MAX_EFFECT_FORMATION_DAYS + 1, see cross_sectional.py
    fractional_quantities = True   # coins are divisible; a 1,000 USDT book cannot hold whole BTC

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        # crypto_max_effect_percentile is injected by the caller (crypto_lane.py, via
        # simulate_portfolio()'s extra_columns_by_symbol) BEFORE precompute() runs -- see
        # swing_research/cross_sectional.py's compute_crypto_max_effect_percentile_ranks(). If
        # genuinely absent (e.g. a unit test not exercising the cross-sectional wiring), treat as
        # "not in the bottom quintile" rather than crashing.
        if "crypto_max_effect_percentile" not in df.columns:
            df["crypto_max_effect_percentile"] = float("nan")
        df["is_rebalance_day"] = pd.Series(df.index.weekday == REBALANCE_WEEKDAY, index=df.index)
        df["qualifies"] = df["is_rebalance_day"] & (df["crypto_max_effect_percentile"] <= BOTTOM_QUINTILE_PERCENTILE)
        df["qualifies_prev"] = df["qualifies"].shift(1).fillna(False)
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.qualifies) or bool(row.qualifies_prev) or not bool(row.qualifies):
            return None
        entry_price = float(row.Close)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=entry_price * (1 - STOP_LOSS_PCT),
            confidence=100.0 - float(row.crypto_max_effect_percentile), strategy_name=self.name,
            reason=(f"Trailing ~1-month MAX (single highest daily return) in the bottom quintile "
                    f"(percentile {row.crypto_max_effect_percentile:.1f}) on the weekly rebalance day"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if bool(row.is_rebalance_day) and entry_date is not None and (row.date - entry_date).days >= HOLDING_DAYS:
            return float(row.Close)
        return None
