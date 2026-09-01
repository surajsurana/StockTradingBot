"""
Configuration dedicated ONLY to the Research Deployment system
(deployment/) -- paper-trading virtual capital, deployment state paths.
Deliberately NOT part of config/settings.py (a protected, off-limits file
per the research program's standing rules) and NOT reusing
STARTING_CAPITAL or swing_research.acceptance_criteria-adjacent settings
-- this program's own dedicated setting, isolated the same way
research_lab's RESEARCH_LAB_VIRTUAL_CAPITAL is isolated from production's
STARTING_CAPITAL (see config/settings.py's own comment on that precedent).

Nothing here is read by run_daily.py, monitor_positions.py, execution/,
cio/, or the real risk.risk_manager.RiskManager. This capital is virtual
and never becomes real money without a separate, explicit, human-approved
change outside this file (see deployment/pilot_live.py's module docstring).

CONFIGURABLE CAPITAL (added 2026-08-17, per explicit direction): the
user's real eventual starting capital is flexible (~Rs.25,000 today,
possibly Rs.50,000/1L/5L/10L/20L+ later, based on real performance) --
no specific rupee figure may be a permanent, hardcoded assumption
anywhere in this program. PAPER_TRADING_VIRTUAL_CAPITAL below is now read
from the PAPER_TRADING_VIRTUAL_CAPITAL environment variable (the
"appropriate existing configuration mechanism" this codebase already
uses -- see this file's own KITE_API_KEY-adjacent comments in
config/settings.py recommending env vars), falling back to the ORIGINAL
1,000,000 default if unset -- so this change is 100% backward compatible
for SW-003/SW-008 (already-running strategies keep their own
already-persisted portfolio.json "starting_capital" regardless; this
constant only ever seeds a BRAND NEW strategy's first-ever portfolio, see
paper_trading_engine._load_portfolio()). To paper-trade at a different
capital going forward (e.g. Rs.25,000 to rehearse a specific pilot size),
set this env var before running run_paper_trading.py -- no code change
required, per explicit direction ("Capital must be a configurable
runtime/account-level setting").

PAPER_TRADING_MIN_POSITION_VALUE_RUPEES: OFF (0) by default -- preserves
the exact existing behavior (any signal producing >=1 share and
affordable cash is taken, no minimum). At small capital, a signal can
round down to a tiny position (e.g. 1-2 shares) where a flat brokerage
fee would represent a disproportionate cost -- rather than silently
inventing a minimum-position threshold, this makes the RULE explicit and
configurable (a real number here activates the check in
paper_trading_engine.run_daily()), left at 0/disabled until a specific
value is deliberately chosen and documented, per explicit direction
("Do not invent arbitrary rules silently").
"""

import os

PAPER_TRADING_VIRTUAL_CAPITAL = float(os.environ.get("PAPER_TRADING_VIRTUAL_CAPITAL", 1_000_000))
PAPER_TRADING_MIN_POSITION_VALUE_RUPEES = float(os.environ.get("PAPER_TRADING_MIN_POSITION_VALUE_RUPEES", 0))

