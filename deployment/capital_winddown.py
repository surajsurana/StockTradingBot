"""
Capital Wind-Down (added 2026-08-23, per explicit direction) -- a
SEPARATE layer on top of deployment/paper_trading_engine.py that brings
each strategy's own idle cash down toward a target "actively deployable
capital" level (deployment/settings.py's PAPER_TRADING_WINDDOWN_TARGET_CAPITAL,
Rs.1,00,000 by default), without ever:
  - touching cash reserved for currently-queued (already-pending, from a
    prior day) next-session entries -- compute_winddown_withdrawal()
    hard-caps every withdrawal at idle_cash (cash minus reserved),
    regardless of PAPER_TRADING_WINDDOWN_DAILY_FRACTION's aggressiveness,
  - force-selling any open position,
  - changing signal generation, position sizing FORMULA, or exit logic at
    all (see run_paper_trading.py's sizing_capital_cap for the separate,
    2026-08-24 fix that caps what a signal's SIZE can be, independent of
    this module),
  - rewriting historical trades, equity, or performance metrics.

POLICY CHANGE (2026-08-26, per explicit direction): a BRAND-NEW signal
(one that wasn't already pending/reserved before today's withdrawal) is
NO LONGER guaranteed to fill -- once wind-down has aggressively swept
cash down near target (PAPER_TRADING_WINDDOWN_DAILY_FRACTION=0.90 by
default), a later same-day signal can find genuinely insufficient real
cash and be skipped entirely (deployment/paper_trading_engine.py's
existing `quantity < 1 or cost > cash` skip path, unchanged code, simply
more often reachable now). This is intentional: a strategy operating at
a small, finite capital pool sometimes can't take every signal, exactly
as it wouldn't be able to with real money -- confirmed as an accepted
trade-off, not a bug. Nothing about the RESERVATION guarantee above
changes: an entry already queued from a prior day is still always
protected.

This module NEVER modifies swing_research/ or the entry/exit logic in
deployment/paper_trading_engine.py's run_daily(). It only ever reads and
writes ONE field of a strategy's own portfolio.json (`cash`), and its
own append-only log file, both entirely separate from the trade/equity
history those other modules already own.

============================= HOW IT WORKS =============================

Called ONCE per strategy, per daily cycle, strictly AFTER that day's
run_daily()/run_pead_daily() call has fully completed (exits processed,
new signals detected and queued) -- see run_paper_trading.py's
_run_one(). At that point, portfolio["pending_entries"] holds EVERY
entry still awaiting resolution at a future day's real market Open
(both leftover-from-earlier-days and newly-queued-today), and
portfolio["cash"] is that day's final, post-exit, post-new-signal cash
figure.

1. RESERVE: for every pending entry, estimate what it will likely cost
   to actually fill (see estimate_reservation() below), using the EXACT
   SAME sizing formula deployment/paper_trading_engine.py's own
   _resolve_pending_fills() will use tomorrow, applied here as a
   simulation against a SEPARATE working copy of cash (never mutates
   the real portfolio while estimating). Each entry's estimated cost is
   multiplied by 1.05 (a fixed 5% buffer, per explicit spec) to protect
   against the overnight gap between today's signal_price -- the best
   price estimate available today, since real tomorrow's Open is by
   definition unknown -- and the real fill.
2. IDLE CASH: cash minus the total reserved amount. Never negative in
   effect -- see compute_winddown_withdrawal()'s own guard.
3. WITHDRAW: a bounded FRACTION (PAPER_TRADING_WINDDOWN_DAILY_FRACTION)
   of today's excess-over-target, capped at idle cash, so the wind-down
   is gradual (not a single shock reduction) and NEVER touches the
   reserved amount.

The reservation is a NON-BINDING CALCULATION, never an actual transfer
into some separate reserved-funds ledger -- the reserved amount always
stays sitting in `cash`, untouched, exactly where run_daily()'s own real
resolution logic will find it tomorrow. This is what makes "unused
reservation buffer is released after execution" true by construction:
there is no separate bucket to release FROM. If tomorrow's real fill
costs less than estimated (a smaller quantity, or the position is
abandoned because the real gap moved through the stop), whatever's left
in `cash` was simply never touched by this module in the first place.

CONFIRMED DESIGN DECISION (2026-08-23, per explicit direction, after
this exact tension was raised for review): the EXISTING, frozen fill
formula (_resolve_pending_fills()) sizes every entry as floor(cash x
risk_pct / risk_distance) -- proportional to the WHOLE remaining cash
pool, not a dollar amount carved out for one specific entry. Reserving
guarantees a queued entry is NEVER SKIPPED OR REJECTED (there is always
enough cash left overall), but it does NOT guarantee that entry's
QUANTITY is identical to what it would have been had no withdrawal ever
happened -- a smaller cash pool naturally sizes smaller future
positions, which was confirmed as the intended, accepted behavior
(the alternative -- pausing wind-down entirely on any day with a pending
entry -- was explicitly declined as almost certainly too slow, given how
often these strategies signal). See test_capital_winddown.py's
test_queued_entry_is_never_skipped_or_rejected_by_winddown() for the
concrete guarantee this actually provides.

===================== WHY PERFORMANCE METRICS ARE SAFE =====================

A cash withdrawal is a real, deliberate capital-management action, not a
trading result -- so it must never be misread as a trading LOSS by
CAGR/Sharpe/Sortino/MaxDrawdown (deployment/paper_trading_engine.py's
compute_live_metrics(), which reads a raw cash-vs-date curve) or by the
newer daily_pnl field (run_daily()'s own day-over-day equity delta).
Both of those are made capital-flow-aware by ADDING BACK the cumulative
amount withdrawn as of each date -- exactly the standard, real-fund
convention of excluding subscriptions/redemptions from a time-weighted
return calculation. This module's own append-only winddown_log.jsonl is
the single source of truth for "how much was withdrawn, and when" that
those two read-time adjustments consult (see
cumulative_withdrawn_up_to()/cumulative_withdrawn_between(), and their
call sites in paper_trading_engine.py's compute_live_metrics() and
run_daily()). The RAW recorded daily_equity.jsonl/trades.jsonl files are
NEVER rewritten -- the adjustment happens only at read time, so the
underlying historical record stays exactly as genuinely recorded.

A strategy that has never had a withdrawal gets cumulative_withdrawn_*
== 0.0 for every date, making every adjustment this module makes a
complete, provable no-op -- byte-identical to today's behavior until
this feature actually withdraws something.
"""

