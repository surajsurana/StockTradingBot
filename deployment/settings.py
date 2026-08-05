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
"""

import os

PAPER_TRADING_VIRTUAL_CAPITAL = 1_000_000   # never real money -- fully isolated, see module docstring

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
