"""
EXP-007's strategy: VWAP Extension Exhaustion Fade.

Proposed by the Quant Researcher (Claude), selected by the Research
Director's ranking over 7 other survivors from a fresh proposal batch
(2026-09-07) explicitly informed by the cross-experiment review of
EXP-001/002/003/004/005/006's failures -- all six were REJECTed, and the
review's own top finding was that stacked conjunctive conditions were
starving sample sizes before a fair out-of-sample test was even possible
(see research_lab/knowledge_base.jsonl). This hypothesis was picked
specifically for being a single, well-specified, falsifiable trigger
rather than another multi-condition conjunction, and for being the
BEHAVIORAL OPPOSITE of EXP-001/002 (VWAP-hold CONTINUATION) -- trading
exhaustion/fade instead of absorption/continuation.

Mechanism: track each stock's live distance from its own intraday VWAP,
normalized by that stock's own volatility (ATR) so the threshold is
stock-specific, not a flat percentage. When that distance exceeds
1.5x the stock's own trailing 20-day typical intraday VWAP extension,
and the next two consecutive 5-minute candles fail to print a new
extreme (momentum decelerating -- the marginal chaser has run out), fade
the move back toward VWAP: VWAP-benchmarked execution algorithms (which
systematically buy below/sell above VWAP over the day) are read as the
mechanism pulling price back toward it once the retail/momentum chase
that pushed it away has stalled.

Needs two NEW multi-day context fields added specifically for this
hypothesis (research_lab/backtesting_engineer.py's _compute_day_context(),
2026-09-07): "atr_14d" (a 14-daily-bar ATR, derived from intraday bars
aggregated into synthetic daily OHLC -- no separate yfinance pull needed)
and "avg_max_vwap_extension_atr_20d" (the trailing 20-day average of each
day's own max |Close-VWAP| in atr_14d units -- the stock-specific "how
far does this normally get pulled from VWAP on an ordinary day" baseline
this hypothesis's threshold is defined relative to).

Simplifications stated explicitly:
- "Target: VWAP itself (exit on touch)" -- a genuinely dynamic,
  continuously-moving target the engine's fixed-target-per-trade design
  (see pdh_failed_breakout_fade.py's identical class of gap for its own
  hypothesis) can't express. Approximated as a FIXED target: VWAP's OWN
  VALUE at the moment of entry, not tracked dynamically thereafter. Not a
  claim of exactly replicating "exit on touch" bar-by-bar.
- "0.3 ATR beyond the extension's extreme" for the stop uses atr_14d, the
  SAME daily-bar ATR used to define the extension threshold itself --
  disclosed as one consistent volatility unit throughout, not a
  separately-calibrated intraday stop distance.
- "skip if extension coincides with scheduled results/news day": NOT
  implemented -- this program's research_lab has no earnings-calendar
  integration (that infrastructure exists only in the separate swing
  program, data/fetch_earnings_calendar.py, deliberately isolated from
  research_lab per this program's own isolation mandate). A genuine,
  disclosed scope gap, not silently dropped.
- "only trade top-20 liquidity names": not enforced inside this
  strategy -- a universe-selection concern, satisfied by the caller
  passing a 20-symbol slice of run_experiment.py's LIQUID_UNIVERSE, same
  convention as pdh_failed_breakout_fade.py's own top-30 filter.
"""

from typing import Optional
import pandas as pd
from research_lab.base import Signal, Strategy


class VwapExtensionExhaustionFadeStrategy(Strategy):
    name = "vwap_extension_exhaustion_fade"

    def __init__(self, extension_multiple: float = 1.5, stop_atr_buffer: float = 0.3):
        self.extension_multiple = extension_multiple
        self.stop_atr_buffer = stop_atr_buffer

    def generate_signal(self, todays_bars_so_far: pd.DataFrame, context: Optional[dict] = None,
                         market_state=None) -> Optional[Signal]:
        context = context or {}
        atr = context.get("atr_14d")
        avg_extension = context.get("avg_max_vwap_extension_atr_20d")
        if not atr or atr <= 0 or not avg_extension or avg_extension <= 0:
            return None  # not enough history to evaluate the ATR/extension baseline yet

        # Need a bar BEFORE the candidate extension bar (to confirm a fresh
        # transition into extended territory, not an already-extended
        # state) plus the extension bar itself plus 2 confirming bars.
        if len(todays_bars_so_far) < 4:
            return None

        typical_price = (todays_bars_so_far["High"] + todays_bars_so_far["Low"] + todays_bars_so_far["Close"]) / 3
        vwap = (typical_price * todays_bars_so_far["Volume"]).cumsum() / todays_bars_so_far["Volume"].cumsum()
        extension = (todays_bars_so_far["Close"] - vwap) / atr  # signed: + = above VWAP, - = below

        threshold = self.extension_multiple * avg_extension
        ext_now = float(extension.iloc[-3])     # the candidate "extension" bar
        ext_prior = float(extension.iloc[-4])   # the bar just before it

        if abs(ext_now) < threshold:
            return None  # candidate bar wasn't actually extended
        if abs(ext_prior) >= threshold:
            return None  # already extended the bar before -- not a fresh transition, avoid re-signaling

        direction_above = ext_now > 0
        extreme = (float(todays_bars_so_far["High"].iloc[-3]) if direction_above
                   else float(todays_bars_so_far["Low"].iloc[-3]))

        confirm_bars = todays_bars_so_far.iloc[-2:]   # the 2 bars after the extension bar
        if direction_above:
            if bool((confirm_bars["High"] > extreme).any()):
                return None  # still extending -- exhaustion not confirmed
        else:
            if bool((confirm_bars["Low"] < extreme).any()):
                return None

        entry_price = float(todays_bars_so_far.iloc[-1]["Close"])
        current_vwap = float(vwap.iloc[-1])

        if direction_above:
            direction = "SELL"
            stop_loss = extreme + self.stop_atr_buffer * atr
            target = current_vwap
            if stop_loss <= entry_price or target >= entry_price:
                return None
        else:
            direction = "BUY"
            stop_loss = extreme - self.stop_atr_buffer * atr
            target = current_vwap
            if stop_loss >= entry_price or target <= entry_price:
                return None

        return Signal(
            symbol="", direction=direction, entry_price=entry_price, stop_loss=stop_loss, target=target,
            confidence=0.55, strategy_name=self.name,
            reason=(f"VWAP extension exhaustion: {abs(ext_now):.2f} ATR vs {threshold:.2f} ATR threshold, "
                    f"2 bars without a new extreme, fading back toward VWAP ({current_vwap:.2f})"),
        )
