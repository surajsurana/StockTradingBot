"""
Standalone backtest of the RsiMacdConfluenceStrategy prototype
(strategies/rsi_macd_confluence.py) against the Nifty 500 universe,
independent of main.py/config.ACTIVE_STRATEGIES so this can be evaluated
without touching the live system at all. Same structure as
backtest_pullback_continuation.py -- see that script's own docstring for
the shared conventions.

    python backtest_rsi_macd_confluence.py [--years=N] [--limit=N]

Prints combined trade count, win rate, P&L, and max drawdown, plus trades
per month so we can judge fit against a multi-day swing-holding style.
"""

import sys

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series
from strategies.rsi_macd_confluence import RsiMacdConfluenceStrategy
from fundamentals.fundamental_agent import filter_universe
from risk.risk_manager import RiskManager
from backtest.backtester import run_backtest, BacktestResult


def parse_args():
    years = 3
    limit = 150
    for arg in sys.argv[1:]:
        if arg.startswith("--years="):
            years = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    return years, limit


def main():
    years, limit = parse_args()
    period = f"{years}y"

    symbols = get_nifty500_symbols()[:limit]
    print(f"Universe: {len(symbols)} symbol(s), period: {period}")

    print("\nFundamentals health check...")
    eligible_symbols, _ = filter_universe(symbols, settings.FUNDAMENTALS_CRITERIA)
    print(f"Eligible after fundamentals: {len(eligible_symbols)}")

    print(f"\nFetching {period} of historical data...")
    data = fetch_all(sorted(eligible_symbols), period=period)
    print(f"Data fetched for {len(data)} symbol(s)")

    print("Fetching Nifty 50 index for the regime filter...")
    nifty = fetch_nifty(period=period)
    regime_series = build_regime_series(nifty)

    strategy = RsiMacdConfluenceStrategy()
    combined = BacktestResult(starting_capital=settings.STARTING_CAPITAL)
    holding_days = []

    for symbol in sorted(eligible_symbols):
        price_history = data.get(symbol)
        if price_history is None or len(price_history) < 60:
            continue

        risk_manager = RiskManager(
            capital=settings.STARTING_CAPITAL,
            risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
            daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
        )
        result = run_backtest(symbol, price_history, strategy, risk_manager, regime_series=regime_series)
        if result.trades:
            print(f"  {symbol}: {result.summary()}")
        combined.trades.extend(result.trades)

    combined.ending_capital = combined.starting_capital + combined.total_pnl

    import pandas as pd
    for t in combined.trades:
        try:
            days = (pd.Timestamp(t.exit_date) - pd.Timestamp(t.entry_date)).days
            holding_days.append(days)
        except Exception:
            pass

    months = years * 12
    print("\n" + "=" * 70)
    print(f"RSI+MACD CONFLUENCE -- {len(eligible_symbols)} symbols, {period} backtest")
    print("=" * 70)
    print(combined.summary())
    print(f"Trades per month (avg): {len(combined.trades) / months:.2f}")
    if holding_days:
        print(f"Avg holding period: {sum(holding_days) / len(holding_days):.1f} calendar days "
              f"(min {min(holding_days)}, max {max(holding_days)})")
    wins = [t for t in combined.trades if t.pnl > 0]
    losses = [t for t in combined.trades if t.pnl <= 0]
    if wins:
        print(f"Avg win: Rs.{sum(t.pnl for t in wins) / len(wins):,.2f}")
    if losses:
        print(f"Avg loss: Rs.{sum(t.pnl for t in losses) / len(losses):,.2f}")
    exit_reasons = {}
    for t in combined.trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    print(f"Exit reasons: {exit_reasons}")


if __name__ == "__main__":
    main()
