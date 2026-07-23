"""
Research Lab Portfolio Manager -- distinct from and never importing
portfolio/portfolio_manager.py (the real swing Portfolio Manager, which
must never be touched, see PROJECT_CONTEXT.md). This one owns the capital
allocation question PART 8 of the brief asked about, scoped to what's
real right now: intraday is research-only, so it operates on VIRTUAL
capital, never real Kite account funds.

config.settings.INTRADAY_CAPITAL_ALLOCATION_PCT defaults to 0 -- until a
research_lab strategy is explicitly promoted to production in a future,
separately-approved phase, this always returns a research-only number.
Promoting a strategy means: (1) raising INTRADAY_CAPITAL_ALLOCATION_PCT
above 0, AND (2) making the real risk/risk_manager.py aware it no longer
has 100% of capital -- that second step is itself a deliberate, separate
future change, not implied by changing this setting. Swing continues to
assume 100% capital until that explicit change is made.
"""

from config import settings


def get_intraday_research_capital() -> float:
    """Virtual capital available for research_lab backtests -- never real
    money, regardless of how INTRADAY_CAPITAL_ALLOCATION_PCT is set."""
    return settings.RESEARCH_LAB_VIRTUAL_CAPITAL * settings.INTRADAY_CAPITAL_ALLOCATION_PCT / 100


def get_swing_capital_allocation_pct() -> float:
    """Read-only visibility into what swing's share of the pool would be
    once/if allocations become real -- swing itself doesn't call this."""
    return settings.SWING_CAPITAL_ALLOCATION_PCT


def is_intraday_promoted_to_production() -> bool:
    """False by design in this phase. A future phase flips
    INTRADAY_CAPITAL_ALLOCATION_PCT above 0 only after the real
    RiskManager has also been updated -- this function is here so that
    future code has one obvious place to check "is this real yet", rather
    than each caller re-deriving it from the raw percentage."""
    return settings.INTRADAY_CAPITAL_ALLOCATION_PCT > 0
