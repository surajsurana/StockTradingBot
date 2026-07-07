"""
Sits between strategies and execution. A strategy proposes a trade; this
decides how big it should actually be, or whether it should happen at all,
based on portfolio-wide rules — so no single strategy (or bug in one) can
over-leverage the account.
"""

from dataclasses import dataclass
from strategies.base import Signal


@dataclass
class ApprovedTrade:
    signal: Signal
    quantity: int
    capital_deployed: float


class RiskManager:
    def __init__(
        self,
        capital: float,
        risk_per_trade_pct: float,
        max_open_positions: int,
        max_deployed_capital_pct: float,
        daily_loss_circuit_breaker_pct: float,
    ):
        self.capital = capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.max_deployed_capital_pct = max_deployed_capital_pct
        self.daily_loss_circuit_breaker_pct = daily_loss_circuit_breaker_pct

        # live state, updated as the day/backtest progresses
        self.open_positions_count = 0
        self.capital_deployed = 0.0
        self.realized_pnl_today = 0.0

    def daily_loss_breached(self) -> bool:
        return self.realized_pnl_today <= -(self.capital * self.daily_loss_circuit_breaker_pct)

    def evaluate(self, signal: Signal, risk_pct_override: float | None = None) -> ApprovedTrade | None:
        """
        Returns an ApprovedTrade with sized quantity, or None if the trade is rejected.

        risk_pct_override: lets a caller (e.g. Portfolio Manager) size this specific
        trade at a different risk percentage than the account default -- e.g. a
        confidence-weighted amount. Hard safety limits below (circuit breaker, max
        open positions, max deployed capital) are NOT affected by this override --
        those still apply exactly as they would for a default-sized trade. This is
        deliberate: no per-trade confidence score should be able to bypass the
        account-wide safety ceilings.
        """

        if self.daily_loss_breached():
            return None  # circuit breaker tripped, no new positions today

        if self.open_positions_count >= self.max_open_positions:
            return None  # already at max concurrent positions

        risk_pct = risk_pct_override if risk_pct_override is not None else self.risk_per_trade_pct
        risk_amount = self.capital * risk_pct
        risk_per_share = signal.entry_price - signal.stop_loss
        if risk_per_share <= 0:
            return None  # bad signal, shouldn't happen but guard anyway

        quantity = int(risk_amount / risk_per_share)
        if quantity <= 0:
            return None  # position would be too small to size meaningfully

        capital_needed = quantity * signal.entry_price
        max_allowed_deployed = self.capital * self.max_deployed_capital_pct

        if self.capital_deployed + capital_needed > max_allowed_deployed:
            # shrink the position to fit within the remaining capital budget
            remaining_budget = max_allowed_deployed - self.capital_deployed
            if remaining_budget <= 0:
                return None
            quantity = int(remaining_budget / signal.entry_price)
            if quantity <= 0:
                return None
            capital_needed = quantity * signal.entry_price

        return ApprovedTrade(signal=signal, quantity=quantity, capital_deployed=capital_needed)

    def on_trade_opened(self, trade: ApprovedTrade):
        self.open_positions_count += 1
        self.capital_deployed += trade.capital_deployed

    def on_trade_closed(self, trade: ApprovedTrade, realized_pnl: float):
        self.open_positions_count -= 1
        self.capital_deployed -= trade.capital_deployed
        self.realized_pnl_today += realized_pnl

    def reset_day(self):
        self.realized_pnl_today = 0.0