# CAPITAL WIND-DOWN (added 2026-08-23, per explicit direction): gradually
# brings each strategy's own idle cash down toward a target "actively
# deployable capital" level, without ever touching cash reserved for
# currently-queued next-session entries or force-selling anything -- see
# deployment/capital_winddown.py for the full mechanism. All three knobs
# below are runtime-configurable (same "appropriate existing configuration
# mechanism" convention as PAPER_TRADING_VIRTUAL_CAPITAL above), per the
# same "no hardcoded capital assumption" principle -- change these via
# environment variable, never a code edit, to retarget or pause wind-down.
#
# PAPER_TRADING_WINDDOWN_ENABLED: on by default -- this setting exists
# specifically so the feature can be switched off operationally (e.g. if
# something looks wrong after deployment) without a code change.
# PAPER_TRADING_WINDDOWN_TARGET_CAPITAL: the cash level each strategy
# gradually converges toward. Explicit direction named Rs.1,00,000.
# PAPER_TRADING_WINDDOWN_DAILY_FRACTION: what fraction of TODAY's excess
# cash (cash above target, after reservation) is withdrawn on any one
# day. CHANGED 2026-08-26, per explicit direction, from the original 0.10
# (10%, deliberately conservative -- removed a large excess over a few
# weeks without a sudden cash drop) to 0.90 (90%): the slow default meant
# new BUY signals were still finding a large, un-withdrawn pool sitting
# around for days/weeks (confirmed live: Minervini/Cross-Sectional
# Momentum's daily withdrawal was visibly shrinking day over day as new
# positions consumed the "idle" cash faster than 10%/day could claim it
# back -- see the sizing_capital_cap fix, 2026-08-24, for the other half
# of this same problem). At 0.90, an excess is reduced to ~10% of itself
# per day (below the snap threshold within 3-4 days for even a very large
# excess) so cash genuinely reaches target quickly rather than nominally
# sitting there unused.
#
# EXPLICIT POLICY CHANGE (2026-08-26, confirmed): this reverses the
# earlier "capital reduction must never cause an otherwise-valid signal
# to be skipped, only resized" guarantee for BRAND-NEW (not yet queued)
# signals -- once cash has been aggressively swept down near target, a
# later same-day signal can find genuinely insufficient cash and be
# skipped entirely (see deployment/paper_trading_engine.py's existing
# `quantity < 1 or cost > cash` skip path -- always present, rarely
# reachable before this change). Confirmed as an intentional, accepted
# trade-off: a strategy operating at a small, finite capital pool
# sometimes can't take every signal, exactly as it wouldn't be able to
# with real money. This does NOT weaken the SEPARATE reservation
# guarantee for entries ALREADY queued from a prior day --
# compute_winddown_withdrawal() still hard-caps every withdrawal at
# idle_cash (cash minus reserved), regardless of daily_fraction, so an
# entry already reserved for is never starved by wind-down itself.
PAPER_TRADING_WINDDOWN_ENABLED = os.environ.get("PAPER_TRADING_WINDDOWN_ENABLED", "true").lower() == "true"
PAPER_TRADING_WINDDOWN_TARGET_CAPITAL = float(os.environ.get("PAPER_TRADING_WINDDOWN_TARGET_CAPITAL", 100_000))
PAPER_TRADING_WINDDOWN_DAILY_FRACTION = float(os.environ.get("PAPER_TRADING_WINDDOWN_DAILY_FRACTION", 0.90))

# PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET: added 2026-09-01,
# per explicit direction, for strategies still sitting on a much larger
# pool (e.g. Rs.10,00,000) than the target because most of it is tied up
# in open POSITIONS, not idle cash -- compute_winddown_withdrawal() only
# ever acts on cash, so a strategy whose cash has already fallen below
# PAPER_TRADING_WINDDOWN_TARGET_CAPITAL (common once most of the pool is
# deployed) gets zero further withdrawal under the normal target, even
# though its total mark-to-market EQUITY is still far above target. This
# knob is a SEPARATE, much lower cash floor that applies ONLY while a
# strategy's current equity is still above the real target (see
# apply_capital_winddown()'s current_equity parameter) -- it does not
# change the real target itself, and reverts to the normal
# PAPER_TRADING_WINDDOWN_TARGET_CAPITAL floor the moment equity has
# actually reached target. Explicit direction named "leave 10% of cash
# to buy new signals" -- 10% of the real target by default. Confirmed
# accepted trade-off (2026-09-01): sizing off a much smaller cash pool
# will very likely shrink new-signal position sizes below
# PAPER_TRADING_MIN_POSITION_VALUE_RUPEES for these strategies until
# their existing open positions have unwound and equity has actually
# reached target -- prioritizing fast convergence to target capital over
# continued new-trade generation in the meantime.
PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET = float(
    os.environ.get("PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET",
                    PAPER_TRADING_WINDDOWN_TARGET_CAPITAL * 0.10))

# NARROW, EXPLICIT EXCEPTION (2026-08-04, per explicit direction): the ONLY
# thing ever imported from config/settings.py anywhere in deployment/.
# Exists solely so paper-trading Telegram notifications land in the same
# channel as live trading notifications -- a notification-routing
# convenience, not trading behavior. Do NOT import anything else from
# config/settings.py (no execution settings, no risk settings, no broker
# credentials) -- deployment/ remains otherwise completely isolated from
# production trading behavior.
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   # noqa: F401 (re-exported for deployment/'s own callers)

DEPLOYMENT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(DEPLOYMENT_DIR, "state")
PAPER_TRADING_STATE_DIR = os.path.join(STATE_DIR, "paper_trading")
REGISTRY_PATH = os.path.join(STATE_DIR, "strategy_registry.json")
REPORTS_DIR = os.path.join(DEPLOYMENT_DIR, "reports")
LIFECYCLE_REVIEWS_DIR = os.path.join(DEPLOYMENT_DIR, "lifecycle_reviews")
CERTIFICATION_EXPERIMENTS_DIR = os.path.join(DEPLOYMENT_DIR, "certification_experiments")
CERTIFICATION_KNOWLEDGE_BASE_PATH = os.path.join(DEPLOYMENT_DIR, "certification_knowledge_base.jsonl")
