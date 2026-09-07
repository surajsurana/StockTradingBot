"""
EXP-009's strategy: Intra-Sector Pair Spread Reversion.

Proposed by the Quant Researcher (Claude), selected by the Research
Director's ranking over 5 other survivors from a fresh batch (2026-09-07)
explicitly informed by the cross-experiment review of EXP-001 through
EXP-008 -- all eight REJECTed. That review's own headline conclusion was
that every prior hypothesis, continuation or fade, long or short, was a
single-stock DIRECTIONAL bet drawn from the same 5-minute OHLCV data, and
none survived out-of-sample. This one was picked for being the first
genuinely different trade TYPE: market-neutral (long one stock, short its
correlated same-sector peer in equal notional), so market and sector
direction cancel and only the relative divergence is traded.

Mechanism: for each pre-selected same-sector pair (research_lab/
pairs_candidates.py), track the live intraday price ratio Close_A/Close_B
against its own trailing-20-day baseline mean and standard deviation.
When the ratio's z-score reaches +/-2.0 -- a single-bar trigger, no
confirmation delay (the review flagged N-bar confirmation as a recurring
lag/sample-starvation failure) -- buy the leg that has fallen behind and
short the leg that has run ahead. Exit when the z-score reverts to within
0.5 of zero (target), widens further to 3.5 on the entry's own side
(stop), or at the mandatory EOD square-off, both legs together. A pair is
only eligible on days its trailing-60-day daily-close-return correlation
exceeds 0.8.

Runs through research_lab/pairs_simulator.py (the pairs engine built for
this experiment), invoked via `python run_experiment.py --continue --pairs`.

Simplifications stated explicitly:
- "AND the sector index itself has not moved commensurately": NOT checked
  against real sector-index data (this lab's fetchers have no NSE sector
  index feed). Instead it relies on the ratio's own construction -- a
  move common to both legs (the whole sector up 5%) leaves Close_A/
  Close_B essentially unchanged, so a sector-wide move cannot by itself
  push the z-score to 2.0; only a divergence BETWEEN the two legs can.
  The rule's intent (trade idiosyncratic divergence, not sector news) is
  therefore embedded in the signal rather than tested separately. A
  disclosed approximation, not an exact implementation.
- The 20-day baseline pools every intraday bar across the trailing 20
  trading days (not time-of-day-matched) -- the same simplification
  already used for avg_max_vwap_extension_atr_20d.
- Correlation is on daily-close % returns (yfinance, via
  data/fetch_historical.py), not on intraday bars and not on price
  levels; unavailable correlation means ineligible (fails closed).
- The stop is direction-aware ("widens further" is read literally):
  a one-bar swing through zero to the opposite extreme is treated as the
  spread having reverted past the target, not as a stop.
- Entry is level-based (|z| >= 2.0 on this bar), not strictly a
  below-to-above transition -- a pair that opens already diverged
  (overnight gap between the two) is tradeable on its first bar rather
  than requiring the crossing to be observed intraday.
- "Equal notional" is capital_per_pair split 50/50, each leg rounded
  down to whole shares, so the two legs' notional differs slightly.
"""

from typing import Optional

import pandas as pd

from research_lab.pairs_base import PairSignal, PairStrategy


class IntraSectorPairSpreadReversionStrategy(PairStrategy):
    name = "intra_sector_pair_spread_reversion"

    def __init__(self, entry_zscore: float = 2.0, stop_zscore: float = 3.5, target_zscore: float = 0.5):
        self.entry_zscore = entry_zscore
        self.stop_zscore = stop_zscore
        self.target_zscore = target_zscore

    def generate_pair_signal(self, bars_a_so_far: pd.DataFrame, bars_b_so_far: pd.DataFrame,
                              spread_context: Optional[dict] = None) -> Optional[PairSignal]:
        if spread_context is None or not spread_context.get("correlation_ok"):
            return None
        mean, std = spread_context.get("baseline_mean"), spread_context.get("baseline_std")
        if mean is None or std is None or std <= 0:
            return None
        if bars_a_so_far.empty or bars_b_so_far.empty:
            return None

        ratio = float(bars_a_so_far.iloc[-1]["Close"]) / float(bars_b_so_far.iloc[-1]["Close"])
        z = (ratio - mean) / std
        if abs(z) < self.entry_zscore:
            return None

        # Ratio rich (z > 0): A has run ahead of B -> A is the leader -> short A, long B.
        long_leg = "b" if z > 0 else "a"
        return PairSignal(
            long_leg=long_leg, entry_zscore=z, stop_zscore=self.stop_zscore,
            target_zscore=self.target_zscore, confidence=0.5, strategy_name=self.name,
            reason=f"ratio z={z:+.2f} vs 20d baseline {mean:.4f} +/- {std:.4f} "
                   f"(corr {spread_context.get('correlation'):.2f})",
        )