import json
import math
import os
from datetime import date as date_type
from typing import Optional

from deployment.paper_trading_engine import _load_portfolio, _save_portfolio
from deployment.settings import (
    PAPER_TRADING_MIN_POSITION_VALUE_RUPEES,
    PAPER_TRADING_STATE_DIR,
    PAPER_TRADING_WINDDOWN_DAILY_FRACTION,
    PAPER_TRADING_WINDDOWN_ENABLED,
    PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET,
    PAPER_TRADING_WINDDOWN_TARGET_CAPITAL,
)

# Fixed per explicit spec ("reserved_capital = estimated_position_cost x
# 1.05") -- not a tunable preference like the settings.py knobs above,
# it's part of the algorithm itself.
RESERVATION_BUFFER_MULTIPLIER = 1.05

# Below this remaining excess, withdraw it all in one go rather than
# asymptotically approaching the target forever under the daily-fraction
# rule (10% of a small number stays small forever) -- disclosed, not
# hidden: this is what actually lets a strategy CONVERGE to exactly
# target_active_capital in finite time, per explicit direction
# ("portfolios naturally converge toward Rs.1,00,000").
SNAP_TO_TARGET_THRESHOLD_RUPEES = 2_000.0


# --------------------------- reservation math ---------------------------

def estimate_reservation(pending_entries: dict, cash: float, risk_pct_per_unit: float,
                          min_position_value_rupees: float = PAPER_TRADING_MIN_POSITION_VALUE_RUPEES,
                          target_active_capital: float = PAPER_TRADING_WINDDOWN_TARGET_CAPITAL) -> dict:
    """
    Estimates the total cash tomorrow's real resolution is likely to need
    for every symbol currently in `pending_entries`, using the EXACT same
    sizing formula deployment/paper_trading_engine.py's own
    _resolve_pending_fills() uses (quantity = floor(min(available,
    target_active_capital) x risk_pct_per_unit / risk_per_share) --
    see that function's own sizing_capital_cap docstring, added
    2026-08-24, for why the sizing basis is capped), simulated
    sequentially against a working copy of `cash` -- mirrors that real
    resolution loop's own sequential consumption (each entry sized using
    whatever cash is left after the previous one in the same run), so a
    strategy with several pending entries doesn't get an inflated
    reservation that assumes each one gets the FULL cash pool
    independently.

    target_active_capital: the SAME cap _resolve_pending_fills() applies
    -- defaults to PAPER_TRADING_WINDDOWN_TARGET_CAPITAL so this estimate
    stays an exact mirror of what the real (now-capped) resolution will
    do. A strategy already at or below target is unaffected: the working
    pool starts at cash either way.

    pending_entries: {symbol: {"stop_loss": ..., "signal_price": ...,
    ...}} -- the exact shape portfolio["pending_entries"] already has.
    signal_price is the reference price used (the close the signal was
    detected at, the day before) -- real tomorrow's Open is, by
    definition, not yet known. An entry queued before signal_price
    existed as a field (a genuine backward-compatibility case, not a bug
    -- see reporting/telegram_templates.py's own identical fallback) is
    skipped from reservation entirely: with no reference price to size
    from, reserving zero for it is the conservative choice (the
    alternative -- reserving nothing -- already happens for it; reserving
    a GUESS would risk reserving the WRONG amount).

    Returns {"total_reserved": float, "per_symbol": {symbol: float}} --
    per_symbol only contains symbols an amount was actually reserved for.
    """
    working_cash = min(max(cash, 0.0), target_active_capital)
    per_symbol = {}
    for symbol, pending in pending_entries.items():
        signal_price = pending.get("signal_price")
        stop_loss = pending.get("stop_loss")
        if signal_price is None or stop_loss is None or signal_price <= stop_loss:
            continue   # can't size without a reference price -- same guard real resolution applies
        risk_per_share = signal_price - stop_loss
        quantity = math.floor(working_cash * risk_pct_per_unit / risk_per_share)
        if quantity < 1:
            continue
        estimated_cost = quantity * signal_price
        if estimated_cost < min_position_value_rupees:
            continue   # mirrors run_daily()'s own min-position-value skip
        per_symbol[symbol] = round(estimated_cost * RESERVATION_BUFFER_MULTIPLIER, 2)
        working_cash -= estimated_cost   # sequential consumption, same order real resolution will use

    return {"total_reserved": round(sum(per_symbol.values()), 2), "per_symbol": per_symbol}


