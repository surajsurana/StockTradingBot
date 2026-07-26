"""
Cross-sectional Market State -- a generic, reusable snapshot of the WHOLE
universe's condition at a single point in time, computed once per bar and
shared across every symbol's strategy call that bar. Built to unblock
EXP-004 (Breadth-Thrust Laggard Catch-Up), but deliberately designed as a
foundation for any future cross-sectional hypothesis (breadth, sector
rotation, relative strength, leader/laggard screens) -- not a one-off
breadth-only implementation. Per Suraj's explicit request (2026-07-25):
"support not only the current hypothesis but an entire class of future
strategies without further architectural redesign."

Deterministic, pure computation -- no Claude calls, no randomness, no
lookahead (only ever computed from bars up to and including "now").
Entirely within research_lab -- never imported by, or importing from, the
swing production package.
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class MarketState:
    timestamp: object
    breadth_pct_above_vwap: Optional[float]      # % of universe currently above its own VWAP (0-100)
    sector_breadth_pct_above_vwap: dict = field(default_factory=dict)   # {sector: pct_above_vwap}
    relative_strength: dict = field(default_factory=dict)               # {symbol: % return since today's open}
    leaders: list = field(default_factory=list)      # [symbol, ...] top N by relative_strength, best first
    laggards: list = field(default_factory=list)      # [symbol, ...] bottom N by relative_strength, worst last
    nifty_return_since_open_pct: Optional[float] = None
    nifty_return_last_15min_pct: Optional[float] = None  # trailing 15-min return, distinct from since-open --
                                                           # added for EXP-004's "beats Nifty's recent momentum"
                                                           # check, but a generically reusable stat (see
                                                           # return_over_lookback_minutes_pct below), not
                                                           # EXP-004-specific.
    universe_size: int = 0                             # how many symbols had valid data this bar


def _vwap_so_far(bars: pd.DataFrame) -> Optional[float]:
    if bars is None or bars.empty or float(bars["Volume"].sum()) <= 0:
        return None
    typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
    return float((typical * bars["Volume"]).sum() / bars["Volume"].sum())


def _return_since_open_pct(bars: pd.DataFrame) -> Optional[float]:
    if bars is None or bars.empty:
        return None
    open_price = float(bars.iloc[0]["Open"])
    if open_price == 0:
        return None
    return (float(bars.iloc[-1]["Close"]) - open_price) / open_price * 100


def return_over_lookback_minutes_pct(bars: pd.DataFrame, lookback_minutes: int = 15) -> Optional[float]:
    """
    % return over the trailing `lookback_minutes`, ending at bars' last
    timestamp -- distinct from _return_since_open_pct (always measured
    from the day's open). Public (no leading underscore, unlike this
    module's other helpers): used both internally for
    nifty_return_last_15min_pct AND directly by any strategy that needs
    its OWN symbol's recent-momentum return (that part is never
    cross-sectional -- a strategy already has its own bars locally, it
    only needs Nifty's equivalent shared via MarketState).

    Finds the last bar at or before (last_timestamp - lookback_minutes)
    and uses its Close as the window start -- works regardless of candle
    interval (5min/15min/etc) rather than hardcoding a bar count. Returns
    None if there's no bar that far back yet today (too early in the
    session for a full lookback window).
    """
    if bars is None or bars.empty:
        return None
    cutoff = bars.index[-1] - pd.Timedelta(minutes=lookback_minutes)
    prior = bars[bars.index <= cutoff]
    if prior.empty:
        return None
    start_price = float(prior.iloc[-1]["Close"])
    if start_price == 0:
        return None
    return (float(bars.iloc[-1]["Close"]) - start_price) / start_price * 100


def compute_market_state(bars_so_far_by_symbol: dict, sector_map: dict, timestamp,
                          nifty_bars_so_far: Optional[pd.DataFrame] = None,
                          leader_laggard_n: int = 5) -> MarketState:
    """
    bars_so_far_by_symbol: {symbol: DataFrame of TODAY's bars up to and
    including `timestamp`} for every symbol with data at this bar (symbols
    with no bar yet this timestamp should simply be omitted by the caller,
    not passed as empty/None -- see market_simulator.py). Pure function --
    no lookahead, since it only ever sees what the caller passes in.
    """
    above_vwap_count = 0
    valid_count = 0
    sector_counts = {}   # sector -> [above_count, total_count]
    relative_strength = {}

    for symbol, bars in bars_so_far_by_symbol.items():
        if bars is None or bars.empty:
            continue
        ret = _return_since_open_pct(bars)
        if ret is not None:
            relative_strength[symbol] = ret

        vwap = _vwap_so_far(bars)
        if vwap is None:
            continue
        valid_count += 1
        is_above = float(bars.iloc[-1]["Close"]) > vwap
        if is_above:
            above_vwap_count += 1
        sector = sector_map.get(symbol, "Unknown")
        counts = sector_counts.setdefault(sector, [0, 0])
        counts[1] += 1
        if is_above:
            counts[0] += 1

    breadth_pct = (above_vwap_count / valid_count * 100) if valid_count > 0 else None
    sector_breadth = {sec: (above / total * 100) for sec, (above, total) in sector_counts.items() if total > 0}

    ranked = sorted(relative_strength.items(), key=lambda kv: kv[1], reverse=True)  # best first
    leaders = [sym for sym, _ in ranked[:leader_laggard_n]]
    laggard_count = min(leader_laggard_n, len(ranked))
    laggards = [sym for sym, _ in ranked[-laggard_count:]] if laggard_count > 0 else []

    nifty_return = _return_since_open_pct(nifty_bars_so_far) if nifty_bars_so_far is not None else None
    nifty_return_15min = (return_over_lookback_minutes_pct(nifty_bars_so_far, 15)
                           if nifty_bars_so_far is not None else None)

    return MarketState(
        timestamp=timestamp, breadth_pct_above_vwap=breadth_pct,
        sector_breadth_pct_above_vwap=sector_breadth, relative_strength=relative_strength,
        leaders=leaders, laggards=laggards, nifty_return_since_open_pct=nifty_return,
        nifty_return_last_15min_pct=nifty_return_15min, universe_size=valid_count,
    )
