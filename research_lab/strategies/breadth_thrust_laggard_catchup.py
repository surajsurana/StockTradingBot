"""
EXP-004's strategy: Breadth-Thrust Laggard Catch-Up.

Proposed by the Quant Researcher (Claude), selected by the Research
Director's ranking over 7 other survivors (see
research_lab/pending_proposal.json for the full batch and selection
reasoning) as the top-ranked hypothesis specifically BECAUSE it uses
market breadth as its primary entry trigger -- something no prior
experiment had used, and something the single-symbol backtesting engine
could not evaluate at all. Suraj explicitly directed (2026-07-25) that the
lab invest in proper cross-sectional infrastructure
(research_lab/market_state.py + research_lab/market_simulator.py) rather
than pick a weaker, easier-to-backtest hypothesis instead. This strategy
is the first to actually use the market_state param.

Mechanism: by 10:30, if broad market breadth shows a bullish thrust
(>70% of the universe trading above its own VWAP) while Nifty itself is
up on the day, screen for LAGGARD stocks -- still below their own VWAP --
whose own trailing 15-minute return already exceeds Nifty's trailing
15-minute return (an early relative-strength turn, before the price
action itself confirms it). Enter long the moment that laggard reclaims
its own VWAP. Mirrors symmetrically for a bearish thrust (<30% breadth,
Nifty down on the day): short laggards still above VWAP that are losing
relative strength, on VWAP loss.

Only fires once market_state.breadth_pct_above_vwap AND
market_state.nifty_return_since_open_pct AND
market_state.nifty_return_last_15min_pct are all available -- i.e. only
ever produces a signal when actually run through
research_lab/market_simulator.py's cross-sectional engine (market_state
is always None through the single-symbol backtesting_engineer.py engine,
per research_lab/base.py's Strategy.generate_signal docstring).

Simplifications stated explicitly:
- Target: the hypothesis says "average return of the day's already-moved
  leaders or fixed R-multiple" -- the first option requires knowing which
  of TODAY's leaders were the actual triggers for the breadth thrust,
  which market_state doesn't track historically (it's a snapshot, not a
  log). Uses the stated fallback: a fixed R-multiple (target_r_multiple,
  default 1.5x risk) instead.
- "Losing relative strength" for the bearish mirror is read as: the
  stock's own trailing 15-min return is WORSE (more negative, i.e. lower)
  than Nifty's trailing 15-min return -- the direct mirror of the bullish
  case's "exceeds" comparison.
- Reclaim/loss of VWAP is detected as a same-bar transition (previous bar
  closed on the "wrong" side of its own VWAP, this bar closes on the
  "right" side) -- same convention as gap_and_go_vwap.py's VWAP-hold
  check and pdh_failed_breakout_fade.py's rejection-confirmation logic,
  just inverted (reclaim, not hold).
- min_bar_hour=10.5 (the hypothesis's own "by 10:30" cutoff) gates when
  the thrust condition is even evaluated, so a strong breadth reading in
  the first few minutes of trading (still noisy, low sample) can't
  trigger a signal.
"""

from typing import Optional
import pandas as pd
from research_lab.base import Signal, Strategy
from research_lab.market_state import MarketState, return_over_lookback_minutes_pct


