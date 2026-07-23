"""
EXP-001's strategy: Gap-and-Go with VWAP Hold Confirmation.

Proposed by the Quant Researcher (Claude), selected by the Research
Director's ranking over 1 other hard-filter survivor -- see
research_lab/pending_proposal.json (consumed by `run_experiment.py
--continue`) for the full hypothesis text and selection reasoning, and
research_lab/experiments/EXP-001/ once this has actually been backtested.

Mechanism: a stock gapping up on real (above-average) volume, whose price
never trades back down through its own intraday VWAP during or after the
opening 15 minutes, is read as a sign the overnight order-flow imbalance
is being absorbed by committed buyers rather than faded by retail
round-tripping. Enter on a breakout of the opening 15-minute high, stop at
the current VWAP (the level whose breach invalidates the whole thesis),
target one gap-size measured from the prior close.

Simplification stated explicitly: LONG-ONLY (gap-UP case only), even
though the hypothesis as proposed also covers a symmetric gap-down/short
case. Every other strategy in this project (swing and research_lab alike)
is long-only, and shorting -- while legitimate for real MIS intraday
trading in India, unlike CNC delivery -- has never been implemented or
exercised anywhere in this codebase. Adding it here would be new, untested
surface area rather than a direct translation of an existing pattern.
Revisit if this hypothesis is worth extending after a PASS verdict.

Needs multi-day context (research_lab/base.py's Strategy.generate_signal
context param, added specifically to support this hypothesis): prior
close (for the gap) and 20-day average first-15-minute volume (for the
volume filter) -- neither derivable from a single day's bars alone, unlike
the earlier (now-deleted) Opening Range Breakout prototype.
"""

from typing import Optional
import pandas as pd
from research_lab.base import Signal, Strategy


class GapAndGoVWAPStrategy(Strategy):
    name = "gap_and_go_vwap"

    def __init__(self, min_gap_pct: float = 1.0, volume_multiple: float = 1.5,
                 range_minutes: int = 15, candle_minutes: int = 5, target_gap_multiple: float = 1.0):
        self.min_gap_pct = min_gap_pct / 100
        self.volume_multiple = volume_multiple
        self.range_candles = max(1, range_minutes // candle_minutes)
        self.target_gap_multiple = target_gap_multiple

    def generate_signal(self, todays_bars_so_far: pd.DataFrame, context: Optional[dict] = None) -> Optional[Signal]:
        context = context or {}
        prior_close = context.get("prior_close")
        avg_first_15min_volume = context.get("avg_first_15min_volume_20d")
        if not prior_close or not avg_first_15min_volume or avg_first_15min_volume <= 0:
            return None  # not enough history to evaluate the gap/volume filter yet

        if len(todays_bars_so_far) <= self.range_candles:
            return None  # still inside the opening range -- nothing to evaluate yet

        opening_bar = todays_bars_so_far.iloc[0]
        gap_pct = (float(opening_bar["Open"]) - prior_close) / prior_close
        if gap_pct < self.min_gap_pct:
            return None  # no qualifying gap up (gap-down/short case out of scope, see module docstring)

        opening_range = todays_bars_so_far.iloc[: self.range_candles]
        first_15min_volume = float(opening_range["Volume"].sum())
        if first_15min_volume < avg_first_15min_volume * self.volume_multiple:
            return None  # gap without real volume behind it -- likely to fade

        typical_price = (todays_bars_so_far["High"] + todays_bars_so_far["Low"] + todays_bars_so_far["Close"]) / 3
        cum_vwap = (typical_price * todays_bars_so_far["Volume"]).cumsum() / todays_bars_so_far["Volume"].cumsum()
        # Compare each bar's Close against the PRIOR bar's VWAP (shifted) --
        # checking a bar's own Low against a VWAP that includes that same
        # bar's own contribution is self-referential and nearly always
        # fails on the very first bar (a bar's Low is almost always below
        # its own typical price). The first bar has no prior VWAP to have
        # violated, so it's excluded from the check, not treated as a fail.
        prior_vwap = cum_vwap.shift(1)
        vwap_held = bool((todays_bars_so_far["Close"].iloc[1:] >= prior_vwap.iloc[1:]).all())
        if not vwap_held:
            return None  # closed back through VWAP at some point -- thesis invalidated

        range_high = float(opening_range["High"].max())
        post_range = todays_bars_so_far.iloc[self.range_candles:]
        breakout_candles = post_range[post_range["Close"] > range_high]
        if breakout_candles.empty:
            return None

        first_breakout_time = breakout_candles.index[0]
        if todays_bars_so_far.index[-1] != first_breakout_time:
            return None  # breakout already happened on an earlier candle today -- don't re-signal

        entry_price = float(todays_bars_so_far.iloc[-1]["Close"])
        stop_loss = float(cum_vwap.iloc[-1])
        if stop_loss >= entry_price:
            return None  # invalid stop -- VWAP has drifted at or above current price

        gap_size = float(opening_bar["Open"]) - prior_close
        target = entry_price + self.target_gap_multiple * gap_size
        if target <= entry_price:
            return None

        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss, target=target,
            confidence=0.55, strategy_name=self.name,
            reason=f"Gap-and-go: {gap_pct:.2%} gap up on {first_15min_volume / avg_first_15min_volume:.1f}x "
                   f"avg first-15min volume, VWAP held throughout, broke opening range high "
                   f"({range_high:.2f}) at {first_breakout_time.strftime('%H:%M')}",
        )
