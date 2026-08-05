"""
Pilot Live eligibility + allocation-sizing scaffold (Phase 6).

*** THIS MODULE CONTAINS NO EXECUTION CAPABILITY. *** It never imports
execution/, never constructs a broker order, never sends a network
request, never moves real capital. It answers exactly one question --
"is this strategy ELIGIBLE to be considered for Pilot Live, and if so
what would a small pilot allocation look like" -- as pure data/config,
for a human to read and act on manually, entirely outside this codebase's
automation.

Activating Pilot Live for real (wiring a strategy into strategies/ +
execution/ with real capital) is DELIBERATELY NOT built here and is not
planned to be built by any part of the Research Deployment program. That
step requires a separate, explicit, human-approved change outside this
program's automation -- consistent with this program's own standing rule
that no research or deployment-tracking code should be able to move real
capital or place real orders.
"""

from dataclasses import dataclass
from typing import Optional

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord

MIN_PAPER_TRADING_DAYS_ELAPSED = 60     # ~3 trading months, a floor not a guarantee of sufficiency
MIN_PAPER_TRADING_TRADES = 20           # enough for the live sample to be more than anecdotal
DEFAULT_PILOT_ALLOCATION_PCT = 5.0      # config-driven, per explicit direction ("for example 5%")


@dataclass
class PilotEligibilityResult:
    eligible: bool
    reasons: list
    recommended_allocation_pct: float = 0.0


def check_pilot_eligibility(record: StrategyRecord, paper_trading_days_elapsed: int,
                             paper_trading_trade_count: int,
                             min_days: int = MIN_PAPER_TRADING_DAYS_ELAPSED,
                             min_trades: int = MIN_PAPER_TRADING_TRADES) -> PilotEligibilityResult:
    """
    Pure evaluation function -- no side effects, does not read or write
    any state itself (caller supplies the facts). Eligibility is
    NECESSARY-but-not-sufficient: even an eligible strategy per this
    function requires a SEPARATE, explicit CIO Research Review
    recommendation (deployment/lifecycle_review.py) AND a human decision
    (deployment_manager.set_deployment_status()) before anything changes.
    This function alone can never move a strategy to PILOT_LIVE.
    """
    reasons = []

    if record.research_verdict != ResearchVerdict.PASS:
        reasons.append(f"Research Verdict is {record.research_verdict.value}, not PASS.")
    if record.deployment_status != DeploymentStatus.PAPER_TRADING:
        reasons.append(f"Deployment Status is {record.deployment_status.value}, not PAPER_TRADING "
                        f"(a strategy must be actively paper trading before Pilot Live).")
    if paper_trading_days_elapsed < min_days:
        reasons.append(f"Only {paper_trading_days_elapsed} paper-trading days elapsed, "
                        f"below the {min_days}-day floor.")
    if paper_trading_trade_count < min_trades:
        reasons.append(f"Only {paper_trading_trade_count} paper trades recorded, "
                        f"below the {min_trades}-trade floor.")

    eligible = len(reasons) == 0
    return PilotEligibilityResult(
        eligible=eligible, reasons=reasons,
        recommended_allocation_pct=DEFAULT_PILOT_ALLOCATION_PCT if eligible else 0.0,
    )
