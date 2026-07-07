"""
Simulates a strategy against historical data, day by day, so we can see
hypothetical performance before any real money is at risk.

Important: to avoid lookahead bias, the strategy only ever sees price history
up to and including "today" when deciding whether to open a trade on "today's"
close. Once a trade is open, we check each subsequent day's High/Low to see
if the stop-loss or target was hit, using whichever came first.
"""

from dataclasses import dataclass, field
import pandas as pd

from strategies.base import Signal
from risk.risk_manager import RiskManager, ApprovedTrade


@dataclass
class ClosedTrade:
    symbol: str
    strategy_name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    stop_loss: float
    target: float
    quantity: int
    pnl: float
    exit_reason: str  # "target", "stop_loss", or "still_open_at_end"


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    ending_capital: float = 0.0
    starting_capital: float = 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades)

    @property
    def max_drawdown(self) -> float:
        if not self.trades:
            return 0.0
        equity = self.starting_capital
        peak = equity
        max_dd = 0.0
        for t in self.trades:
            equity += t.pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    def summary(self) -> str:
        return (
            f"Trades: {len(self.trades)} | "
            f"Win rate: {self.win_rate:.1%} | "
            f"Total P&L: {self.total_pnl:,.2f} | "
            f"Ending capital: {self.ending_capital:,.2f} "
            f"(started at {self.starting_capital:,.2f}) | "
            f"Max drawdown: {self.max_drawdown:.1%}"
        )


def run_backtest(symbol: str, price_history: pd.DataFrame, strategy, risk_manager: RiskManager,
                  regime_series: pd.Series | None = None) -> BacktestResult:
    """
    Runs one strategy against one symbol's full price history.
    Only one open position per symbol at a time (keeps this first version simple).

    regime_series: optional boolean Series (see strategies/market_regime.py).
    If provided, new BUY signals are only taken on days the market itself is
    considered to be in an uptrend -- this is the whipsaw-reduction filter.
    """
    result = BacktestResult(starting_capital=risk_manager.capital)
    open_trade: ApprovedTrade | None = None
    entry_date = None

    min_bars = 55  # a little more than the slow MA period, so signals have enough history

    for i in range(min_bars, len(price_history)):
        window = price_history.iloc[: i + 1]  # only data up to and including "today"
        today_date = str(price_history.index[i].date())
        today_row = price_history.iloc[i]

        if open_trade is not None:
            hit_stop = today_row["Low"] <= open_trade.signal.stop_loss
            hit_target = today_row["High"] >= open_trade.signal.target

            if hit_stop or hit_target:
                exit_price = open_trade.signal.stop_loss if hit_stop else open_trade.signal.target
                pnl = (exit_price - open_trade.signal.entry_price) * open_trade.quantity
                risk_manager.on_trade_closed(open_trade, pnl)

                result.trades.append(ClosedTrade(
                    symbol=symbol,
                    strategy_name=open_trade.signal.strategy_name,
                    entry_date=entry_date,
                    exit_date=today_date,
                    entry_price=open_trade.signal.entry_price,
                    exit_price=exit_price,
                    stop_loss=open_trade.signal.stop_loss,
                    target=open_trade.signal.target,
                    quantity=open_trade.quantity,
                    pnl=pnl,
                    exit_reason="stop_loss" if hit_stop else "target",
                ))
                open_trade = None
            continue

        signal = strategy.generate_signal(window)
        if signal is None:
            continue

        if regime_series is not None:
            from strategies.market_regime import is_bullish_on
            if signal.direction == "BUY" and not is_bullish_on(regime_series, price_history.index[i]):
                continue

        signal.symbol = symbol
        approved = risk_manager.evaluate(signal)
        if approved is None:
            continue

        risk_manager.on_trade_opened(approved)
        open_trade = approved
        entry_date = today_date

    if open_trade is not None:
        last_price = float(price_history["Close"].iloc[-1])
        pnl = (last_price - open_trade.signal.entry_price) * open_trade.quantity
        risk_manager.on_trade_closed(open_trade, pnl)
        result.trades.append(ClosedTrade(
            symbol=symbol,
            strategy_name=open_trade.signal.strategy_name,
            entry_date=entry_date,
            exit_date=str(price_history.index[-1].date()),
            entry_price=open_trade.signal.entry_price,
            exit_price=last_price,
            stop_loss=open_trade.signal.stop_loss,
            target=open_trade.signal.target,
            quantity=open_trade.quantity,
            pnl=pnl,
            exit_reason="still_open_at_end",
        ))

    result.ending_capital = result.starting_capital + result.total_pnl
    return result
