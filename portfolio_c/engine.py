"""
Portfolio C's candidate pipeline -- collecting today's anchor-strategy
signals and adapting them into the shape the agent stack (research/,
fundamentals/, news/, portfolio/, risk/) expects.

Deliberately does NOT read or write anything under
deployment/state/paper_trading/ -- candidates are recomputed independently
from the same read-only price data Portfolio A's own daily run uses, never
by reading Portfolio A's live results. This means Portfolio C's LLM calls
are extra API cost on top of Portfolio A's own daily run, not a free
byproduct of it -- an accepted, disclosed trade-off for keeping the two
portfolios structurally unable to affect each other.
"""

import datetime
from typing import Optional

from strategies.base import Signal as AgentSignal
from swing_research.base import Signal as SwingSignal
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS

# The two already-PASS Swing Research strategies Portfolio C tests the
# agent layer against -- see the design review's Part 3 recommendation
# ("anchor on a validated Swing strategy" over reviving a REJECT strategy
# or a fully autonomous scan). Confirmed 2026-09-01, per explicit
# direction, alongside Portfolio B (a separate, fixed-watchlist track,
# built after Portfolio C).
ANCHOR_STRATEGY_KEYS = ("max_effect", "short_term_reversal")

_SPECS_BY_KEY = {spec.strategy_key: spec for spec in PAPER_TRADING_STRATEGY_SPECS}

# How far above/below entry the adapted Signal's `target` is set, as a
# multiple of the strategy's own risk distance (entry - stop_loss). Swing
# Research strategies have no fixed profit target of their own -- they
# exit via their own exit_signal_at() (a signal-based, not price-based,
# exit) or the mechanical stop. strategies.base.Signal requires a
# `target` field, but nothing in this pipeline ever uses it to size or
# decide an exit -- Research Analyst's prompt only displays it, and
# Portfolio Manager/Risk Manager size purely off entry_price/stop_loss.
# A 2R target is a disclosed DISPLAY-ONLY convention, not a real profit
# target Portfolio C actually exits at.
DISPLAY_ONLY_TARGET_R_MULTIPLE = 2.0


def adapt_swing_signal(swing_signal: SwingSignal) -> AgentSignal:
    """
    Translates a swing_research.base.Signal (the anchor strategy's own
    entry proposal) into strategies.base.Signal, the shape
    research.research_analyst / portfolio.portfolio_manager /
    risk.risk_manager all expect. Confidence, symbol, direction,
    entry_price, stop_loss, strategy_name, and reason pass through
    unchanged -- only `target` (absent from swing_research.base.Signal)
    is derived, and only for display (see DISPLAY_ONLY_TARGET_R_MULTIPLE).
    """
    risk_per_share = (swing_signal.entry_price - swing_signal.stop_loss
                       if swing_signal.direction == "BUY"
                       else swing_signal.stop_loss - swing_signal.entry_price)
    offset = DISPLAY_ONLY_TARGET_R_MULTIPLE * risk_per_share
    target = (swing_signal.entry_price + offset if swing_signal.direction == "BUY"
              else swing_signal.entry_price - offset)

    return AgentSignal(
        symbol=swing_signal.symbol,
        direction=swing_signal.direction,
        entry_price=swing_signal.entry_price,
        stop_loss=swing_signal.stop_loss,
        target=target,
        confidence=swing_signal.confidence,
        strategy_name=swing_signal.strategy_name,
        reason=swing_signal.reason,
    )


def collect_anchor_candidates(data: dict, as_of_date: datetime.date,
                               anchor_strategy_keys=ANCHOR_STRATEGY_KEYS) -> dict:
    """
    Returns {symbol: {strategy_key: AgentSignal}} -- every symbol at least
    one anchor strategy proposes a NEW entry for on as_of_date, using the
    EXACT SAME precompute()/entry_signal_at() call sequence and
    compute_extra_columns_fn wiring deployment/paper_trading_engine.py's
    run_daily() uses for Portfolio A's own live run of these strategies
    (see swing_research/strategy_catalog.py's PAPER_TRADING_STRATEGY_SPECS)
    -- so Portfolio C sees the identical signal Portfolio A would see
    today, just recomputed independently rather than read from Portfolio
    A's own state.

    data: {symbol: OHLCV DataFrame}, most recent row last -- the same
    shape data/fetch_historical.py's fetch_all() returns.

    Does not know about, or filter by, any existing open position --
    that's Portfolio C's own isolated portfolio state's job (a later
    piece of this pipeline), not this collection step's.
    """
    candidates: dict = {}

    for strategy_key in anchor_strategy_keys:
        spec = _SPECS_BY_KEY[strategy_key]
        strategy = spec.strategy_factory()
        extra_columns = spec.compute_extra_columns_fn(data) if spec.compute_extra_columns_fn else None

        for symbol, df in data.items():
            if df is None or df.empty:
                continue
            frame = df.sort_index()
            if extra_columns and symbol in extra_columns:
                frame = frame.join(extra_columns[symbol])

            precomputed = strategy.precompute(frame)
            rows_on_date = precomputed[precomputed.index.date == as_of_date]
            if rows_on_date.empty:
                continue   # no bar for this symbol on as_of_date (holiday, delisting, etc.)
            row = list(rows_on_date.itertuples(index=False))[0]

            swing_signal = strategy.entry_signal_at(row)
            if swing_signal is None:
                continue
            swing_signal.symbol = symbol

            candidates.setdefault(symbol, {})[strategy_key] = adapt_swing_signal(swing_signal)

    return candidates
