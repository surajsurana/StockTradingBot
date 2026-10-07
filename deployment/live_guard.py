"""
The gate every real order must pass before it can be placed (Gate H, 2026-10-06).

This module places no orders and imports no broker code. It answers one
question -- "may this order be placed right now?" -- and returns a refusal with
a reason when the answer is no. It is written first, before any order-placing
code exists, so that the executor physically cannot be built without going
through it.

EVERYTHING HERE FAILS CLOSED. Missing config, an unreadable file, an
unparseable number, an unexpected exception: all of them refuse. There is no
path through this module where "I could not tell" becomes "yes". That rule is
the whole design -- a live trading guard whose failure mode is "allow" is worse
than no guard, because it looks like protection.

WHY THIS EXISTS AT ALL. deployment/pilot_live.py carries a standing rule that
no research or deployment code should be able to move real capital, and that
rule is deliberately NOT being deleted. Instead this module makes real orders
possible only when a human has done four separate, explicit things:

  1. set LIVE_TRADING = True in config/settings.py (git-ignored, VPS-only)
  2. supplied real broker credentials
  3. moved the strategy to PILOT_LIVE, which requires passing the gates in
     deployment/LIVE_PROMOTION_CRITERIA.md
  4. not left a kill-switch file in place

Any one of those missing means no order. The code can exist, be reviewed and be
fully tested long before any of it is true -- which is the point: the dangerous
step stays a human decision, taken on purpose, on a specific day.

CAPS ARE ENFORCED HERE, NOT BY CONVENTION. A per-order value cap, a total live
exposure cap and a per-day order count cap, all read from settings with
conservative defaults, all applied to every order.
"""

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

KILL_SWITCH_FILENAME = "LIVE_TRADING_HALTED"

# Which credentials each broker needs, and what to say when one is missing. A pair of names means
# either will do (Kite's token is refreshed into settings by the TOTP auto-login, so it may live
# there rather than in the credential store).
#
# ADDING A BROKER IS ONE ENTRY HERE. The guard's other checks -- kill switch, deployment status,
# pilot gates, caps -- are broker-agnostic and apply unchanged, which is the point: a new venue
# inherits every protection rather than needing its own.
DEFAULT_BROKER = "kite"
BROKER_CREDENTIALS = {
    "kite": [("No Kite API key configured.", ("KITE_API_KEY",)),
             ("No Kite access token configured (it expires daily).", ("KITE_ACCESS_TOKEN",))],
    "coindcx": [("No CoinDCX API key configured.", ("COINDCX_API_KEY",)),
                ("No CoinDCX API secret configured.", ("COINDCX_API_SECRET",))],
}


# Where the dashboard's credential store lives. A module constant rather than a value computed
# inside the lookup, so a test can point it somewhere empty: otherwise these checks read whatever
# real credentials happen to sit on the machine, and a test that passes on a laptop fails on the
# server that has the store -- which is precisely backwards.
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def _credential(settings, state_dir: str, name: str) -> str:
    """A credential's value, from the dashboard's store first and config/settings.py second -- the
    same resolution order everything else uses, so a key set on the Settings tab is honoured here.
    Any failure reading the store is treated as "absent", which fails the check closed."""
    try:
        from deployment.credential_store import credential
        return credential(name, CONFIG_DIR, settings)
    except Exception:
        return str(getattr(settings, name, "") or "")

# Conservative defaults. They are floors on caution, not recommendations: the real numbers belong in
# config/settings.py on the VPS, and anything absent falls back to these rather than to "unlimited".
# THE PER-ORDER CAP IS A PERCENTAGE, NOT A RUPEE FIGURE (changed 2026-10-07).
#
# It was Rs5,000, chosen when the only live book was crypto and a position was about Rs2,600. An
# equity position cannot open below Rs12,469, so the same number that was generous for one venue
# refused every order the other could ever produce -- and would have gone on doing so one order at a
# time, reading as bad luck rather than as a setting. Any absolute cap has that failure built in: it
# is a guess about capital, and it is wrong the moment capital changes.
#
# What the cap is actually for is catching a SIZING BUG -- an order far larger than the strategy
# intended. That is inherently relative to the book, so the cap is too. At 50% a tenfold sizing error
# is caught on any book of any size, in any currency, for ever, with nothing to re-tune.
DEFAULT_MAX_ORDER_PCT_OF_CAPITAL = 0.50

# An optional ABSOLUTE ceiling on top, for someone who wants one. Zero/absent means "no absolute
# ceiling, the percentage governs" -- deliberately not a rupee default, because a rupee default is
# the thing that just broke.
DEFAULT_MAX_ORDER_VALUE_RUPEES = 0.0

