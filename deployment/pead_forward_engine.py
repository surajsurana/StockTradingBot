"""
PEAD Forward Evidence Engine -- daily orchestration for SW-007's
forward-only paper-trading pipeline. NO historical backtest exists for
this strategy (Research Verdict remains NOT_YET_EVALUATED, unchanged --
see swing_research/strategy_library/pead.md and
swing_research/published_research_analyst.py's
POST_EARNINGS_ANNOUNCEMENT_DRIFT record). This module exists to collect
NEW, real-market evidence going forward, clearly distinguished from a
validated strategy.

REUSES EXISTING INFRASTRUCTURE THROUGHOUT (per explicit direction --
"reuse existing infrastructure wherever possible rather than creating
parallel systems"):
  - deployment/paper_trading_engine.py's own portfolio/trade/equity state
    functions (_load_portfolio, _save_portfolio, _append_trade,
    _append_daily_equity, compute_live_metrics, generate_report) --
    SAME state-file format and directory convention as every other
    paper-trading strategy, just under strategy_key="pead".
  - deployment/paper_trading_engine.py's run_daily() for the EXIT side
    (holding-period time-stop + protective stop), via
    swing_research/strategies/pead.py's entry-signal-always-None shim --
    the exit machinery itself is completely unmodified/untouched.
  - deployment/pead_signal.py for the SUE formula and threshold (pure
    functions, independently testable, never duplicated here).
  - data/fetch_earnings_calendar.py for the actual forward earnings data.

LOOKAHEAD-BIAS AVOIDANCE (per explicit direction): an earnings event is
NEVER acted on the same calendar day it is first detected. An event
detected on day D (meaning its announcement_date <= D) is only eligible
to become an entry starting on day D+1's close at the earliest -- by
which point at least one full trading day has definitely elapsed since
the announcement, regardless of whether the real announcement was before
or after that day's market close (yfinance's own announcement-time
timestamp is not trusted -- see fetch_earnings_calendar.py's own
docstring).

EVENT LOG (per explicit direction -- "records all relevant candidate
events, not only trades that were ultimately taken"): every earnings
event this engine examines is appended to
deployment/state/paper_trading/pead/events.jsonl, whether or not it
became a signal or a trade -- see _append_event() below for the exact
schema. A separate processed_events.json tracks which (symbol,
announcement_date) pairs have already been logged, so the same real-world
event is never double-counted across multiple daily runs within the same
lookback window.
"""

import json
import math
import os
from datetime import date as date_type, timedelta
from typing import Optional

from data.fetch_earnings_calendar import fetch_recent_earnings_events_chunked
from deployment.paper_trading_engine import (
    ExecutionRealismConfig,
    _load_portfolio,
    _save_portfolio,
    compute_live_metrics,
    generate_report,
    run_daily,
)
from deployment.pead_signal import PEAD_RISK_PCT_PER_UNIT, PEAD_STOP_LOSS_PCT, compute_sue, evaluate_pead_signal
from deployment.settings import PAPER_TRADING_STATE_DIR

STRATEGY_KEY = "pead"
EARNINGS_LOOKBACK_DAYS = 10   # how far back an announcement can be and still be considered "recent"


def _pead_state_dir() -> str:
    return os.path.join(PAPER_TRADING_STATE_DIR, STRATEGY_KEY)


def _events_path() -> str:
    return os.path.join(_pead_state_dir(), "events.jsonl")


def _processed_events_path() -> str:
    return os.path.join(_pead_state_dir(), "processed_events.json")


def _load_processed_events() -> set:
    path = _processed_events_path()
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {tuple(pair) for pair in json.load(f)}


def _save_processed_events(processed: set) -> None:
    path = _processed_events_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([list(pair) for pair in processed], f)


