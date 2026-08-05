"""
Research Deployment system -- a NEW, wholly independent lifecycle-tracking
layer built 2026-08-04, per explicit direction, on top of (never inside)
the frozen swing_research/ research platform.

CORE PRINCIPLE (Phase 1, per explicit direction): every strategy has TWO
INDEPENDENT statuses that must never be conflated or derived from one
another:

  ResearchVerdict   -- whether the strategy is scientifically validated
                        (PASS / REJECT / INCONCLUSIVE, mirrors
                        swing_research.acceptance_criteria.ACCEPTANCE_VERDICTS
                        exactly -- same three values, same meaning, but
                        this module does NOT import acceptance_criteria.py
                        to avoid coupling the frozen research framework to
                        this new, actively-developed deployment layer).

  DeploymentStatus  -- whether and how the strategy is CURRENTLY being
                        used (RESEARCH / PAPER_TRADING / PILOT_LIVE /
                        PRODUCTION / ARCHIVED).

A REJECT or INCONCLUSIVE research verdict on an already-deployed strategy
NEVER automatically changes its deployment status, and a strategy being
promoted to PAPER_TRADING or beyond NEVER changes its recorded research
verdict. Only an explicit, separate human/caller action changes either
one. This module enforces that separation structurally: set_research_verdict()
and set_deployment_status() (in deployment_manager.py) are two entirely
distinct functions, neither one ever invoked as a side effect of the other.

ISOLATION: this package never imports execution/, cio/, strategies/,
risk/, portfolio/, or config/settings.py. It reuses swing_research/ and
research_lab/ read-only, the same reuse-by-import convention already
established throughout this research program.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ResearchVerdict(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_YET_EVALUATED = "NOT_YET_EVALUATED"   # a strategy registered before its research completes


class DeploymentStatus(str, Enum):
    RESEARCH = "RESEARCH"               # not deployed anywhere -- research-only
    PAPER_TRADING = "PAPER_TRADING"     # live virtual signals/portfolio, zero real capital
    PILOT_LIVE = "PILOT_LIVE"           # small real allocation -- NOT implemented by this
                                          # program, see deployment/pilot_live.py's module docstring
    PRODUCTION = "PRODUCTION"           # full real allocation, live in strategies/+execution/
    ARCHIVED = "ARCHIVED"                # no longer active in any capacity


# Allowed manual transitions -- a strategy's status only ever moves along
# these documented edges, and ARCHIVED is reachable from any state (a
# strategy can always be retired). This is advisory validation, not a hard
# technical barrier against every possible real-world need (see
# deployment_manager.set_deployment_status()'s force parameter) -- but the
# default path always requires an explicit, valid transition, never an
# automatic jump (e.g. straight from RESEARCH to PRODUCTION).
_ALLOWED_TRANSITIONS = {
    DeploymentStatus.RESEARCH: {DeploymentStatus.PAPER_TRADING, DeploymentStatus.ARCHIVED},
    DeploymentStatus.PAPER_TRADING: {DeploymentStatus.PILOT_LIVE, DeploymentStatus.ARCHIVED,
                                      DeploymentStatus.RESEARCH},
    DeploymentStatus.PILOT_LIVE: {DeploymentStatus.PRODUCTION, DeploymentStatus.ARCHIVED,
                                   DeploymentStatus.PAPER_TRADING},
    DeploymentStatus.PRODUCTION: {DeploymentStatus.ARCHIVED, DeploymentStatus.PILOT_LIVE},
    DeploymentStatus.ARCHIVED: set(),
}


def is_valid_transition(from_status: DeploymentStatus, to_status: DeploymentStatus) -> bool:
    if from_status == to_status:
        return True
    return to_status in _ALLOWED_TRANSITIONS.get(from_status, set())


@dataclass
class StrategyRecord:
    """
    One entry in the deployment registry. strategy_key must match the key
    used elsewhere in this program (e.g. swing_research's run_*_experiment
    naming, or a production strategy's own name) so cross-referencing
    Knowledge Base / Strategy Library entries is unambiguous.
    """
    strategy_key: str
    display_name: str
    strategy_family: str   # e.g. "swing_research published strategy", "production strategy"
    research_verdict: ResearchVerdict = ResearchVerdict.NOT_YET_EVALUATED
    research_verdict_source: str = ""   # e.g. exp_ids this verdict is based on, for traceability
    deployment_status: DeploymentStatus = DeploymentStatus.RESEARCH
    deployment_status_history: list = field(default_factory=list)   # [{status, timestamp, reason}]
    notes: str = ""
    # Strategy metadata (added 2026-08-04, Phase 2 operationalization) --
    # declarative facts the scheduler (deployment/scheduler.py) reads to
    # decide WHEN a strategy is due to run, instead of any script
    # hardcoding a schedule per strategy. timeframe: e.g. "Daily", "5 Minute".
    # execution_frequency: "End-of-Day" or "Intraday" -- EOD strategies are
    # refused by the scheduler during NSE market hours (see scheduler.py).
    timeframe: str = "Daily"
    execution_frequency: str = "End-of-Day"
    # PERMANENT identifier (added 2026-08-04, operational hardening) --
    # assigned exactly ONCE, by deployment_manager.register_strategy() on a
    # strategy's FIRST registration, in the "SW-NNN" scheme
    # (deployment_manager._next_strategy_id()). NEVER reassigned or
    # renumbered afterward, even across re-registration attempts (register_strategy()
    # is idempotent and returns the existing record, ID included, unchanged).
    # Appears in every Telegram message, report, review, and library entry
    # this strategy ever produces, so a strategy's identity is stable and
    # traceable across its entire lifecycle.
    strategy_id: str = ""
    # The base-run research experiment this strategy's drift/report
    # references point to (e.g. "EXP-013" for 52-Week High Momentum) --
    # set once via deployment_manager.set_primary_experiment_id(), shown
    # as "Experiment: ..." in every Telegram message/report.
    primary_experiment_id: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy_key": self.strategy_key, "display_name": self.display_name,
            "strategy_family": self.strategy_family,
            "research_verdict": self.research_verdict.value,
            "research_verdict_source": self.research_verdict_source,
            "deployment_status": self.deployment_status.value,
            "deployment_status_history": self.deployment_status_history,
            "notes": self.notes, "timeframe": self.timeframe,
            "execution_frequency": self.execution_frequency,
            "strategy_id": self.strategy_id, "primary_experiment_id": self.primary_experiment_id,
        }

    @staticmethod
    def from_dict(d: dict) -> "StrategyRecord":
        return StrategyRecord(
            strategy_key=d["strategy_key"], display_name=d["display_name"],
            strategy_family=d["strategy_family"],
            research_verdict=ResearchVerdict(d.get("research_verdict", "NOT_YET_EVALUATED")),
            research_verdict_source=d.get("research_verdict_source", ""),
            deployment_status=DeploymentStatus(d.get("deployment_status", "RESEARCH")),
            deployment_status_history=d.get("deployment_status_history", []),
            notes=d.get("notes", ""),
            timeframe=d.get("timeframe", "Daily"),
            execution_frequency=d.get("execution_frequency", "End-of-Day"),
            strategy_id=d.get("strategy_id", ""),
            primary_experiment_id=d.get("primary_experiment_id", ""),
        )
