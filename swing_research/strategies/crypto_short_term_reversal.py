"""
Cryptocurrency Short-Term Reversal -- the crypto lane's first SHORT-horizon
reversal strategy (every other reversal strategy in this lane,
crypto_long_horizon_reversal.py, targets the longer ~2-month horizon where
Dobrynskaya (2023) finds crypto momentum switches sign; this candidate
targets the much shorter, Jegadeesh (1990)-style 1-month horizon instead).

Source: Kozlowski, S.E., Puleo, M.R. and Zhou, J. (2021), "Cryptocurrency
return reversals," Applied Economics Letters, Vol. 28, No. 11, 887-893;
Zaremba, A., Bilgin, M.H., Long, H., Mercik, A. and Szczygielski, J.J.
(2021), "Up or down? Short-term reversal, momentum, and liquidity effects
in cryptocurrency markets," International Review of Financial Analysis,
Vol. 78, 101908. Both document the SAME liquidity-provision mechanism
already registered for this program's India strategy short_term_reversal.py
(PASS, paper trading): Jegadeesh, N. (1990), "Evidence of Predictable
Behavior of Security Returns," The Journal of Finance, Vol. 45, No. 3.

*** CENTRAL DISCLOSED CAVEAT (see research_roadmap.py's known_weaknesses
field for this candidate) *** -- both source papers are explicit that the
reversal effect is CONCENTRATED IN SMALL, ILLIQUID coins, while the
largest/most liquid coins instead show MOMENTUM (the opposite sign). This
platform's own crypto universe (data/fetch_crypto.py's CRYPTO_UNIVERSE) is
deliberately the 40 largest, most liquid USDT pairs -- explicitly the
segment BOTH papers say shows momentum, not reversal. A REJECT, or even a
sign-flipped result, is itself a genuinely informative finding given this
disclosed tension, not a wasted research cycle -- this implementation does
not adjust the mechanism to try to force a PASS.

=========================== DOCUMENTED RULES ===========================

- Formation period: the source papers test daily, weekly AND monthly
  rebalancing and find the effect at all three.
- Cross-sectional sort by formation-period return; long the bottom group
  (prior-period losers) -- same disclosed long-only reduction as every
  candidate in this program, same side as the India strategy.
- This program's own India/US siblings (short_term_reversal.py,
  us_short_term_reversal candidate) use a 1-month formation/hold -- the
  candidate's own typical_holding_period field (research_roadmap.py) names
  this as "the natural starting specification here too," pending
  confirmation the effect is not concentrated at a shorter horizon on this
  platform's own universe (a further research question, not resolved by
  this implementation).

===================== IMPLEMENTATION ASSUMPTIONS =====================

1. LONG ONLY (past LOSERS) -- same disclosed long-only reduction as every
   strategy in this program. Estimated impact: DIRECTIONALLY UNKNOWN.
2. FORMATION PERIOD = 30 CALENDAR DAYS (~1 month), the SAME 21-TRADING-day
   -> 30-CALENDAR-day conversion already established for this lane's own
   crypto_idiosyncratic_volatility.py (CRYPTO_IVOL_FORMATION_DAYS), not a
   new number invented for this strategy -- crypto trades every day, so
   calendar days are bars. See swing_research/cross_sectional.py's
   CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS. Estimated impact: MINOR, a
   direct restatement of the India/US siblings' 1-month specification.
3. SINGLE-VINTAGE, DAILY STATE-TRANSITION ENTRY, not a monthly-batch
   reformation -- the DIRECT crypto analogue of the India short_term_
   reversal.py's own design (entry fires the day a coin's formation-return
   percentile first drops to <=10, not on a monthly rebalance calendar),
   not this lane's own month-end-gated crypto_long_horizon_reversal.py/
   crypto_illiquidity_premium.py/crypto_idiosyncratic_volatility.py design
   -- those three all gate to month-end specifically because each one's
   OWN formation window is LONGER than its own rebalance period; here the
   formation window (30 days) and the holding period (30 days) are the
   SAME length, exactly the India sibling's own relationship between its
   21-day formation and 21-day hold, so the India sibling's daily-
   transition design is the more faithful port, not the longer-horizon
   crypto strategies' batch design. Estimated impact: MODERATE-to-
   MATERIAL, DIRECTIONALLY UNKNOWN -- identical reasoning to the India
   sibling's own single-vintage deviation from the papers' overlapping-
   portfolio construction.
4. EXIT RULE: ONLY a 30-calendar-day time-stop OR the synthetic
   protective stop below, whichever comes first. Deliberately NO
   percentile-based early exit, same discipline as every prior strategy
   in this program (including the India sibling).
5. PROTECTIVE STOP-LOSS: 20% below entry -- NOT the India sibling's 8%,
   which this lane's own crypto_xs_momentum.py/crypto_tsmom.py/crypto_
   trend_timing*.py/crypto_illiquidity_premium.py/crypto_idiosyncratic_
   volatility.py/crypto_long_horizon_reversal.py already disclose is an
   equity-volatility number inappropriate for crypto's much higher daily
   volatility; 20% is reused from those strategies rather than inventing a
   new number. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source
   papers are factor-return studies with zero position-level risk
   management, same disclosed reason as the India sibling. Estimated
   impact: MODERATE, one-directional.
6. RANK THRESHOLD: bottom decile = formation-return percentile <=10 (the
   same convention as every other reversal strategy in this program, the
   inverse of every momentum strategy's >=90 threshold). Estimated
   impact: MINOR, a direct restatement of the documented rule.
7. POSITION SIZING: risk_pct_per_unit=0.025 (12.5% of the book per coin
   against the 20% stop) -- identical sizing convention to every other
   cross-sectional crypto strategy in this lane, not a new number invented
   for this strategy. Estimated impact: MINOR.
8. BOOK IN USDT, fractional quantities, Binance daily OHLCV -- same
   lane-wide convention as every other crypto strategy.
9. COSTS AND TAX (swing_research/crypto_costs.py): applied by
   crypto_lane.py's run_crypto_experiment_generic() exactly as for every
   other crypto strategy. This Strategy class itself produces ordinary
   pre-cost, pre-tax Signals. Estimated impact: MAJOR -- the lane's
   standing question.

Rebalance frequency: the 30-calendar-day formation-return percentile is
computed DAILY (via swing_research.cross_sectional.compute_crypto_short_
term_reversal_percentile_ranks), but entries only fire on the qualifying
state-transition day -- see point 3 above.
"""

