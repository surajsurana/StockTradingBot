"""
Backtests the trailing stop (risk/trailing_stop.py) against real history:
runs the SAME signals from the two active strategies through three exit
simulations -- baseline (fixed stop/target only, exactly like
backtest/backtester.py), trailing-stop (ideal peak-based ratchet only), and
trailing-stop-with-fallback (falls back to locking in a smaller,
CURRENTLY-placeable gain off today's close when the ideal stop would be
rejected) -- and reports the real difference in P&L, win rate, and how
often each variant actually changed the outcome.

v3 addition (2026-08-02): earlier versions of this backtest applied
new_stop unconditionally every day, which never modeled the real Kite
constraint that a GTT stop-loss trigger must sit below the current market
price -- so it could never reveal (or validate a fix for) the real
production incident this fallback exists to fix: NTPC.NS reached +3.2%
since entry, the ideal ratchet was rejected the moment it was attempted
(price had pulled back off the peak before the order could be placed), and
because price never again exceeded that peak, the position sat with NO
improved protection for the next ~3 weeks. This version models that
rejection using each day's Close as a proxy for "price at the time the
ratchet would actually be attempted" -- a daily-bar approximation (the real
system checks intraday LTP a few times a day), documented here rather than
silently assumed.

Fetches a longer window than the reporting period so the 200-day
market-regime filter has real history to work with well before the reporting
window starts (see the pullback-continuation backtest earlier this session
for why a too-short fetch silently zeroes out every regime-filtered trade).
Only entries within the last `--months` are included in the reported
comparison.

    python backtest_trailing_stop.py [--months=3] [--limit=150]
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
from risk.trailing_stop import compute_trailing_stop_update, compute_trailing_stop_fallback


def parse_args():
    months = 3
    limit = 150
    activation_fraction = settings.TRAILING_STOP_ACTIVATION_FRACTION
    lock_in_fraction = settings.TRAILING_STOP_LOCK_IN_FRACTION
    fallback_lock_in_fraction = lock_in_fraction  # overridable separately -- see --fallback-lockin
    fallback_activation_fraction = settings.TRAILING_STOP_FALLBACK_ACTIVATION_FRACTION
    for arg in sys.argv[1:]:
        if arg.startswith("--months="):
            months = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
        elif arg.startswith("--activation="):
            activation_fraction = float(arg.split("=")[1])
        elif arg.startswith("--lockin="):
            lock_in_fraction = float(arg.split("=")[1])
            fallback_lock_in_fraction = lock_in_fraction
        elif arg.startswith("--fallback-lockin="):
            fallback_lock_in_fraction = float(arg.split("=")[1])
        elif arg.startswith("--fallback-activation="):
            fallback_activation_fraction = float(arg.split("=")[1])
    return (months, limit, activation_fraction, lock_in_fraction,
            fallback_lock_in_fraction, fallback_activation_fraction)


def run_symbol(symbol, price_history, strategy, regime_series, risk_manager_factory,
               reporting_start_date, use_trailing_stop: bool, use_fallback: bool = False,
               activation_fraction: float = None, lock_in_fraction: float = None,
               fallback_lock_in_fraction: float = None, fallback_activation_fraction: float = None):
    """
    Same day-by-day loop as backtest/backtester.py's run_backtest, with one
    addition: while a trade is open, if use_trailing_stop is True, checks
    whether the trailing stop should ratchet the stop-loss up before
    checking for a stop/target hit that day.

    Models the real Kite constraint that a stop-loss GTT can't be placed at
    or above the current market price: the ideal (peak-based) new_stop is
    only actually applied if it sits below today's Close (the proxy for
    "price at the time the ratchet would be attempted" -- see module
    docstring). If use_fallback is True and the ideal stop is rejected this
    way, tries compute_trailing_stop_fallback() instead, using today's Close
    as current_price.
    """
    risk_manager = risk_manager_factory()
    trades = []
    open_trade = None
    entry_date = None
    highest_high_since_entry = None
    min_bars = 55

    for i in range(min_bars, len(price_history)):
        window = price_history.iloc[: i + 1]
        today_date = price_history.index[i].date()
        today_row = price_history.iloc[i]
        today_close = float(today_row["Close"])
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

            if use_trailing_stop:
                new_stop = compute_trailing_stop_update(
                    entry_price=open_trade.signal.entry_price,
                    current_stop=open_trade.signal.stop_loss,
                    target=open_trade.signal.target,
                    highest_high_since_entry=highest_high_since_entry,
                    activation_fraction=activation_fraction,
                    lock_in_fraction=lock_in_fraction,
                )
                if new_stop is not None and new_stop < today_close:
                    open_trade.signal.stop_loss = new_stop
                elif new_stop is not None and use_fallback:
                    fallback_stop = compute_trailing_stop_fallback(
                        entry_price=open_trade.signal.entry_price,
                        current_stop=open_trade.signal.stop_loss,
                        target=open_trade.signal.target, current_price=today_close,
                        fallback_activation_fraction=fallback_activation_fraction,
                        lock_in_fraction=fallback_lock_in_fraction
                        if fallback_lock_in_fraction is not None else lock_in_fraction,
                    )
                    if fallback_stop is not None:
                        open_trade.signal.stop_loss = fallback_stop

            hit_stop = today_row["Low"] <= open_trade.signal.stop_loss
            hit_target = today_row["High"] >= open_trade.signal.target

            if hit_stop or hit_target:
                exit_price = open_trade.signal.stop_loss if hit_stop else open_trade.signal.target
                pnl = (exit_price - open_trade.signal.entry_price) * open_trade.quantity
                risk_manager.on_trade_closed(open_trade, pnl)
                if entry_date >= reporting_start_date:
                    trades.append({
                        "symbol": symbol, "entry_date": entry_date, "exit_date": today_date,
                        "pnl": pnl, "exit_reason": "stop_loss" if hit_stop else "target",
                    })
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
        entry_date = today_date
        highest_high_since_entry = float(today_row["High"])

    return trades


def main():
    (months, limit, activation_fraction, lock_in_fraction,
     fallback_lock_in_fraction, fallback_activation_fraction) = parse_args()
    period = "18mo"  # long runway so the 200-day regime filter is valid well before the reporting window

    symbols = get_nifty500_symbols()[:limit]
    print(f"Universe: {len(symbols)} symbol(s), fetch period: {period}, reporting last {months} month(s)")
    print(f"Trailing stop params: activation_fraction={activation_fraction}, lock_in_fraction={lock_in_fraction}, "
          f"fallback_lock_in_fraction={fallback_lock_in_fraction}, "
          f"fallback_activation_fraction={fallback_activation_fraction}")

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

    def risk_manager_factory():
        return RiskManager(
            capital=settings.STARTING_CAPITAL, risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
            daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
            max_capital_per_trade_pct=settings.MAX_CAPITAL_PER_TRADE_PCT,
        )

    baseline_trades, trailing_trades, fallback_trades = [], [], []

    for symbol in sorted(eligible_symbols):
        price_history = data.get(symbol)
        if price_history is None or len(price_history) < 60:
            continue
        for strategy_key, strategy_cls in STRATEGY_REGISTRY.items():
            strategy = strategy_cls()
            effective_regime = regime_series if strategy.uses_regime_filter else None

            baseline_trades += run_symbol(symbol, price_history, strategy, effective_regime,
                                           risk_manager_factory, reporting_start_date, use_trailing_stop=False)
            trailing_trades += run_symbol(symbol, price_history, strategy, effective_regime,
                                           risk_manager_factory, reporting_start_date, use_trailing_stop=True,
                                           use_fallback=False,
                                           activation_fraction=activation_fraction, lock_in_fraction=lock_in_fraction)
            fallback_trades += run_symbol(symbol, price_history, strategy, effective_regime,
                                           risk_manager_factory, reporting_start_date, use_trailing_stop=True,
                                           use_fallback=True,
                                           activation_fraction=activation_fraction, lock_in_fraction=lock_in_fraction,
                                           fallback_lock_in_fraction=fallback_lock_in_fraction,
                                           fallback_activation_fraction=fallback_activation_fraction)

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
        print(f"{label}: {len(trades)} trades | win rate {win_rate:.1f}% | total P&L Rs.{total_pnl:,.2f} | "
              f"avg win Rs.{avg_win:,.2f} | avg loss Rs.{avg_loss:,.2f} | exits {reasons}")

    print("\n" + "=" * 70)
    print(f"TRAILING STOP BACKTEST -- entries in the last {months} month(s)")
    print("=" * 70)
    summarize(baseline_trades, "BASELINE (fixed stop/target)")
    summarize(trailing_trades, "TRAILING STOP (ideal only, Kite-rejection modeled)")
    summarize(fallback_trades, "TRAILING STOP + FALLBACK")

    def compare(a_trades, b_trades, a_label, b_label):
        # Match trades 1:1 by (symbol, entry_date) to show per-trade outcome
        # differences -- the real question isn't just aggregate P&L, it's HOW
        # OFTEN a variant changed a trade's outcome and in which direction.
        a_by_key = {(t["symbol"], t["entry_date"]): t for t in a_trades}
        b_by_key = {(t["symbol"], t["entry_date"]): t for t in b_trades}
        changed = improved = worsened = 0
        for key, a_t in a_by_key.items():
            b_t = b_by_key.get(key)
            if b_t is None:
                continue
            diff = b_t["pnl"] - a_t["pnl"]
            if abs(diff) > 0.01:
                changed += 1
                if diff > 0:
                    improved += 1
                else:
                    worsened += 1
        print(f"\n{b_label} vs {a_label}: {changed}/{len(a_by_key)} trade(s) changed "
              f"({improved} improved, {worsened} worsened)")

    compare(baseline_trades, trailing_trades, "BASELINE", "TRAILING STOP")
    compare(baseline_trades, fallback_trades, "BASELINE", "TRAILING STOP + FALLBACK")
    compare(trailing_trades, fallback_trades, "TRAILING STOP", "TRAILING STOP + FALLBACK")


if __name__ == "__main__":
    main()
