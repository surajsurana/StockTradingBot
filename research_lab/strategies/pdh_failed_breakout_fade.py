"""
EXP-003's strategy: Prior-Day-High Failed-Breakout Exhaustion Fade.

Proposed by the Quant Researcher (Claude), selected by the Research
Director's ranking over 5 other survivors -- see
research_lab/pending_proposal.json / research_lab/experiments/EXP-003/
for the full hypothesis text and selection reasoning.

Mechanism: a stock pokes above its prior day's high (PDH) on a real
relative-volume spike -- drawing in momentum/breakout chasers -- but then
fails to hold above PDH for two consecutive 5-minute closes. Those late
longs are now trapped and forced to liquidate as price falls back through
the level, which is read as a self-reinforcing unwind (a trapped-trader
order-flow story, not a "breakout continues" bet -- directly the opposite
of every SEED-ORB/EXP-001/002 mechanism, per the Research Director's
2026-07-24 cross-experiment review).

This is this project's FIRST short-side strategy -- see
research_lab/backtesting_engineer.py's direction-agnostic refactor
(2026-07-24), built specifically to support this.

Simplifications stated explicitly (the hypothesis's exit design is more
dynamic than the current engine supports a fixed stop/target per trade):
- Target: the hypothesis says "cover at VWAP touch or prior day's close,
  whichever hits first" -- a genuinely dynamic two-level exit. Approximated
  here as a FIXED target chosen AT ENTRY TIME: whichever of (current VWAP,
  prior day's close) is numerically closer to entry, since the nearer
  level is the one more likely to be reached first under normal price
  movement. Not a claim of exactly replicating "whichever hits first"
  bar-by-bar.
- "Trail stop to breakeven once price clears VWAP": not implemented --
  the engine only supports a fixed stop per trade. Revisit if this
  hypothesis passes and is worth the extra engine work.
- "Top-30 liquid names" filter: not enforced inside this strategy -- it's
  a universe-selection concern, satisfied by the caller passing a
  30-symbol slice of run_experiment.py's LIQUID_UNIVERSE, not a per-symbol
  check here.
"""

from typing import Optional
import pandas as pd
from research_lab.base import Signal, Strategy


class PDHFailedBreakoutFadeStrategy(Strategy):
    name = "pdh_failed_breakout_fade"

    def __init__(self, volume_multiple: float = 1.5, poke_cutoff_hour: float = 11.0,
                 candle_minutes: int = 5):
        self.volume_multiple = volume_multiple
        self.poke_cutoff_hour = poke_cutoff_hour
        self.candle_minutes = candle_minutes

    def generate_signal(self, todays_bars_so_far: pd.DataFrame, context: Optional[dict] = None) -> Optional[Signal]:
        context = context or {}
        prior_high = context.get("prior_high")
        prior_close = context.get("prior_close")
        avg_volume_by_slot = context.get("avg_volume_by_slot_20d") or {}
        if not prior_high or not prior_close or not avg_volume_by_slot:
            return None  # not enough history to evaluate the PDH/volume filters yet

        if len(todays_bars_so_far) < 3:
            return None  # need at least a poke bar + 2 confirming bars

        # Find the FIRST bar (within the first 90 min, before poke_cutoff_hour)
        # whose High crossed above prior_high on a real relative-volume spike --
        # the "poke".
        poke_idx = None
        for i in range(len(todays_bars_so_far)):
            bar = todays_bars_so_far.iloc[i]
            bar_hour = todays_bars_so_far.index[i].hour + todays_bars_so_far.index[i].minute / 60
            if bar_hour > self.poke_cutoff_hour:
                break
            if float(bar["High"]) <= prior_high:
                continue
            avg_slot_volume = avg_volume_by_slot.get(i)
            if not avg_slot_volume or float(bar["Volume"]) < avg_slot_volume * self.volume_multiple:
                continue
            poke_idx = i
            break
        if poke_idx is None:
            return None  # no qualifying poke yet this morning

        # Need at least 2 CLOSED candles after the poke to confirm rejection,
        # plus one more bar to actually break the failure-candle low on.
        confirm_end = poke_idx + 2
        if len(todays_bars_so_far) <= confirm_end:
            return None  # rejection window not complete yet

        confirm_candles = todays_bars_so_far.iloc[poke_idx + 1: confirm_end + 1]
        if len(confirm_candles) < 2:
            return None
        rejection_confirmed = bool((confirm_candles["Close"] < prior_high).all())
        if not rejection_confirmed:
            return None  # price held above PDH -- no failed breakout, no signal

        failure_sequence = todays_bars_so_far.iloc[poke_idx: confirm_end + 1]
        failure_low = float(failure_sequence["Low"].min())
        poke_high = float(failure_sequence["High"].max())

        # Only fire on the FIRST bar (after the confirmed rejection window)
        # that actually breaks below failure_low -- once per day.
        post_confirm = todays_bars_so_far.iloc[confirm_end + 1:]
        if post_confirm.empty:
            return None
        breakdown_candles = post_confirm[post_confirm["Close"] < failure_low]
        if breakdown_candles.empty:
            return None
        first_breakdown_time = breakdown_candles.index[0]
        if todays_bars_so_far.index[-1] != first_breakdown_time:
            return None  # breakdown already happened on an earlier candle -- don't re-signal

        entry_price = float(todays_bars_so_far.iloc[-1]["Close"])
        stop_loss = poke_high
        if stop_loss <= entry_price:
            return None  # invalid stop for a short (must be above entry)

        typical_price = (todays_bars_so_far["High"] + todays_bars_so_far["Low"] + todays_bars_so_far["Close"]) / 3
        current_vwap = float(
            (typical_price * todays_bars_so_far["Volume"]).sum() / todays_bars_so_far["Volume"].sum()
        )
        # Whichever level is closer to entry is used as the fixed target --
        # see module docstring's stated simplification of the hypothesis's
        # dynamic "whichever hits first" exit design. Only choose among
        # candidates that are actually valid for a short (below entry) --
        # if price has already fallen through both reference levels by
        # entry time, there's no room left to target either one.
        candidates = [lvl for lvl in (current_vwap, prior_close) if lvl < entry_price]
        if not candidates:
            return None
        target = min(candidates, key=lambda lvl: abs(lvl - entry_price))

        return Signal(
            symbol="", direction="SELL", entry_price=entry_price, stop_loss=stop_loss, target=target,
            confidence=0.55, strategy_name=self.name,
            reason=f"PDH ({prior_high:.2f}) poke at bar {poke_idx} failed to hold (2-candle rejection "
                   f"confirmed), broke failure-sequence low ({failure_low:.2f}) at "
                   f"{first_breakdown_time.strftime('%H:%M')}, covering toward "
                   f"{'VWAP' if target == current_vwap else 'prior close'}",
        )
