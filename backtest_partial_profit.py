"""
Backtests partial profit booking (risk/partial_profit.py) ON TOP OF the
already-validated trailing stop (both run together, matching what actually
runs live) against real signals from the two active strategies, comparing:
  - baseline: trailing stop only (today's live configuration)
  - with partial profit booking: trailing stop + partial booking

Same long-fetch-window-vs-short-reporting-window approach as the other
backtests this session (see backtest_trailing_stop.py) so the 200-day
market-regime filter has real history before the reporting window starts.

    python backtest_partial_profit.py [--months=3] [--limit=150]
    python backtest_partial_profit.py --sweep   # try a few parameter combos
"""

import sys
import pandas as pd

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series, is_bullish_on
from strategies.technical_agent import STRATEGY_REGISTRY
from fundamentals.fundamental_agent import filter_universe
from risk.risk_manager import RiskManager
from risk.trailing_stop import compute_trailing_stop_update
from risk.partial_profit import should_book_partial_profit, compute_extended_target, compute_booking_split


def parse_args():
    months = 3
    limit = 150
    sweep = "--sweep" in sys.argv[1:]
    for arg in sys.argv[1:]:
        if arg.startswith("--months="):
            months = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    return months, limit, sweep


def run_symbol(symbol, price_history, strategy, regime_series, risk_manager_factory,
               reporting_start_date, use_partial: bool,
               activation_fraction=None, booking_fraction=None, extension_multiple=None):
    """
    Day-by-day simulation: trailing stop always applied (matches live
    default config); partial profit booking applied only if use_partial.
    A triggered partial booking produces a separate trade record for the
    booking tranche (fixed realized gain at the activation-threshold price)
    alongside the eventual runner-tranche exit.
    """
    risk_manager = risk_manager_factory()
    trades = []
    open_trade = None
    entry_date = None
    highest_high_since_entry = None
    partial_booked = False
    current_quantity = None
    current_target = None
    min_bars = 55

    for i in range(min_bars, len(price_history)):
        window = price_history.iloc[: i + 1]
        today_date = price_history.index[i].date()
        today_row = price_history.iloc[i]
        # Real run_daily.py builds a fresh RiskManager every run, so
        # realized_pnl_today implicitly resets every real trading day.
        # risk_manager here is deliberately reused across this whole
        # symbol's simulated history -- without this explicit reset, a
        # losing streak anywhere in the (long) fetch window can trip the 3%
        # daily-loss circuit breaker permanently and silently suppress
        # every later trade for this symbol, including within the actual
        # reporting window (a real bug, caught via the portfolio backtest).
        risk_manager.reset_day()

        if open_trade is not None:
            highest_high_since_entry = max(highest_high_since_entry, float(today_row["High"]))

            new_stop = compute_trailing_stop_update(
                entry_price=open_trade.signal.entry_price, current_stop=open_trade.signal.stop_loss,
                target=current_target, highest_high_since_entry=highest_high_since_entry,
                activation_fraction=settings.TRAILING_STOP_ACTIVATION_FRACTION,
                lock_in_fraction=settings.TRAILING_STOP_LOCK_IN_FRACTION,
            )
            if new_stop is not None:
                open_trade.signal.stop_loss = new_stop

            if use_partial and not partial_booked:
                if should_book_partial_profit(open_trade.signal.entry_price, current_target,
                                               highest_high_since_entry, activation_fraction):
                    split = compute_booking_split(current_quantity, booking_fraction)
                    if split is not None:
                        booking_qty, remaining_qty = split
                        booking_price = (open_trade.signal.entry_price
                                          + activation_fraction * (current_target - open_trade.signal.entry_price))
                        booking_pnl = (booking_price - open_trade.signal.entry_price) * booking_qty
                        if entry_date >= reporting_start_date:
                            trades.append({"symbol": symbol, "entry_date": entry_date, "exit_date": today_date,
                                           "pnl": booking_pnl, "exit_reason": "partial_booking"})
                        current_target = compute_extended_target(open_trade.signal.entry_price,
                                                                   current_target, extension_multiple)
                        open_trade.signal.target = current_target
                        current_quantity = remaining_qty
                        open_trade.quantity = remaining_qty
                        partial_booked = True

            hit_stop = today_row["Low"] <= open_trade.signal.stop_loss
            hit_target = today_row["High"] >= open_trade.signal.target
            if hit_stop or hit_target:
                exit_price = open_trade.signal.stop_loss if hit_stop else open_trade.signal.target
                pnl = (exit_price - open_trade.signal.entry_price) * current_quantity
                risk_manager.on_trade_closed(open_trade, pnl)
                if entry_date >= reporting_start_date:
                    trades.append({"symbol": symbol, "entry_date": entry_date, "exit_date": today_date,
                                   "pnl": pnl, "exit_reason": "stop_loss" if hit_stop else "target"})
                open_trade = None
                partial_booked = False
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
        entry_date = today_date
        highest_high_since_entry = float(today_row["High"])
        current_quantity = approved.quantity
        current_target = approved.signal.target
        partial_booked = False

    return trades


def summarize(trades, label):
    if not trades:
        print(f"{label}: no trades")
        return
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    reasons = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    print(f"{label}: {len(trades)} record(s) | win rate {win_rate:.1f}% | total P&L Rs.{total_pnl:,.2f} | "
          f"avg win Rs.{avg_win:,.2f} | avg loss Rs.{avg_loss:,.2f} | {reasons}")


def main():
    months, limit, sweep = parse_args()
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
            max_capital_per_trade_pct=settings.MAX_CAPITAL_PER_TRADE_PCT,
        )

    if sweep:
        configs = [
            ("baseline (trailing stop only)", False, None, None, None),
            ("activation=0.4, booking=0.5, ext=1.0", True, 0.4, 0.5, 1.0),
            ("activation=0.5, booking=0.5, ext=1.0", True, 0.5, 0.5, 1.0),
            ("activation=0.5, booking=0.5, ext=2.0", True, 0.5, 0.5, 2.0),
            ("activation=0.6, booking=0.5, ext=1.0", True, 0.6, 0.5, 1.0),
            ("activation=0.5, booking=0.3, ext=1.0", True, 0.5, 0.3, 1.0),
        ]
    else:
        configs = [
            ("baseline (trailing stop only)", False, None, None, None),
            (f"activation={settings.PARTIAL_PROFIT_ACTIVATION_FRACTION}, "
             f"booking={settings.PARTIAL_PROFIT_BOOKING_FRACTION}, "
             f"ext={settings.PARTIAL_PROFIT_TARGET_EXTENSION_MULTIPLE}", True,
             settings.PARTIAL_PROFIT_ACTIVATION_FRACTION, settings.PARTIAL_PROFIT_BOOKING_FRACTION,
             settings.PARTIAL_PROFIT_TARGET_EXTENSION_MULTIPLE),
        ]

    print("\n" + "=" * 90)
    for label, use_partial, activation_fraction, booking_fraction, extension_multiple in configs:
        all_trades = []
        for symbol in sorted(eligible_symbols):
            price_history = data.get(symbol)
            if price_history is None or len(price_history) < 60:
                continue
            for strategy_key, strategy_cls in STRATEGY_REGISTRY.items():
                strategy = strategy_cls()
                effective_regime = regime_series if strategy.uses_regime_filter else None
                all_trades += run_symbol(symbol, price_history, strategy, effective_regime,
                                          risk_manager_factory, reporting_start_date, use_partial,
                                          activation_fraction, booking_fraction, extension_multiple)
        summarize(all_trades, label)


if __name__ == "__main__":
    main()