def _append_event(record: dict) -> None:
    path = _events_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_events(strategy_key: str = STRATEGY_KEY) -> list:
    """Public accessor -- every event ever logged, for future analysis
    (total detected, eligible, rejected-and-why, signaled, traded)."""
    path = _events_path()
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def run_pead_daily(symbols: list, as_of_date: Optional[date_type] = None, force: bool = False,
                    fetch_ohlcv_fn=None,
                    execution_config: Optional[ExecutionRealismConfig] = None) -> dict:
    """
    The idempotent daily PEAD runner -- call once per trading day, after
    market close, same cadence as every other paper-trading strategy.

    symbols: the eligible universe to SCAN FOR EARNINGS EVENTS (the frozen
    swing_research universe, same as every other strategy). Unlike the
    cross-sectional strategies (SW-002/003/008), PEAD does not rank across
    the universe, so it does not need every symbol's price history --
    only symbols that can actually need a price today: existing open
    positions (for the exit side) and today's candidate earnings-event
    symbols (for pricing a possible new entry). fetch_ohlcv_fn is called
    with just that small subset, keeping memory bounded regardless of
    universe size (discovered necessary after a full-universe 3y OHLCV
    preload -- previously done in run_paper_trading.py before calling
    this function -- exhausted memory on the production VPS).

    fetch_ohlcv_fn: callable(symbol_list) -> {symbol: DataFrame} OHLCV --
    needed for the EXIT side (run_daily() checks stop-loss/holding-period
    against real price bars) and for pricing a NEW entry at today's close.

    Returns a summary dict: {"status": ..., "events_detected": N,
    "events_eligible": N, "signals_generated": N, "new_entries": [...],
    "new_exits": [...], ...} -- open_positions/cash/mark_to_market_equity
    included via the underlying run_daily() result.
    """
    target_date = as_of_date or date_type.today()
    portfolio = _load_portfolio(STRATEGY_KEY)

    last_processed = portfolio.get("last_processed_date")
    if not force and last_processed is not None and date_type.fromisoformat(last_processed) >= target_date:
        return {"status": "skipped_already_processed", "as_of_date": target_date.isoformat(),
                "last_processed_date": last_processed}

    processed_events = _load_processed_events()
    raw_events = fetch_recent_earnings_events_chunked(symbols, target_date, lookback_days=EARNINGS_LOOKBACK_DAYS)

    events_detected = len(raw_events)
    events_eligible = 0
    signals_generated = 0
    injected_entries = []

    needed_symbols = sorted(set(portfolio["positions"].keys()) | {e.symbol for e in raw_events})
    data = fetch_ohlcv_fn(needed_symbols) if fetch_ohlcv_fn else {}

    for event in raw_events:
        key = (event.symbol, event.announcement_date.isoformat())
        if key in processed_events:
            continue   # already logged in a prior daily run -- do not double-count the same real event
        processed_events.add(key)

        # LOOKAHEAD GUARD (per explicit direction): never act the same
        # calendar day an event is first detected -- at least one full
        # trading day must have elapsed.
        if event.announcement_date >= target_date:
            _append_event({
                "detected_date": target_date.isoformat(), "symbol": event.symbol,
                "announcement_date": event.announcement_date.isoformat(),
                "reported_eps": event.reported_eps, "eps_estimate": event.eps_estimate,
                "sue": None, "eligible": False,
                "eligibility_reason": "Announcement detected same-day or in the future relative to this "
                                       "run -- deferred to a later run to avoid lookahead (no same-day action).",
                "signal_generated": False, "trade_taken": False,
            })
            processed_events.discard(key)   # NOT yet actually processed -- re-examine on a later run
            continue

        sue_result = compute_sue(event.trailing_actual_eps)
        signal_generated, signal_reason = evaluate_pead_signal(sue_result)

        already_held = event.symbol in portfolio["positions"]
        eligible = sue_result.sufficient_history and not already_held
        events_eligible += 1 if eligible else 0
        if signal_generated:
            signals_generated += 1

        trade_taken = False
        rejection_reason = None
        if already_held:
            rejection_reason = "Already holding a PEAD position in this symbol."
        elif not sue_result.sufficient_history:
            rejection_reason = sue_result.reason
        elif not signal_generated:
            rejection_reason = signal_reason
        else:
            # Real, eligible signal -- price the entry at TODAY's close
            # (target_date), the same convention every other strategy in
            # this program uses, and by construction at least one trading
            # day after the real announcement (the lookahead guard above).
            df = data.get(event.symbol)
            if df is None or df.empty:
                rejection_reason = "No price data available to price the entry."
            else:
                rows = df[df.index.date == target_date]
                if rows.empty:
                    rejection_reason = "No trading bar for this symbol today (holiday/delisting)."
                else:
                    entry_price = float(rows.iloc[0]["Close"])
                    stop_loss = entry_price * (1 - PEAD_STOP_LOSS_PCT)
                    risk_per_share = entry_price - stop_loss
                    quantity = math.floor(portfolio["cash"] * PEAD_RISK_PCT_PER_UNIT / risk_per_share)
                    cost = entry_price * quantity
                    if quantity >= 1 and cost <= portfolio["cash"]:
                        portfolio["cash"] -= cost
                        portfolio["positions"][event.symbol] = {
                            "entry_price": entry_price, "entry_date": target_date.isoformat(),
                            "quantity": quantity, "stop_loss": stop_loss,
                        }
                        injected_entries.append({"symbol": event.symbol, "entry_price": entry_price,
                                                  "quantity": quantity, "stop_loss": stop_loss,
                                                  "sue": sue_result.sue})
                        trade_taken = True
                    else:
                        rejection_reason = "Signal fired but position sized to zero or unaffordable."

        _append_event({
            "detected_date": target_date.isoformat(), "symbol": event.symbol,
            "announcement_date": event.announcement_date.isoformat(),
            "reported_eps": event.reported_eps, "eps_estimate": event.eps_estimate,
            "surprise_pct": event.surprise_pct, "sue": sue_result.sue,
            "eligible": eligible, "signal_generated": signal_generated,
            "trade_taken": trade_taken, "rejection_reason": rejection_reason,
        })

    _save_processed_events(processed_events)

    if injected_entries:
        _save_portfolio(STRATEGY_KEY, portfolio)

    # Exit side: reuse run_daily() UNCHANGED, with PEADStrategy's
    # entry_signal_at() always returning None (see swing_research/
    # strategies/pead.py) -- this call can only ever produce EXITS
    # (stop-loss / holding-period time-stop) on positions already in
    # `positions`, including the ones just injected above.
    from swing_research.strategies.pead import PEADStrategy
    strategy = PEADStrategy()
    daily_result = run_daily(STRATEGY_KEY, strategy, fetch_data_fn=lambda: data,
                              as_of_date=target_date, force=force, execution_config=execution_config)

    if daily_result["status"] != "processed":
        return daily_result

    daily_result["events_detected"] = events_detected
    daily_result["events_eligible"] = events_eligible
    daily_result["signals_generated"] = signals_generated
    # run_daily() only ever handles EXITS for PEAD (entry_signal_at is
    # always None, see swing_research/strategies/pead.py) -- its own
    # new_entries is always []. Report OUR entries (injected above,
    # before run_daily() was called) here instead.
    daily_result["new_entries"] = injected_entries
    return daily_result
