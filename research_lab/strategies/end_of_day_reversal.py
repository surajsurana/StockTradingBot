"""
EXP-011's strategy: End-of-Day Reversal.

The first research_lab hypothesis sourced from the academic literature
rather than from the Quant Researcher's own proposals (per explicit
direction, 2026-09-07, after EXP-001 to EXP-010 -- five LLM-proposed
mechanisms -- all REJECTed): Baltussen, Da & Soebhag, "End-of-Day
Reversal" (2025; Erasmus / Notre Dame / Robeco). Their finding: an
individual stock's return from the PRIOR close to 30 minutes before
today's close ("ROD3") strongly and NEGATIVELY predicts its return over
the last 30 minutes, in the cross-section. US stocks 1993-2019, quintile
sorts, t-statistics above 10; a long-losers / short-winners portfolio
held only for the last half hour earns 3.78 bps/day value-weighted
(~9.5%/yr) and 6.86 bps/day equal-weighted (~17.3%/yr), present in
almost every 3-year window and within the largest, most liquid stocks
(3.41 bps/day). Mechanism: attention-driven retail buying of the day's
losers, plus short-sellers closing out risk before the close. It is
distinct from (and opposite in sign to) market-level intraday momentum
(Gao/Han/Li/Zhou 2018).

Rule, as implemented here:
- At the last 5-minute bar before 15:00 (the 14:55 candle, whose close
  is the ~15:00 price), compute every stock's ROD3 = close / prior
  day's close - 1, via MarketState.return_since_prior_close.
- Bottom quintile of the universe: BUY. Top quintile: SELL (intraday
  short). Everything in between: no trade.
- Hold to the mandatory EOD square-off (the 15:25 candle's close,
  ~15:30) -- the engine's own last-bar exit IS the paper's exit.

Why this is structurally unlike the ten REJECTs before it: a
cross-sectional RANK at a fixed time of day, traded every day across the
whole universe -- no price-level breakout or fade, no N-bar confirmation
(the review's recurring lag/starvation failure), no correlation gate
(EXP-010's sample-killer). Sample size is large by construction: ~40% of
the universe trades every single day.

Simplifications stated explicitly:
- The paper holds with NO stop or target -- a pure 30-minute hold. The
  engine sizes every position off its stop distance, so a stop is set
  5% away (and a target 5% away, symmetric): in 30 minutes on a liquid
  large-cap that practically never fires, so the EOD square-off decides
  every exit. A fixed-percentage stop also makes every position the
  SAME notional (capital x risk_pct / 5%), i.e. the paper's
  equal-weighted portfolio, rather than a stop-distance-varying size.
- Quintiles are of THIS lab's ~150-name liquid universe (run_experiment.py's
  LIQUID_UNIVERSE), not the whole market. The paper reports the effect
  is weakest (though still significant) in the largest stocks -- this
  universe is large-cap-heavy, so expect the smaller end of the range.
- The paper skips the second-to-last half hour (14:30-15:00) when
  forming ROD3 to rule out bid-ask bounce; here ROD3 runs to 15:00,
  the "including SLH" variant the authors report is equally robust.
- This is the first experiment judged NET of transaction costs
  (research_lab/transaction_costs.py, applied by run_experiment.py by
  default): the paper's own gross edge is of the same order as an Indian
  retail round-trip (~11 bps + spread on a Rs.50k position), and the
  authors say as much. A gross PASS here would mean nothing.
"""

from datetime import time
from typing import Optional

import pandas as pd

from research_lab.base import Signal, Strategy


class EndOfDayReversalStrategy(Strategy):
    name = "end_of_day_reversal"

    def __init__(self, entry_window_start: time = time(14, 55), entry_window_end: time = time(15, 0),
                 quintile: float = 0.2, stop_pct: float = 0.05, target_pct: float = 0.05,
                 min_universe: int = 25):
        self.entry_window_start = entry_window_start
        self.entry_window_end = entry_window_end
        self.quintile = quintile
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.min_universe = min_universe

    def generate_signal(self, todays_bars_so_far: pd.DataFrame, context: Optional[dict] = None,
                         market_state=None) -> Optional[Signal]:
        if market_state is None or context is None or todays_bars_so_far.empty:
            return None
        ts = todays_bars_so_far.index[-1]
        if not (self.entry_window_start <= ts.time() < self.entry_window_end):
            return None
        prior_close = context.get("prior_close")
        if prior_close is None or prior_close <= 0:
            return None
        universe = list(market_state.return_since_prior_close.values())
        if len(universe) < self.min_universe:
            return None

        close = float(todays_bars_so_far.iloc[-1]["Close"])
        own_rod = (close / prior_close - 1) * 100
        # Own rank as the fraction of the universe strictly below us -- no
        # need to know our own symbol, just where our value sits.
        rank = sum(1 for r in universe if r < own_rod) / len(universe)

        if rank < self.quintile:
            direction = "BUY"      # intraday loser -> expect end-of-day price pressure upward
            stop, target = close * (1 - self.stop_pct), close * (1 + self.target_pct)
        elif rank >= 1 - self.quintile:
            direction = "SELL"     # intraday winner -> expect reversal
            stop, target = close * (1 + self.stop_pct), close * (1 - self.target_pct)
        else:
            return None
        return Signal(symbol="", direction=direction, entry_price=close, stop_loss=stop, target=target,
                      confidence=0.5, strategy_name=self.name,
                      reason=f"ROD3 {own_rod:+.2f}% is at rank {rank:.2f} of {len(universe)} at {ts.time()}")
