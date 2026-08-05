"""
Metadata-driven scheduler (2026-08-04) -- decides which registered
strategies are due to run RIGHT NOW, based on each strategy's own
declared timeframe/execution_frequency (deployment/base.py's
StrategyRecord), rather than any script hardcoding a schedule.

NO OS-LEVEL SCHEDULING IS BUILT HERE, per explicit direction -- this
module only answers "given the current time, which strategies SHOULD run
if triggered now," and enforces the EOD-only market-hours guard. An
external trigger (Task Scheduler, or a manual run) still has to actually
invoke run_paper_trading.py; see that script's docstring for the
recommended cadence.

EOD MARKET-HOURS GUARD (explicit requirement, 2026-08-04): published
swing strategies (End-of-Day execution_frequency) must NOT generate
signals during market hours -- their signals are only valid once the
day's candle is complete. This module's is_market_open() is a
SIMPLIFIED check (fixed 09:15-15:30 IST window, Monday-Friday) --
DISCLOSED LIMITATION: it does NOT account for NSE trading holidays. A
holiday would be treated as "market closed all day" (safe -- an EOD
strategy would just correctly find no new bar to process that day), not
as a false "market open" state, so this simplification cannot cause a
premature/incorrect EOD signal.
"""

from datetime import datetime, time as time_type
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except ImportError:   # pragma: no cover -- zoneinfo is stdlib on this program's Python version
    _IST = None

MARKET_OPEN_TIME = time_type(9, 15)
MARKET_CLOSE_TIME = time_type(15, 30)


def is_market_open(now: Optional[datetime] = None) -> bool:
    """Simplified NSE market-hours check -- see module docstring for the
    disclosed holiday-calendar limitation."""
    now = now or (datetime.now(_IST) if _IST else datetime.now())
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME


def is_due_now(record, now: Optional[datetime] = None) -> tuple:
    """
    Returns (due: bool, reason: str). A strategy is due now if:
      - its deployment_status is PAPER_TRADING, PILOT_LIVE, or PRODUCTION
        (RESEARCH/ARCHIVED strategies never run live), AND
      - for "End-of-Day" execution_frequency: the market is CLOSED right
        now (refuses to run during market hours, per explicit
        requirement) -- there is no further time-of-day restriction
        beyond "not during market hours," so any post-close invocation is
        due (idempotency in paper_trading_engine.run_daily() already
        prevents double-processing the same day).
      - for "Intraday" execution_frequency: due only while the market IS
        open (a future capability -- no intraday strategy exists in this
        program yet; included for completeness per the metadata design).
    """
    from deployment.base import DeploymentStatus

    if record.deployment_status not in (DeploymentStatus.PAPER_TRADING, DeploymentStatus.PILOT_LIVE,
                                          DeploymentStatus.PRODUCTION):
        return False, f"deployment_status is {record.deployment_status.value}, not an active trading status"

    market_open = is_market_open(now)
    if record.execution_frequency == "End-of-Day":
        if market_open:
            return False, "End-of-Day strategy refuses to run while the market is open"
        return True, "market closed -- End-of-Day strategy is due"
    if record.execution_frequency == "Intraday":
        if not market_open:
            return False, "Intraday strategy has nothing to do while the market is closed"
        return True, "market open -- Intraday strategy is due"

    return False, f"unrecognized execution_frequency '{record.execution_frequency}'"


def strategies_due_now(records: list, now: Optional[datetime] = None) -> list:
    """Filters a list of StrategyRecords to those currently due, per is_due_now()."""
    return [r for r in records if is_due_now(r, now)[0]]
