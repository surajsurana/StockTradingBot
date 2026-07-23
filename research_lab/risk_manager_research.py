"""
Risk Manager (research mode) -- researches stop-loss method, position
sizing, daily loss limits, and max-trades-per-day as BACKTESTABLE INPUTS
the Backtesting Engineer varies. This is not a live risk system and is
never imported by the real risk/risk_manager.py or vice versa -- that
module stays the sole authority for actual live trading.

Distinct from the earlier ad hoc ORB backtest, which had no daily loss
limit or max-trades-per-day concept at all -- a real intraday strategy
needs its own same-day circuit breaker, arguably more so than swing given
the much higher trade frequency (a bad morning can compound fast across
many trades in one day, unlike swing's occasional trade). Reimplemented
independently here rather than importing risk/risk_manager.py's version,
per the isolation requirement.
"""

from dataclasses import dataclass


@dataclass
class RiskParameters:
    risk_per_trade_pct: float = 0.01     # fraction of capital risked per trade (entry-to-stop distance)
    max_trades_per_day: int = 3          # per symbol -- caps how many round-trips one symbol can do in a day
    daily_loss_limit_pct: float = 0.02   # stop taking new trades (per symbol) once today's realized loss exceeds this
    stop_loss_method: str = "signal_based"  # trusts the Hypothesis's own stop_loss rule; a placeholder
                                             # for researching alternative methods (ATR-based, fixed-%, etc.)
                                             # in a future iteration without changing this dataclass's shape


def should_block_new_trade(realized_pnl_today: float, trades_taken_today: int,
                            capital: float, params: RiskParameters) -> bool:
    """Pure function: given today's realized P&L and trade count so far
    for one symbol, should a new trade be blocked? Used by
    backtesting_engineer.simulate_symbol() when a RiskParameters object is
    passed in -- defaults to effectively unlimited (matching the earlier
    ad hoc backtest's behavior) unless the caller opts into these limits."""
    if trades_taken_today >= params.max_trades_per_day:
        return True
    if realized_pnl_today < 0 and abs(realized_pnl_today) >= capital * params.daily_loss_limit_pct:
        return True
    return False