def compute_winddown_withdrawal(cash: float, reserved: float, target_active_capital: float,
                                 daily_fraction: float,
                                 snap_threshold: float = SNAP_TO_TARGET_THRESHOLD_RUPEES,
                                 immediate_if_fully_idle: bool = False) -> float:
    """
    Pure decision function -- how much to withdraw TODAY, given today's
    cash, the amount reserved for pending entries, and the target. Never
    negative, never more than idle cash (cash - reserved), and never
    reduces cash below target_active_capital in one step unless the
    remaining gap is already below snap_threshold (see module docstring
    for why a snap-to-target rule is needed for finite convergence) OR
    immediate_if_fully_idle is True.

    immediate_if_fully_idle (added 2026-08-24, per explicit direction,
    after observing PEAD -- zero open positions, zero pending entries --
    still only had its excess trimmed 10%/day like an actively-trading
    strategy): the daily_fraction pacing exists to avoid a sudden,
    visually-alarming cash swing next to an ACTIVE strategy's real trades
    and reports -- a strategy holding nothing at all has no such trading
    narrative for a same-day full withdrawal to disrupt. The CALLER
    (apply_capital_winddown()) decides when this applies, from the real
    portfolio's positions/pending_entries -- this function only encodes
    what to DO once told, exactly like conflicting_robustness_evidence in
    swing_research/acceptance_criteria.py's determine_acceptance_verdict().
    """
    idle_cash = cash - reserved
    if idle_cash <= 0:
        return 0.0
    excess_over_target = cash - target_active_capital
    if excess_over_target <= 0:
        return 0.0   # already at or below target -- nothing to wind down
    if immediate_if_fully_idle or excess_over_target <= snap_threshold:
        withdrawal = excess_over_target
    else:
        withdrawal = excess_over_target * daily_fraction
    return round(min(idle_cash, withdrawal), 2)


