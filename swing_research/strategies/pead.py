"""
Post-Earnings Announcement Drift (PEAD) -- EXIT-SIDE-ONLY Strategy shim.

PEAD's trigger is an EARNINGS ANNOUNCEMENT EVENT, not a daily technical/
cross-sectional bar condition -- it does not fit swing_research.base.
Strategy's row-based entry_signal_at() interface at all. Real entries are
decided separately by deployment/pead_forward_engine.py (fetches recent
earnings events, computes SUE via deployment/pead_signal.py, and injects
a position directly into the paper-trading portfolio state) -- NEVER by
this class's entry_signal_at(), which always returns None, deliberately.

This class exists ONLY so PEAD's EXIT side (holding-period time-stop +
the standard protective stop-loss) can reuse
deployment/paper_trading_engine.py's existing, already-tested run_daily()
exit machinery completely unchanged, rather than duplicating exit logic
for a single strategy.

No backtest exists for this strategy (Research Verdict: NOT_YET_EVALUATED,
unchanged -- see swing_research/strategy_library/pead.md) -- this file is
used ONLY by the forward, real-money-free paper-trading pipeline, never
by run_swing_experiment.py or any research_director.py run_*_experiment
function.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy
from deployment.pead_signal import PEAD_HOLDING_PERIOD_TRADING_DAYS, PEAD_RISK_PCT_PER_UNIT

HOLDING_PERIOD_CALENDAR_DAYS = round(PEAD_HOLDING_PERIOD_TRADING_DAYS / 252 * 365.25)


class PEADStrategy(Strategy):
    name = "pead"
    max_units = 1
    risk_pct_per_unit = PEAD_RISK_PCT_PER_UNIT
    min_lookback_days = 0   # no technical lookback needed -- entries are event-driven, injected externally

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        return None   # deliberate -- see module docstring

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        entry_date = open_position.units[0].entry_date
        if entry_date is not None and (row.date - entry_date).days >= HOLDING_PERIOD_CALENDAR_DAYS:
            return float(row.Close)
        return None
