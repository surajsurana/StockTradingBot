"""
Why a promoted, funded strategy is not trading -- and whether the answer is money or a mistake.

WHAT THIS IS FOR. Suraj's standing requirement, restated 2026-10-07: "come to a stage where you can
tell me the only problem now is that there is no money." That is a question about the whole chain --
venue, credentials, broker session, caps, the kill switch, the schedule -- and before this it could
only be answered by reading six files and a crontab. A system meant to run unattended has to be able
to say what is stopping it.

THE ONE DISTINCTION THAT MATTERS. Every blocker is either MONEY -- the account or the cap simply does
not hold enough -- or CONFIG, meaning something is set up wrong and no amount of funding will fix it.
Mixing the two is how "it isn't trading" stays unexplained for weeks: a stale access token and an
empty account look identical from outside, and only one of them is your decision to make.

WHAT IT DOES NOT DO. It changes nothing: no orders, no files, no settings, no session refresh. It
reports. A caller that wants to act on what it finds does that itself, deliberately.

It also cannot see the future. A clean report means nothing is blocking an order AT THE MOMENT IT IS
ASKED -- a token that expires tonight reads as fine this afternoon, which is correct and is why the
runner refreshes its own session rather than trusting a check made earlier.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

MONEY = "money"
CONFIG = "config"


@dataclass(frozen=True)
class Blocker:
    kind: str           # MONEY or CONFIG
    detail: str         # a sentence a person can act on
    fix: str = ""       # what would clear it, where that is a single concrete step


@dataclass
class Readiness:
    strategy_key: str
    venue: str = ""
    allocated: float = 0.0
    blockers: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def money_only(self) -> bool:
        """True when funding is the ONLY thing left. The sentence this module exists to produce."""
        return bool(self.blockers) and all(b.kind == MONEY for b in self.blockers)

    def of_kind(self, kind: str) -> list:
        return [b for b in self.blockers if b.kind == kind]


def _min_position(venue: str) -> float:
    """The smallest position this venue's books will open, which is what a per-order cap has to
    clear. A cap below it refuses every order the engine can produce -- forever, silently."""
    from deployment.venues import KITE
    if venue != KITE:
        return 0.0
    from swing_research.broker_costs import min_viable_notional
    return min_viable_notional()


def _natural_position(strategy_key: str, rupees: float) -> Optional[float]:
    """What a book of `rupees` would actually put into one position, from this strategy's own risk
    budget and the stop distances it has really been using. None when it has no history to read."""
    from deployment.settings import STATE_DIR
    import json
    import statistics
    sizes = []
    for folder in ("paper_trading", "pool_f"):
        path = os.path.join(STATE_DIR, folder, strategy_key, "trades.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    t = json.loads(line)
                    value = float(t.get("entry_price") or 0) * float(t.get("quantity") or 0)
                except (ValueError, TypeError):
                    continue
                if value > 0:
                    sizes.append(value)
    if not sizes:
        return None
    base = 100_000.0        # the books these trades came from
    return statistics.median(sizes) * (rupees / base)


def check(record, rupees: float, *, settings, state_dir: str, broker_cash: Optional[float] = None,
          now: Optional[datetime] = None, deployment_cap: Optional[float] = None) -> Readiness:
    """Everything standing between this strategy and a real order, newest facts first.

    `broker_cash` is what the venue's account actually holds; None means "not read", which is itself
    reported rather than assumed to be fine."""
    from deployment.base import DeploymentStatus
    from deployment.live_guard import (DEFAULT_MAX_LIVE_EXPOSURE_RUPEES,
                                       DEFAULT_MAX_ORDER_VALUE_RUPEES, _setting,
                                       kill_switch_engaged, kill_switch_path)
    from deployment.venues import KITE, NONE, venue_of

    now = now or datetime.now()
    out = Readiness(strategy_key=getattr(record, "strategy_key", ""), allocated=float(rupees or 0))
    add = out.blockers.append

    venue = venue_of(record)
    out.venue = venue
    if venue == NONE:
        add(Blocker(CONFIG, f"No broker is wired for {out.strategy_key}'s market, so it cannot trade "
                            f"at all.", "Pool I is paper-only by design; nothing to fix here."))
        return out                      # nothing below means anything without a venue

    # 1. The switches that stop everything, whatever else is true.
    if getattr(settings, "LIVE_TRADING", False) is not True:
        add(Blocker(CONFIG, "LIVE_TRADING is not True, so no real order can be placed.",
                    "Set LIVE_TRADING = True in config/settings.py on the server."))
    if kill_switch_engaged(state_dir):
        add(Blocker(CONFIG, "The kill switch is engaged, which halts every live order.",
                    f"rm {kill_switch_path(state_dir)}"))

    # 2. Is it even promoted.
    status = getattr(record, "deployment_status", None)
    if status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
        add(Blocker(CONFIG, f"It is {getattr(status, 'value', status)}, not live.",
                    "Press P on the Strategies tab to promote it."))

    # 3. Credentials, and for Kite the session behind them -- a token that expired overnight looks
    #    exactly like an empty account from outside, and only one of those is your decision.
    from deployment.credential_store import credential
    config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
    required = (("KITE_API_KEY", "Kite API key"), ("KITE_ACCESS_TOKEN", "Kite access token")) \
        if venue == KITE else (("COINDCX_API_KEY", "CoinDCX API key"),
                               ("COINDCX_API_SECRET", "CoinDCX API secret"))
    for name, label in required:
        if not str(credential(name, config_dir, settings) or "").strip():
            add(Blocker(CONFIG, f"No {label} is configured.",
                        "Add it under Settings on the dashboard."))

    # 4. Money: the allocation, the account behind it, and the cap above it.
    if out.allocated <= 0:
        add(Blocker(MONEY, "No capital is assigned to it, so every position would size to zero.",
                    "Assign capital in the Live Capital column."))
    if broker_cash is None:
        add(Blocker(CONFIG, f"The {venue} balance could not be read, so it is not known whether the "
                            f"account can fund this.", "Check the broker credentials and connectivity."))
    elif out.allocated > broker_cash:
        add(Blocker(MONEY, f"Rs{out.allocated:,.0f} is assigned but the {venue} account holds "
                           f"Rs{broker_cash:,.0f}.",
                    f"Add Rs{out.allocated - broker_cash:,.0f} to the {venue} account."))
    if deployment_cap is not None and out.allocated > deployment_cap:
        add(Blocker(MONEY, f"Rs{out.allocated:,.0f} is assigned but the deployment cap is "
                           f"Rs{deployment_cap:,.0f}.",
                    "Raise the deployment cap under Settings."))

    # 5. The caps, against the size this strategy would ACTUALLY trade.
    #
    # A per-order cap below the smallest position the engine can open refuses every order the
    # strategy will ever produce -- and does it one order at a time, so it reads as bad luck rather
    # than a setting. This is the check that would have saved the first live equity week.
    from deployment.live_guard import DEFAULT_MAX_ORDER_PCT_OF_CAPITAL
    pct = _setting(settings, "LIVE_MAX_ORDER_PCT_OF_CAPITAL", DEFAULT_MAX_ORDER_PCT_OF_CAPITAL)
    absolute = _setting(settings, "LIVE_MAX_ORDER_VALUE_RUPEES", DEFAULT_MAX_ORDER_VALUE_RUPEES)
    max_order = min([c for c in (pct * out.allocated if pct > 0 else 0, absolute) if c > 0],
                    default=0.0)
    max_exposure = _setting(settings, "LIVE_MAX_EXPOSURE_RUPEES", DEFAULT_MAX_LIVE_EXPOSURE_RUPEES)
    if max_exposure <= 0 and deployment_cap:
        max_exposure = float(deployment_cap)
    if max_exposure <= 0:
        max_exposure = float("inf")
    floor = _min_position(venue)
    if max_order and floor and max_order < floor:
        add(Blocker(CONFIG, f"The per-order cap works out at Rs{max_order:,.0f} but this venue will "
                            f"not open a position below Rs{floor:,.0f}, so every order would be "
                            f"refused.",
                    f"Raise LIVE_MAX_ORDER_PCT_OF_CAPITAL, or assign more capital -- the cap is "
                    f"{pct:.0%} of what the strategy holds."))
    natural = _natural_position(out.strategy_key, out.allocated)
    if natural and max_order and natural > max_order:
        add(Blocker(CONFIG, f"At Rs{out.allocated:,.0f} this strategy would open about "
                            f"Rs{natural:,.0f} per position, over the Rs{max_order:,.0f} per-order cap.",
                    f"Raise LIVE_MAX_ORDER_PCT_OF_CAPITAL above "
                    f"{natural / out.allocated:.0%}." if out.allocated else ""))
    if out.allocated > max_exposure:
        add(Blocker(CONFIG, f"Rs{out.allocated:,.0f} is assigned but total live exposure is capped at "
                            f"Rs{max_exposure:,.0f}, so it would stop part-way in.",
                    f"Set LIVE_MAX_EXPOSURE_RUPEES to at least Rs{out.allocated:,.0f}."))
    if floor and out.allocated and natural is not None and natural < floor:
        add(Blocker(MONEY, f"At Rs{out.allocated:,.0f} its positions would be about Rs{natural:,.0f}, "
                           f"below the Rs{floor:,.0f} a trade needs to cover its own charges, so every "
                           f"signal would be skipped.",
                    f"Assign about Rs{out.allocated * floor / natural:,.0f} for its own sizing to "
                    f"clear the floor." if natural else ""))
    return out


def check_all(*, settings=None, state_dir: Optional[str] = None, broker_cash: Optional[dict] = None,
              now: Optional[datetime] = None, deployment_cap: Optional[float] = None) -> list:
    """Every promoted strategy, funded or not -- an unfunded one is exactly the case worth reporting."""
    from deployment.base import DeploymentStatus
    from deployment.deployment_manager import list_strategies
    from deployment.live_allocations import allocation_for
    from deployment.settings import STATE_DIR
    from deployment.venues import venue_of

    if settings is None:
        from config import settings as settings            # noqa: PLC0415
    state_dir = state_dir or STATE_DIR
    cash = broker_cash or {}
    out = []
    for record in list_strategies():
        if record.deployment_status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
            continue
        out.append(check(record, allocation_for(state_dir, record.strategy_key), settings=settings,
                         state_dir=state_dir, broker_cash=cash.get(venue_of(record)), now=now,
                         deployment_cap=deployment_cap))
    return out
