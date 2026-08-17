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