class BreadthThrustLaggardCatchUpStrategy(Strategy):
    name = "breadth_thrust_laggard_catchup"

    def __init__(self, breadth_thrust_threshold: float = 70.0, breadth_break_threshold: float = 30.0,
                 min_bar_hour: float = 10.5, recent_window_minutes: int = 15, target_r_multiple: float = 1.5):
        self.breadth_thrust_threshold = breadth_thrust_threshold
        self.breadth_break_threshold = breadth_break_threshold
        self.min_bar_hour = min_bar_hour
        self.recent_window_minutes = recent_window_minutes
        self.target_r_multiple = target_r_multiple

    def generate_signal(self, todays_bars_so_far: pd.DataFrame, context: Optional[dict] = None,
                         market_state: Optional[MarketState] = None) -> Optional[Signal]:
        if market_state is None or market_state.breadth_pct_above_vwap is None:
            return None  # only ever fires through the cross-sectional engine
        if market_state.nifty_return_since_open_pct is None or market_state.nifty_return_last_15min_pct is None:
            return None  # no Nifty data supplied to this run -- can't evaluate the thrust/RS conditions

        if len(todays_bars_so_far) < 4:
            return None  # need at least a few bars for a meaningful own-VWAP and 15-min-return read

        last_ts = todays_bars_so_far.index[-1]
        bar_hour = last_ts.hour + last_ts.minute / 60
        if bar_hour < self.min_bar_hour:
            return None  # breadth thrust must be an established, not opening-noise, reading

        breadth = market_state.breadth_pct_above_vwap
        bullish_thrust = breadth > self.breadth_thrust_threshold and market_state.nifty_return_since_open_pct > 0
        bearish_thrust = breadth < self.breadth_break_threshold and market_state.nifty_return_since_open_pct < 0
        if not bullish_thrust and not bearish_thrust:
            return None

        stock_recent_return = return_over_lookback_minutes_pct(todays_bars_so_far, self.recent_window_minutes)
        if stock_recent_return is None:
            return None  # not enough same-day history yet for a full lookback window

        typical_price = (todays_bars_so_far["High"] + todays_bars_so_far["Low"] + todays_bars_so_far["Close"]) / 3
        cum_vwap = (typical_price * todays_bars_so_far["Volume"]).cumsum() / todays_bars_so_far["Volume"].cumsum()
        current_vwap = float(cum_vwap.iloc[-1])
        prior_vwap = float(cum_vwap.iloc[-2])
        current_close = float(todays_bars_so_far.iloc[-1]["Close"])
        prior_close = float(todays_bars_so_far.iloc[-2]["Close"])

        if bullish_thrust:
            if stock_recent_return <= market_state.nifty_return_last_15min_pct:
                return None  # no early RS turn yet -- laggard isn't showing relative strength
            reclaimed = prior_close < prior_vwap and current_close >= current_vwap
            if not reclaimed:
                return None  # not the VWAP-reclaim bar

            entry_price = current_close
            stop_loss = float(todays_bars_so_far["Low"].min())
            if stop_loss >= entry_price:
                return None
            risk = entry_price - stop_loss
            target = entry_price + self.target_r_multiple * risk
            direction = "BUY"
            trigger_desc = "reclaimed"
        else:
            if stock_recent_return >= market_state.nifty_return_last_15min_pct:
                return None  # not losing relative strength relative to Nifty yet
            lost = prior_close > prior_vwap and current_close <= current_vwap
            if not lost:
                return None  # not the VWAP-loss bar

            entry_price = current_close
            stop_loss = float(todays_bars_so_far["High"].max())
            if stop_loss <= entry_price:
                return None
            risk = stop_loss - entry_price
            target = entry_price - self.target_r_multiple * risk
            direction = "SELL"
            trigger_desc = "lost"

        return Signal(
            symbol="", direction=direction, entry_price=entry_price, stop_loss=stop_loss, target=target,
            confidence=0.5, strategy_name=self.name,
            reason=f"Breadth {breadth:.1f}% ({'bullish' if bullish_thrust else 'bearish'} thrust), Nifty "
                   f"{'up' if bullish_thrust else 'down'} {market_state.nifty_return_since_open_pct:.2f}% "
                   f"since open, laggard {trigger_desc} own VWAP ({current_vwap:.2f}) at "
                   f"{last_ts.strftime('%H:%M')} with {self.recent_window_minutes}min return "
                   f"{stock_recent_return:.2f}% vs Nifty's {market_state.nifty_return_last_15min_pct:.2f}%",
        )