# --------------------------- wind-down log I/O ---------------------------

def _winddown_log_path(strategy_key: str) -> str:
    return os.path.join(PAPER_TRADING_STATE_DIR, strategy_key, "winddown_log.jsonl")


def _append_winddown_log(strategy_key: str, as_of: date_type, amount: float) -> None:
    path = _winddown_log_path(strategy_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": as_of.isoformat(), "amount": amount}) + "\n")


def _load_winddown_log(strategy_key: str) -> list:
    path = _winddown_log_path(strategy_key)
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def cumulative_withdrawn_up_to(strategy_key: str, as_of_date: date_type) -> float:
    """Total ever withdrawn for this strategy, on or before as_of_date.
    Used to make a historical equity/cash figure comparable to "what it
    would have been if no withdrawal had ever happened" -- see this
    module's own docstring for why that's needed for CAGR/Sharpe/
    daily_pnl. Returns 0.0 (a complete no-op) if this strategy has never
    had a withdrawal -- the file not existing at all is the common case
    for every strategy until wind-down actually removes something."""
    total = 0.0
    for entry in _load_winddown_log(strategy_key):
        if date_type.fromisoformat(entry["date"]) <= as_of_date:
            total += entry["amount"]
    return round(total, 2)


def cumulative_withdrawn_between(strategy_key: str, start_inclusive: date_type,
                                  end_exclusive: date_type) -> float:
    """Sum of withdrawals with start_inclusive <= date < end_exclusive --
    used by run_daily()'s daily_pnl computation to add back exactly the
    withdrawal(s) that happened strictly AFTER the previously-recorded
    equity snapshot (start_inclusive = that previous day, since its OWN
    snapshot was taken BEFORE that day's withdrawal) and up to BEFORE
    today's snapshot (end_exclusive = today, since today's own withdrawal,
    if any, happens AFTER today's run_daily() records its own figure, and
    so only affects TOMORROW's lookup, not today's)."""
    total = 0.0
    for entry in _load_winddown_log(strategy_key):
        d = date_type.fromisoformat(entry["date"])
        if start_inclusive <= d < end_exclusive:
            total += entry["amount"]
    return round(total, 2)


# ------------------------------ orchestration ------------------------------

def apply_capital_winddown(strategy_key: str, risk_pct_per_unit: float,
                            as_of_date: Optional[date_type] = None,
                            target_active_capital: float = PAPER_TRADING_WINDDOWN_TARGET_CAPITAL,
                            daily_fraction: float = PAPER_TRADING_WINDDOWN_DAILY_FRACTION,
                            enabled: bool = PAPER_TRADING_WINDDOWN_ENABLED,
                            current_equity: Optional[float] = None,
                            reduced_floor_while_above_target: float =
                                PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET) -> dict:
    """
    Call ONCE per strategy, per daily cycle, strictly AFTER that day's
    run_daily()/run_pead_daily() call has fully completed -- see this
    module's own docstring for the full sequencing rationale, and
    run_paper_trading.py's _run_one() for the call site.

    Mutates ONLY portfolio["cash"] (never positions, pending_entries/
    exits, trades, or daily_equity) and appends one line to this
    strategy's own winddown_log.jsonl when a withdrawal actually happens.

    current_equity (added 2026-09-01, per explicit direction): this
    module normally only ever compares CASH to target_active_capital --
    a strategy whose cash has already fallen below target (the common
    case once most of its pool is deployed into open POSITIONS, which
    this module never touches) gets zero further withdrawal, even though
    its total mark-to-market equity (cash + position value) is still far
    above target. When the caller supplies current_equity (from that
    day's run_daily() result -- see run_paper_trading.py's _run_one())
    and it is still above target_active_capital, the withdrawal decision
    uses reduced_floor_while_above_target (a much lower cash floor, see
    its own settings.py docstring) instead of the real target for THIS
    call only -- so any cash freed up by a position exiting today (or
    already sitting there) gets aggressively skimmed toward that lower
    floor, rather than waiting for cash alone to exceed the full target.
    The moment current_equity is at or below target_active_capital, this
    reverts to the normal target -- a strategy is never wound down below
    its own real target. Omitting current_equity (the default, None) is
    a complete no-op for this behavior -- byte-identical to before this
    parameter existed. NOTE: this only changes the WITHDRAWAL floor --
    estimate_reservation() below is deliberately still called with the
    real target_active_capital, since it mirrors _resolve_pending_fills()'s
    own SEPARATE, fixed sizing_capital_cap, which this feature does not
    change.

    Returns {"withdrawn": float, "reserved": float, "idle_cash": float,
    "remaining_cash": float, "reason": Optional[str]} -- "reason" is set
    (and withdrawn == 0.0) when nothing was withdrawn, distinguishing
    "disabled", "already at or below target", and "no idle cash after
    reservation" for operational visibility.
    """
    if not enabled:
        return {"withdrawn": 0.0, "reserved": 0.0, "idle_cash": None, "remaining_cash": None,
                "reason": "wind-down disabled"}

    target_date = as_of_date or date_type.today()
    portfolio = _load_portfolio(strategy_key)
    cash = portfolio["cash"]

    reservation = estimate_reservation(portfolio.get("pending_entries", {}), cash, risk_pct_per_unit,
                                        target_active_capital=target_active_capital)
    reserved = reservation["total_reserved"]
    idle_cash = round(cash - reserved, 2)

    # A strategy holding NOTHING at all -- no open positions, nothing
    # queued for tomorrow -- has no active trading narrative for a full,
    # same-day withdrawal to disrupt (see compute_winddown_withdrawal()'s
    # own immediate_if_fully_idle docstring). Determined here, from the
    # real portfolio, not guessed from `reserved` alone (which reflects
    # only PENDING entries, never open positions).
    fully_idle = not portfolio.get("positions") and not portfolio.get("pending_entries")

    withdrawal_floor = (reduced_floor_while_above_target
                         if current_equity is not None and current_equity > target_active_capital
                         else target_active_capital)

    withdrawal = compute_winddown_withdrawal(cash, reserved, withdrawal_floor, daily_fraction,
                                               immediate_if_fully_idle=fully_idle)
    if withdrawal <= 0:
        reason = None if cash <= withdrawal_floor else "no idle cash after reservation"
        return {"withdrawn": 0.0, "reserved": reserved, "idle_cash": idle_cash,
                "remaining_cash": round(cash, 2), "reason": reason}

    portfolio["cash"] = round(cash - withdrawal, 2)
    _save_portfolio(strategy_key, portfolio)
    _append_winddown_log(strategy_key, target_date, withdrawal)

    return {"withdrawn": withdrawal, "reserved": reserved, "idle_cash": idle_cash,
            "remaining_cash": portfolio["cash"], "reason": None}
