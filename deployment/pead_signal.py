"""
Post-Earnings Announcement Drift (PEAD) signal rules -- the "already
established rules" referenced by swing_research/strategy_library/pead.md:
Bernard, V. and Thomas, J. (1989/1990); original Standardized Unexpected
Earnings (SUE) construction: Foster, Olsen and Shevlin (1984).

FAITHFUL TO THE ORIGINAL SUE CONSTRUCTION (the seasonal random-walk
model, NOT an analyst-estimate-based surprise): SUE_q = (EPS_q - EPS_q-4)
/ sigma, where sigma is the standard deviation of the trailing 8
quarters' own (EPS_k - EPS_k-4) year-over-year differences. This is the
ORIGINAL Foster/Olsen/Shevlin (1984) measure Bernard & Thomas's own
papers primarily used -- deliberately NOT yfinance's own "Surprise(%)"
column (actual vs. ANALYST estimate), which is a different, more modern
operationalization. Chosen specifically because it needs ONLY historical
ACTUAL (reported) EPS -- no analyst-estimate history required at all,
sidestepping the exact data gap (analyst-estimate history) that the
original 2026-08-05 PEAD deferral investigation found unavailable via any
integrated source. See data/fetch_earnings_calendar.py's own docstring
for the live-tested finding (2026-08-17) that ~24 quarters of actual EPS
IS available going forward via yfinance's get_earnings_dates() -- a
different method from the one checked during that original investigation.

DISCLOSED ADAPTATION (approved 2026-08-17, NOT invented silently): every
other cross-sectional strategy in this program ranks its signal by
PERCENTILE against the whole universe at a single point in time -- that
requires either a complete, simultaneous cross-section (batch backtesting)
or a rolling-window aggregation for a live, incrementally-arriving event
stream (a new design choice this program hasn't needed before). Given
Indian quarterly results file within ~45 days of quarter-end and cluster
heavily within reporting windows, an ABSOLUTE SUE threshold is used
instead for this FORWARD-ONLY pipeline -- SUE magnitude is itself
well-documented in the PEAD literature as monotonically related to drift
strength (not just the DECILE RANK), so a fixed, round-number threshold
(PEAD_SUE_THRESHOLD = +2.0, a standard "extreme positive surprise" cutoff
across multiple published replications) is a disclosed, faithful-enough
operationalization for this forward stream -- NOT a cross-sectional
decile sort like SW-003/SW-006/SW-008/SW-009/SW-010. If/when enough
forward events accumulate to support a genuine rolling cross-sectional
rank, that would be a deliberate, separate, approved change -- not
silently substituted here.
"""

from dataclasses import dataclass
from statistics import pstdev
from typing import Optional

PEAD_SUE_THRESHOLD = 2.0             # long entry: SUE > this. Disclosed, round-number, standard-in-literature.
PEAD_MIN_TRAILING_QUARTERS = 12      # current quarter + 8 trailing YoY diffs each needing a -4 comparator
PEAD_HOLDING_PERIOD_TRADING_DAYS = 60   # ~1 quarter -- Bernard & Thomas's own standard PEAD holding window
PEAD_STOP_LOSS_PCT = 0.08            # NOT part of the original methodology -- same disclosed pattern as every
                                       # other strategy in this program (source papers have zero position-level
                                       # risk management).
PEAD_RISK_PCT_PER_UNIT = 0.01        # same disclosed pattern, not documented in the source.


@dataclass
class SUEResult:
    sue: Optional[float]
    sufficient_history: bool
    reason: str   # human-readable, for the event log


def compute_sue(trailing_actual_eps: list) -> SUEResult:
    """
    trailing_actual_eps: [most recent quarter .. oldest], as returned by
    data/fetch_earnings_calendar.EarningsEvent.trailing_actual_eps.

    SUE_q = (EPS[0] - EPS[4]) / sigma, sigma = population stdev of
    (EPS[i] - EPS[i+4]) for i in 0..7 -- needs EPS[0..11] all present
    (PEAD_MIN_TRAILING_QUARTERS = 12). Returns sufficient_history=False
    (sue=None) rather than silently computing from a shorter window --
    an insufficient-history event is still LOGGED (see
    deployment/pead_forward_engine.py), just never becomes a signal.
    """
    if len(trailing_actual_eps) < PEAD_MIN_TRAILING_QUARTERS:
        return SUEResult(sue=None, sufficient_history=False,
                          reason=f"Only {len(trailing_actual_eps)} trailing quarters of actual EPS available, "
                                 f"need {PEAD_MIN_TRAILING_QUARTERS} for a faithful SUE calculation.")

    diffs = [trailing_actual_eps[i] - trailing_actual_eps[i + 4] for i in range(8)]
    sigma = pstdev(diffs)
    if sigma == 0:
        return SUEResult(sue=None, sufficient_history=False,
                          reason="Zero variance in trailing YoY EPS differences -- SUE undefined (division by zero).")

    sue = (trailing_actual_eps[0] - trailing_actual_eps[4]) / sigma
    return SUEResult(sue=round(sue, 4), sufficient_history=True, reason="OK")


def evaluate_pead_signal(sue_result: SUEResult, threshold: float = PEAD_SUE_THRESHOLD) -> tuple:
    """Returns (signal_generated: bool, reason: str)."""
    if not sue_result.sufficient_history:
        return False, sue_result.reason
    if sue_result.sue > threshold:
        return True, f"SUE {sue_result.sue} > threshold {threshold}"
    return False, f"SUE {sue_result.sue} <= threshold {threshold}"
