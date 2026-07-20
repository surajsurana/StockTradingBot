"""
Fetches historical data ONCE, then sweeps several (activation_fraction,
lock_in_fraction) combinations for the trailing stop (risk/trailing_stop.py)
against the same real signals, so we can pick sensible defaults instead of
guessing -- see backtest_trailing_stop.py's docstring for why the original
flat-percentage version was rejected (it clipped nearly every winner).

    python backtest_trailing_stop_sweep.py [--months=3] [--limit=150]
"""

import sys
import pandas as pd

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series
from strategies.technical_agent import STRATEGY_REGISTRY
from fundamentals.fundamental_agent import filter_universe
from risk.risk_manager import RiskManager
from backtest_trailing_stop import run_symbol


def parse_args():
    months = 3
    limit = 150
    for arg in sys.argv[1:]:
        if arg.startswith("--months="):
            months = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    return months, limit


CONFIGS = [
    ("baseline (no trailing stop)", None, None),
    ("activation=0.8, lockin=0.6", 0.8, 0.6),
    ("activation=0.8, lockin=0.7", 0.8, 0.7),
    ("activation=0.85, lockin=0.65", 0.85, 0.65),
    ("activation=0.9, lockin=0.7", 0.9, 0.7),
    ("activation=0.9, lockin=0.8", 0.9, 0.8),
    ("activation=0.95, lockin=0.8", 0.95, 0.8),
    ("activation=1.0, lockin=0.8", 1.0, 0.8),
]


def main():
    months, limit = parse_args()
    period = "18mo"

    symbols = get_nifty500_symbols()[:limit]
    print(f"Universe: {len(symbols)} symbol(s), fetch period: {period}, reporting last {months} month(s)")

    print("\nFundamentals health check...")
    eligible_symbols, _ = filter_universe(symbols, settings.FUNDAMENTALS_CRITERIA)
    print(f"Eligible after fundamentals: {len(eligible_symbols)}")

    print(f"\nFetching {period} of historical data (once, reused for every config)...")
    data = fetch_all(sorted(eligible_symbols), period=period)
    print(f"Data fetched for {len(data)} symbol(s)")

    print("Fetching Nifty 50 index for the regime filter...")
    nifty = fetch_nifty(period=period)
    regime_series = build_regime_series(nifty)

    reporting_start_date = (pd.Timestamp.now() - pd.DateOffset(months=months)).date()

    def risk_manager_factory():
        return RiskManager(
            capital=settings.STARTING_CAPITAL, risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
            daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
        )

    print("\n" + "=" * 100)
    print(f"{'CONFIG':<32}{'Trades':>8}{'WinRate':>10}{'TotalP&L':>14}{'AvgWin':>10}{'AvgLoss':>10}{'TargetHits':>12}")
    print("=" * 100)

    for label, activation_fraction, lock_in_fraction in CONFIGS:
        use_trailing = activation_fraction is not None
        all_trades = []
        for symbol in sorted(eligible_symbols):
            price_history = data.get(symbol)
            if price_history is None or len(price_history) < 60:
                continue
            for strategy_key, strategy_cls in STRATEGY_REGISTRY.items():
                strategy = strategy_cls()
                effective_regime = regime_series if strategy.uses_regime_filter else None
                all_trades += run_symbol(symbol, price_history, strategy, effective_regime,
                                          risk_manager_factory, reporting_start_date,
                                          use_trailing_stop=use_trailing,
                                          activation_fraction=activation_fraction,
                                          lock_in_fraction=lock_in_fraction)

        if not all_trades:
            print(f"{label:<32}{'no trades':>8}")
            continue
        total_pnl = sum(t["pnl"] for t in all_trades)
        wins = [t for t in all_trades if t["pnl"] > 0]
        losses = [t for t in all_trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(all_trades) * 100
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        target_hits = sum(1 for t in all_trades if t["exit_reason"] == "target")
        print(f"{label:<32}{len(all_trades):>8}{win_rate:>9.1f}%{total_pnl:>13,.0f} {avg_win:>9,.0f} {avg_loss:>9,.0f}"
              f"{target_hits:>9}/{len(all_trades)}")


if __name__ == "__main__":
    main()