from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS = 30  # ~1 month of calendar days -- see cross_sectional.py's
                                                 # CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS
REVERSAL_PERCENTILE_THRESHOLD = 10.0            # bottom decile (past LOSERS)
HOLDING_PERIOD_DAYS = 30                        # calendar days -- crypto trades every day, no
                                                 # trading-day-to-calendar-day conversion needed
STOP_LOSS_PCT = 0.20                            # crypto-scaled stop, same lane-wide convention


class CryptoShortTermReversalStrategy(Strategy):
    name = "crypto_short_term_reversal"
    max_units = 1                    # single-vintage, no pyramiding -- see module docstring point 3
    risk_pct_per_unit = 0.025
    fractional_quantities = True
    min_lookback_days = CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()

        # crypto_short_reversal_percentile is injected by the caller (crypto_lane.py, via
        # simulate_portfolio()'s extra_columns_by_symbol) BEFORE precompute() runs -- see
        # swing_research/cross_sectional.py's compute_crypto_short_term_reversal_percentile_ranks().
        # If genuinely absent (e.g. a unit test not exercising the cross-sectional wiring), treat
        # as "not in the bottom decile" rather than crashing -- NaN >= threshold is always False, so
        # this must be checked explicitly since we're testing <= here.
        if "crypto_short_reversal_percentile" not in df.columns:
            df["crypto_short_reversal_percentile"] = float("nan")

        df["qualifies"] = df["crypto_short_reversal_percentile"] <= REVERSAL_PERCENTILE_THRESHOLD
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
            confidence=100.0 - float(row.crypto_short_reversal_percentile),
            strategy_name=self.name,
            reason=(f"1-month formation return entered the bottom decile today "
                    f"(percentile {row.crypto_short_reversal_percentile:.1f}), did not qualify yesterday"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_DAYS:
            return float(row.Close)
        return None