# Total exposure defaults to the deployment cap -- the number already set on the dashboard, meaning
# "the most the bot may ever deploy". A second, separate ceiling that has to agree with it is a
# configuration trap: they drift apart and the stricter one silently wins.
DEFAULT_MAX_LIVE_EXPOSURE_RUPEES = 0.0
DEFAULT_MAX_ORDERS_PER_DAY = 20


@dataclass
class LiveOrderDecision:
    """allowed=False always carries at least one reason. An empty reason list with allowed=False is
    impossible by construction (see _refuse)."""
    allowed: bool
    reasons: list = field(default_factory=list)
    # Gates that were deliberately bypassed by a recorded human override. Never silent: an order
    # allowed only because somebody overrode a gate says so here, and the executor logs it.
    overrides: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.allowed


def _override_when(entry: dict) -> str:
    """When the override was recorded, as a readable date. Falls back to the raw value rather than
    guessing, since this ends up in an audit line."""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(entry.get("timestamp"))).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(entry.get("timestamp", "date unknown"))


def _refuse(*reasons: str) -> LiveOrderDecision:
    cleaned = [r for r in reasons if r]
    return LiveOrderDecision(allowed=False, reasons=cleaned or ["refused, reason unrecorded"])


def kill_switch_path(state_dir: str) -> str:
    return os.path.join(state_dir, KILL_SWITCH_FILENAME)


def kill_switch_engaged(state_dir: str) -> bool:
    """True if the kill switch file exists -- OR if we cannot tell. Creating the file is a single
    action needing no code change, no deploy and no working Python: `touch` it over SSH, or make it
    by any other means, and every subsequent order is refused."""
    try:
        return os.path.exists(kill_switch_path(state_dir))
    except OSError:
        return True      # cannot read the filesystem -> assume halted


def _setting(settings, name: str, default):
    """A setting read that treats absent, blank, unparseable and negative all as 'use the default'."""
    try:
        raw = getattr(settings, name, None)
        if raw is None or raw == "":
            return default
        value = type(default)(raw)
        return default if value < 0 else value
    except (TypeError, ValueError):
        return default


