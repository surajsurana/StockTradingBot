"""
Per-strategy live capital, assigned from the one real cash pool (2026-10-06).

WHY THIS EXISTS. Paper trading gives every strategy its own separate virtual
book, so each one sizes positions off its own capital in isolation. Live has a
single real Kite balance. Without an explicit per-strategy allocation there is
no "capital" for a live strategy to size against, and the sizing formula in
deployment/paper_trading_engine.py is built entirely around having one:

    quantity = floor( min(cash, sizing_cap) * risk_pct_per_unit / risk_per_share )

That formula is strictly PROPORTIONAL to capital, which is the property this
module exists to preserve. Assign a strategy half the capital and every
position halves; the percentages -- risk per trade, position as a share of the
book -- come out identical to paper without any translation. Per explicit
direction: "percentages of all must remain same so we get same results as we
got in paper".

WHAT THIS MODULE DOES NOT DO. It holds no opinion about whether a strategy
should be live, places no orders, and never reads the broker itself. The
available balance is passed IN by the caller, so this stays pure and testable
without a broker, a network or a VPS. deployment/live_guard.py remains the
thing that decides whether an order may be placed at all.

FAIL CLOSED, same rule as live_guard.py: an unreadable file, a malformed
number or an unparseable total refuses the change and leaves the stored
allocations untouched. A write that cannot be completed leaves the previous
file intact (atomic replace over a temp file, the same convention
research_queue.py uses).
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

ALLOCATIONS_FILENAME = "live_allocations.json"


@dataclass
class AllocationResult:
    ok: bool
    reasons: list = field(default_factory=list)
    allocations: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def _path(state_dir: str) -> str:
    return os.path.join(state_dir, ALLOCATIONS_FILENAME)


def load(state_dir: Optional[str]) -> dict:
    """{strategy_key: rupees}. Anything unreadable, malformed or non-numeric reads as EMPTY rather
    than as a partial allocation -- a half-understood capital map is more dangerous than none."""
    if not state_dir:
        return {}
    try:
        with open(_path(state_dir), encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        out = {}
        for key, value in raw.items():
            amount = float(value)
            if amount > 0:
                out[str(key)] = amount
        return out
    except (OSError, ValueError, TypeError):
        return {}


def _save(state_dir: str, allocations: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    tmp = _path(state_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(allocations, f, indent=2, sort_keys=True)
    os.replace(tmp, _path(state_dir))


def total_allocated(state_dir: Optional[str], excluding: str = "",
                    only: Optional[set] = None) -> float:
    """Rupees assigned, across every strategy or only the ones named in `only`.

    `only` exists because the money is NOT one pool. Kite and CoinDCX are separate accounts: rupees
    at Kite cannot buy crypto and rupees at CoinDCX cannot buy shares, so what is assigned at one
    venue has no bearing on what can be assigned at the other. Summing them together refused a
    Rs10,000 Kite allocation because Rs10,000 was assigned at CoinDCX -- money in a different account
    entirely."""
    return round(sum(v for k, v in load(state_dir).items()
                     if k != excluding and (only is None or k in only)), 2)


def unallocated(state_dir: Optional[str], available_balance: float) -> float:
    """Real cash not yet assigned to any strategy. Never negative: an over-allocated pool reads as
    zero free rather than as a negative figure that might be mistaken for headroom."""
    try:
        return max(0.0, round(float(available_balance) - total_allocated(state_dir), 2))
    except (TypeError, ValueError):
        return 0.0


def set_allocation(state_dir: str, strategy_key: str, rupees, *, available_balance,
                   open_live_positions: int = 0, same_venue_keys: Optional[set] = None,
                   deployment_cap: Optional[float] = None) -> AllocationResult:
    """
    Assigns `rupees` of the real cash pool to one strategy. Returns the full allocation map on
    success, and on failure changes nothing and explains why.

    open_live_positions guards the invariant this module exists for: the sizing formula reads capital
    at order time, so moving a strategy's capital while it holds live positions would size the rest of
    that position differently from the first part, and from paper. The comparison with paper would
    then be measuring the reallocation rather than execution. Flatten first, then reallocate.
    """
    reasons = []

    key = str(strategy_key or "").strip()
    if not key:
        reasons.append("No strategy key given.")

    try:
        amount = float(rupees)
    except (TypeError, ValueError):
        return AllocationResult(False, [f"Allocation {rupees!r} is not a number."], load(state_dir))
    if amount < 0:
        reasons.append(f"Allocation Rs{amount:,.0f} is negative.")

    try:
        balance = float(available_balance)
    except (TypeError, ValueError):
        return AllocationResult(False, ["The available balance could not be read as a number."],
                                load(state_dir))
    if balance < 0:
        reasons.append(f"Available balance Rs{balance:,.0f} is negative.")

    if open_live_positions:
        reasons.append(f"{key} holds {open_live_positions} open live position(s). Capital may only be "
                        "changed while a strategy is flat -- otherwise the rest of a position sizes off "
                        "different capital from the part already filled, and from paper.")

    # TWO SEPARATE LIMITS, because they are two different facts.
    #
    # The ACCOUNT holds the money, and there is one account per venue. Only what is assigned at THIS
    # venue competes for it. Counting every venue's allocations against one account's balance refused
    # a Rs10,000 Kite allocation because Rs10,000 sat assigned at CoinDCX -- a different account,
    # holding different money, that the Kite order could never have drawn on.
    others = total_allocated(state_dir, excluding=key, only=same_venue_keys)
    if amount + others > balance:
        at_venue = " at this broker" if same_venue_keys is not None else ""
        reasons.append(f"Assigning Rs{amount:,.0f} would take the total allocated{at_venue} to "
                        f"Rs{amount + others:,.0f}, over the available balance of Rs{balance:,.0f} "
                        f"(Rs{others:,.0f} is already assigned to other strategies{at_venue}).")

    # The DEPLOYMENT CAP is the other fact: how much real money you are willing to have deployed in
    # total, whichever account it sits in. That one IS global, and it is the number on the Settings
    # tab -- so when it is what refuses an allocation, the message says so and points there, rather
    # than blaming a broker balance that is perfectly adequate.
    if deployment_cap is not None:
        everywhere = total_allocated(state_dir, excluding=key)
        if amount + everywhere > float(deployment_cap):
            reasons.append(f"Assigning Rs{amount:,.0f} would take the total deployed across all "
                            f"brokers to Rs{amount + everywhere:,.0f}, over your deployment cap of "
                            f"Rs{float(deployment_cap):,.0f}. Raise it under Settings.")

    if reasons:
        return AllocationResult(False, reasons, load(state_dir))

    allocations = load(state_dir)
    if amount == 0:
        allocations.pop(key, None)       # zero means "not allocated", not "allocated nothing"
    else:
        allocations[key] = round(amount, 2)
    try:
        _save(state_dir, allocations)
    except OSError as e:
        return AllocationResult(False, [f"Could not save the allocation ({type(e).__name__}: {e})."],
                                load(state_dir))
    return AllocationResult(True, [], allocations)


def allocation_for(state_dir: Optional[str], strategy_key: str) -> float:
    """A strategy's live capital, 0.0 when it has none. 0.0 means it cannot size any position, which
    is the correct reading of 'no capital assigned' -- never a fallback to the paper figure."""
    return load(state_dir).get(str(strategy_key), 0.0)
