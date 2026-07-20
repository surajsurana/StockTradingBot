"""
Proxy analysis for the price-action facts now fed into the Research
Analyst (strategies/price_action.py) -- NOT a full backtest of the LLM
verdict itself, since that would require real historical news headlines
(unavailable -- RSS only ever gives today's headlines) and real Claude
calls for every historical day (slow, costly, and still not a valid
historical replay if the "news" input is wrong).

Instead: for every real stop-loss exit produced by the two live strategies
over the reporting window, walks forward from entry day-by-day and finds
the first day a concrete price-action warning condition is true (price
below its own 50-day MA AND today is a down day on >=1.5x average volume).
Reports how many trading days BEFORE the actual stop-loss exit that
warning would have first appeared -- i.e., whether the new inputs carry
real advance signal, independent of what the LLM would ultimately have
decided to do with it (which also weighs fundamentals and news).

    python backtest_price_action_lead_time.py [--months=3] [--limit=150]
"""

import sys
import pandas as pd

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series, is_bullish_on
from strategies.technical_agent import STRATEGY_REGISTRY
from strategies.price_action import compute_price_action
from fundamentals.fundamental_agent import filter_universe
from risk.risk_manager import RiskManager


def parse_args():
    months = 3
    limit = 150
    for arg in sys.argv[1:]:
        if arg.startswith("--months="):
            months = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    return months, limit


def find_stop_loss_trades(symbol, price_history, strategy, regime_series, reporting_start_date):
    """Same day-by-day loop as backtest/backtester.py, but only returns
    trades that actually exited via stop_loss (the ones a warning could
    plausibly have helped) with their entry/exit index positions."""
    risk_manager = RiskManager(
        capital=settings.STARTING_CAPITAL, risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
        daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
    )
    stop_loss_trades = []
    open_trade = None
    entry_i = None
    min_bars = 55

    for i in range(min_bars, len(price_history)):
        window = price_history.iloc[: i + 1]
        today_row = price_history.iloc[i]

        if open_trade is not None:
            hit_stop = today_row["Low"] <= open_trade.signal.stop_loss
            hit_target = today_row["High"] >= open_trade.signal.target
            if hit_stop or hit_target:
                entry_date = price_history.index[entry_i].date()
                if hit_stop and entry_date >= reporting_start_date:
                    stop_loss_trades.append({
                        "symbol": symbol, "entry_i": entry_i, "exit_i": i,
                        "entry_price": open_trade.signal.entry_price,
                    })
                pnl = ((open_trade.signal.stop_loss if hit_stop else open_trade.signal.target)
                       - open_trade.signal.entry_price) * open_trade.quantity
                risk_manager.on_trade_closed(open_trade, pnl)
                open_trade = None
            continue

        signal = strategy.generate_signal(window)
        if signal is None:
            continue
        if regime_series is not None and signal.direction == "BUY" and not is_bullish_on(regime_series, price_history.index[i]):
            continue
        signal.symbol = symbol
        approved = risk_manager.evaluate(signal)
        if approved is None:
            continue
        risk_manager.on_trade_opened(approved)
        open_trade = approved
        entry_i = i

    return stop_loss_trades


def warning_lead_time_days(price_history, entry_i, exit_i, entry_price):
    """
    First day (as a trading-day count from entry) where price is below its
    own 50-day MA AND today is a down day on >=1.5x average volume. Returns
    (lead_time_days, warned_at_all) -- lead_time_days is how many trading
    days BEFORE the actual exit this first fired, or None if it never fired.
    """
    for i in range(entry_i, exit_i):
        window = price_history.iloc[: i + 1]
        pa = compute_price_action(window, entry_price=entry_price)
        if pa is None:
            continue
        if pa.above_50ma is False and pa.is_down_day and pa.volume_ratio is not None and pa.volume_ratio >= 1.5:
            return exit_i - i, True
    return None, False


def main():
    months, limit = parse_args()
    period = "18mo"

    symbols = get_nifty500_symbols()[:limit]
    print(f"Universe: {len(symbols)} symbol(s), fetch period: {period}, reporting last {months} month(s)")

    print("\nFundamentals health check...")
    eligible_symbols, _ = filter_universe(symbols, settings.FUNDAMENTALS_CRITERIA)
    print(f"Eligible after fundamentals: {len(eligible_symbols)}")

    print(f"\nFetching {period} of historical data...")
    data = fetch_all(sorted(eligible_symbols), period=period)
    print(f"Data fetched for {len(data)} symbol(s)")

    print("Fetching Nifty 50 index for the regime filter...")
    nifty = fetch_nifty(period=period)
    regime_series = build_regime_series(nifty)

    reporting_start_date = (pd.Timestamp.now() - pd.DateOffset(months=months)).date()

    all_stop_loss_trades = []
    for symbol in sorted(eligible_symbols):
        price_history = data.get(symbol)
        if price_history is None or len(price_history) < 60:
            continue
        for strategy_key, strategy_cls in STRATEGY_REGISTRY.items():
            strategy = strategy_cls()
            effective_regime = regime_series if strategy.uses_regime_filter else None
            trades = find_stop_loss_trades(symbol, price_history, strategy, effective_regime, reporting_start_date)
            for t in trades:
                t["price_history"] = price_history
            all_stop_loss_trades += trades

    print(f"\nFound {len(all_stop_loss_trades)} real stop-loss exit(s) in the last {months} month(s)")

    lead_times = []
    never_warned = 0
    for t in all_stop_loss_trades:
        lead_time, warned = warning_lead_time_days(t["price_history"], t["entry_i"], t["exit_i"], t["entry_price"])
        if warned:
            lead_times.append(lead_time)
        else:
            never_warned += 1

    print("\n" + "=" * 70)
    print("PRICE-ACTION WARNING LEAD TIME (proxy analysis, not an LLM replay)")
    print("=" * 70)
    print(f"Stop-loss exits with an advance warning: {len(lead_times)}/{len(all_stop_loss_trades)}")
    print(f"Stop-loss exits with NO advance warning (only visible the day of, or never): {never_warned}")
    if lead_times:
        print(f"Average lead time: {sum(lead_times) / len(lead_times):.1f} trading days before the stop-loss")
        print(f"Median lead time: {sorted(lead_times)[len(lead_times)//2]} trading days")
        print(f"Min/Max lead time: {min(lead_times)}/{max(lead_times)} trading days")


if __name__ == "__main__":
    main()