def check_order_allowed(*, settings, record, state_dir: str, order_value_rupees: float,
                        current_live_exposure_rupees: float, orders_placed_today: int,
                        eligibility=None, now: Optional[date] = None,
                        broker: str = DEFAULT_BROKER,
                        allocated_rupees: float = 0.0) -> LiveOrderDecision:
    """
    The single entry point. Every argument is supplied by the caller rather than read from global
    state here, so this stays pure and fully testable without a broker, a VPS or a clock.

    `record` is the strategy's StrategyRecord; `eligibility` is the result of
    deployment/pilot_live.py's check_pilot_eligibility() when the caller has one -- passing it makes
    the promotion gates part of every order check, not just a one-off at promotion time, so a
    strategy that drifts out of eligibility stops trading rather than coasting on an old decision.
    """
    from deployment.base import DeploymentStatus
    from deployment.pilot_live import promotion_override

    reasons = []

    # 1. The global switch. Absent attribute -> treated as off.
    if getattr(settings, "LIVE_TRADING", False) is not True:
        reasons.append("LIVE_TRADING is not True in config/settings.py.")

    # 2. The kill switch.
    if kill_switch_engaged(state_dir):
        reasons.append(f"The kill switch is engaged ({kill_switch_path(state_dir)} exists).")

    # 3. Credentials for THIS order's broker. Presence only -- never logged, never echoed.
    #
    # Which credentials matter depends on where the order is going: an equity order needs Kite, a
    # crypto order needs CoinDCX. Hard-coding Kite here (as this did) meant a correctly configured
    # crypto order was refused for "no broker API key" while the key sat right there.
    for label, names in BROKER_CREDENTIALS.get(str(broker or DEFAULT_BROKER).lower(),
                                               BROKER_CREDENTIALS[DEFAULT_BROKER]):
        if not any(str(_credential(settings, state_dir, n) or "").strip() for n in names):
            reasons.append(label)

    # 4. The strategy itself must be deliberately live, not merely paper trading.
    status = getattr(record, "deployment_status", None)
    if status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
        shown = getattr(status, "value", status)
        reasons.append(f"Strategy deployment_status is {shown}, not PILOT_LIVE or PRODUCTION.")

    # 5. The promotion gates, re-checked per order rather than trusted from promotion day.
    #
    # UNLESS the promotion was a recorded manual override. Without this, overriding the gates at
    # promotion time would appear to work and then silently refuse every order -- the worst outcome,
    # because the strategy would look live and do nothing. The override is read from the registry's
    # own status history, so it cannot be asserted by a caller; and it suppresses ONLY this check.
    # LIVE_TRADING, the kill switch, credentials, deployment status and the hard caps all still
    # apply, and the bypass is recorded on the decision rather than disappearing.
    overrides = []
    if eligibility is not None and not getattr(eligibility, "eligible", False):
        why = "; ".join(getattr(eligibility, "reasons", []) or ["no reason given"])
        override = promotion_override(record)
        if override:
            overrides.append(f"Pilot gates bypassed by a manual override recorded at promotion "
                             f"({_override_when(override)}). Gates not met: {why}")
        else:
            reasons.append(f"Strategy no longer passes the pilot gates: {why}")

    # 6. The market has to be open, for a venue that has opening hours.
    #
    # Crypto trades around the clock, so this is a no-op at CoinDCX. NSE does not: the live runner's
    # cron fired at 08:35 and 20:35 IST, set when the only live book was crypto, and both are outside
    # 09:15-15:30. An equity strategy promoted on those timings would have decided correctly and then
    # sent an order into a closed exchange every single day.
    #
    # It is checked HERE, in the one place every real order passes, rather than left to the cron
    # being right -- a cron line is a thing that can be edited by someone who does not know this.
    # A missing or unreadable clock reads as closed: refusing a tradeable minute costs one run,
    # placing into a closed market costs a rejected order and a book that no longer matches reality.
    if str(broker or DEFAULT_BROKER).lower() == "kite":
        from deployment.scheduler import is_market_open
        market_now = now if isinstance(now, datetime) else None
        if not is_market_open(market_now):
            reasons.append("The NSE is closed right now, so an equity order cannot be placed "
                           "(trading hours are 09:15-15:30 IST on a weekday).")

    # 7. Hard caps.
    # The per-order cap, as a fraction of what THIS strategy was funded with. A caller that cannot
    # say how much that is gets the absolute ceiling only -- it may not guess, because guessing a
    # capital figure is exactly the mistake the percentage exists to remove.
    pct = _setting(settings, "LIVE_MAX_ORDER_PCT_OF_CAPITAL", DEFAULT_MAX_ORDER_PCT_OF_CAPITAL)
    absolute = _setting(settings, "LIVE_MAX_ORDER_VALUE_RUPEES", DEFAULT_MAX_ORDER_VALUE_RUPEES)
    caps = []
    try:
        funded = float(allocated_rupees or 0)
    except (TypeError, ValueError):
        funded = 0.0
    if funded > 0 and pct > 0:
        caps.append((pct * funded, f"{pct:.0%} of the Rs{funded:,.0f} assigned to it"))
    if absolute > 0:
        caps.append((absolute, f"the absolute per-order ceiling of Rs{absolute:,.0f}"))
    if not caps:
        # FAIL CLOSED. No funded capital and no absolute ceiling means the cap cannot be computed,
        # and "cannot compute the cap" must never resolve to "there is no cap". A caller that omits
        # the allocation is a bug, and the right outcome of that bug is a refused order.
        return _refuse("The capital assigned to this strategy is not known, so the per-order cap "
                       "cannot be worked out and no order may be placed.")
    max_order, max_order_label = min(caps)

    max_exposure = _setting(settings, "LIVE_MAX_EXPOSURE_RUPEES", DEFAULT_MAX_LIVE_EXPOSURE_RUPEES)
    if max_exposure <= 0:
        from deployment.live_settings import setting as _stored
        max_exposure = _stored("LIVE_CAPITAL_POOL_RUPEES", CONFIG_DIR, settings_module=settings)
    if max_exposure <= 0:
        max_exposure = float("inf")      # nothing has been funded, so the allocation check governs
    max_orders = _setting(settings, "LIVE_MAX_ORDERS_PER_DAY", DEFAULT_MAX_ORDERS_PER_DAY)

    try:
        value = float(order_value_rupees)
        exposure = float(current_live_exposure_rupees)
        placed = int(orders_placed_today)
    except (TypeError, ValueError):
        return _refuse("Order value, exposure or order count could not be read as a number.")

    if not value > 0:
        reasons.append(f"Order value {value} is not a positive number.")
    if value > max_order:
        reasons.append(f"Order value Rs{value:,.0f} exceeds the per-order cap of "
                       f"Rs{max_order:,.0f} ({max_order_label}).")
    if exposure + max(value, 0.0) > max_exposure:
        reasons.append(f"Order would take live exposure to Rs{exposure + value:,.0f}, "
                        f"over the deployment cap of Rs{max_exposure:,.0f}.")
    if placed >= max_orders:
        reasons.append(f"Already placed {placed} live orders today, at the cap of {max_orders}.")

    if reasons:
        return _refuse(*reasons)
    return LiveOrderDecision(allowed=True, overrides=overrides)
