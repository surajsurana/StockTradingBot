"""
Portfolio-level backtest of PullbackContinuationStrategy ALONE (not mixed
with ma_crossover/mean_reversion) -- same true shared-capital-pool
architecture as backtest_portfolio.py (real RiskManager/Portfolio Manager,
current live settings, the reset_day() fix already applied via
backtest/run_backtest's sibling logic below), just with the strategy set
swapped to the one candidate being evaluated for a possible future 20%
capital carve-out. Deliberately a separate script rather than editing
backtest_portfolio.py or registering this strategy anywhere live -- this
is evaluation only, per Suraj's "just create and test, don't implement"
request (2026-07-23). Nothing here touches config.ACTIVE_STRATEGIES,
strategies/technical_agent.py's STRATEGY_REGISTRY, or any live file.

Same non-replayable-Research-Analyst-verdict limitation as
backtest_portfolio.py applies -- see that script's docstring.

    python backtest_pullback_continuation_portfolio.py [--months=1] [--limit=200]
"""

import sys
import pandas as pd

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series, is_bullish_on
from strategies.pullback_continuation import PullbackContinuationStrategy
from fundamentals.fundamental_agent import filter_universe
from risk.risk_manager import RiskManager
from risk.trailing_stop import compute_trailing_stop_update
from research.research_analyst import ResearchAssessment
from portfolio.portfolio_manager import allocate, TradeCandidate


def parse_args():
    months = 1
    limit = 200
    confidence = 0.65
    for arg in sys.argv[1:]:
        if arg.startswith("--months="):
            months = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
        elif arg.startswith("--confidence="):
            confidence = float(arg.split("=")[1])
    return months, limit, confidence


def main():
    months, limit, representative_confidence = parse_args()
    period = "18mo"  # long runway so the 200-day regime filter is valid before the reporting window

    symbols = get_nifty500_symbols()[:limit]
    print(f"Universe: {len(symbols)} symbol(s), fetch period: {period}, reporting last {months} month(s)")
    print(f"Strategy under test: pullback_continuation ONLY (not mixed with the live strategies)")
    print(f"Representative confidence for every favorable signal: {representative_confidence:.0%} "
          f"(stand-in for the Research Analyst's real, non-replayable verdict)")

    print("\nFundamentals health check (once, static for the whole backtest)...")
    eligible_symbols, _ = filter_universe(symbols, settings.FUNDAMENTALS_CRITERIA)
    print(f"Eligible after fundamentals: {len(eligible_symbols)}")

    print(f"\nFetching {period} of historical data...")
    data = fetch_all(sorted(eligible_symbols), period=period)
    print(f"Data fetched for {len(data)} symbol(s)")

    print("Fetching Nifty 50 index for the regime filter...")
    nifty = fetch_nifty(period=period)
    regime_series = build_regime_series(nifty)

    reporting_start_date = (pd.Timestamp.now() - pd.DateOffset(months=months)).date()

    calendar = nifty.index
    min_bars = 210  # a bit more than the 200-day regime MA, so it's valid from day 1 of the reporting window

    risk_manager = RiskManager(
        capital=settings.STARTING_CAPITAL, risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
        daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
        max_capital_per_trade_pct=settings.MAX_CAPITAL_PER_TRADE_PCT,
    )

    strategies = {"pullback_continuation": PullbackContinuationStrategy()}

    open_positions = {}
    cooldown_until_index = {}
    closed_trades = []
    reporting_started = False
    trading_day_counter = 0

    for today in calendar:
        trading_day_counter += 1
        today_date = today.date()
        # Same fix as backtest_portfolio.py: without this, realized P&L
        # accumulates across the whole simulation instead of resetting daily
        # like the real RiskManager does every live run, and the daily-loss
        # circuit breaker can trip permanently partway through, silently
        # zeroing out the rest of the backtest.
        risk_manager.reset_day()
        if today_date >= reporting_start_date and not reporting_started:
            reporting_started = True
            print(f"\n--- Reporting window starts: {today_date} "
                  f"(day {trading_day_counter}, {len(open_positions)} position(s) already open from warmup) ---")

        for symbol in list(open_positions.keys()):
            price_history = data.get(symbol)
            if price_history is None or today not in price_history.index:
                continue
            i = price_history.index.get_loc(today)
            today_row = price_history.iloc[i]
            pos = open_positions[symbol]
            pos["highest_high"] = max(pos["highest_high"], float(today_row["High"]))

            new_stop = compute_trailing_stop_update(
                entry_price=pos["approved_trade"].signal.entry_price,
                current_stop=pos["approved_trade"].signal.stop_loss,
                target=pos["approved_trade"].signal.target,
                highest_high_since_entry=pos["highest_high"],
                activation_fraction=settings.TRAILING_STOP_ACTIVATION_FRACTION,
                lock_in_fraction=settings.TRAILING_STOP_LOCK_IN_FRACTION,
            )
            if new_stop is not None:
                pos["approved_trade"].signal.stop_loss = new_stop

            hit_stop = today_row["Low"] <= pos["approved_trade"].signal.stop_loss
            hit_target = today_row["High"] >= pos["approved_trade"].signal.target
            if hit_stop or hit_target:
                exit_price = pos["approved_trade"].signal.stop_loss if hit_stop else pos["approved_trade"].signal.target
                pnl = (exit_price - pos["approved_trade"].signal.entry_price) * pos["approved_trade"].quantity
                risk_manager.on_trade_closed(pos["approved_trade"], pnl)
                if reporting_started or pos["entry_date"] >= reporting_start_date:
                    closed_trades.append({
                        "symbol": symbol, "entry_date": pos["entry_date"], "exit_date": today_date,
                        "pnl": pnl, "exit_reason": "stop_loss" if hit_stop else "target",
                    })
                if hit_stop:
                    cooldown_until_index[symbol] = trading_day_counter + settings.STOP_LOSS_COOLDOWN_DAYS
                del open_positions[symbol]

        if trading_day_counter < min_bars:
            continue

        candidates = []
        for symbol in sorted(eligible_symbols):
            if symbol in open_positions:
                continue
            if cooldown_until_index.get(symbol, 0) > trading_day_counter:
                continue
            price_history = data.get(symbol)
            if price_history is None or today not in price_history.index:
                continue
            i = price_history.index.get_loc(today)
            if i < min_bars:
                continue
            window = price_history.iloc[: i + 1]

            for strategy_key, strategy in strategies.items():
                signal = strategy.generate_signal(window)
                if signal is None:
                    continue
                if strategy.uses_regime_filter and not is_bullish_on(regime_series, today):
                    continue
                signal.symbol = symbol
                candidates.append(TradeCandidate(
                    symbol=symbol, signal=signal,
                    research_assessment=ResearchAssessment(
                        symbol=symbol, verdict="favorable", confidence=representative_confidence,
                        reasoning="backtest stand-in -- see script docstring",
                    ),
                ))
                break

        if candidates:
            decisions = allocate(candidates, risk_manager)
            for d in decisions:
                if d.approved:
                    matching_candidate = next(c for c in candidates if c.symbol == d.symbol)
                    open_positions[d.symbol] = {
                        "approved_trade": d.approved_trade, "entry_date": today_date,
                        "highest_high": matching_candidate.signal.entry_price,
                    }

    unrealized_pnl = 0.0
    for symbol, pos in open_positions.items():
        price_history = data.get(symbol)
        if price_history is None:
            continue
        last_close = float(price_history["Close"].iloc[-1])
        unrealized_pnl += (last_close - pos["approved_trade"].signal.entry_price) * pos["approved_trade"].quantity

    reported_trades = [t for t in closed_trades if t["entry_date"] >= reporting_start_date or t["exit_date"] >= reporting_start_date]
    total_realized = sum(t["pnl"] for t in reported_trades)
    wins = [t for t in reported_trades if t["pnl"] > 0]
    losses = [t for t in reported_trades if t["pnl"] <= 0]

    print("\n" + "=" * 80)
    print(f"PULLBACK CONTINUATION PORTFOLIO BACKTEST -- last {months} month(s), "
          f"{len(eligible_symbols)} symbols, shared Rs.{settings.STARTING_CAPITAL:,.0f} capital pool")
    print("=" * 80)
    print(f"Closed trades: {len(reported_trades)} | Win rate: "
          f"{(len(wins) / len(reported_trades) * 100) if reported_trades else 0:.1f}%")
    if wins:
        print(f"Avg win: Rs.{sum(t['pnl'] for t in wins) / len(wins):,.2f}")
    if losses:
        print(f"Avg loss: Rs.{sum(t['pnl'] for t in losses) / len(losses):,.2f}")
    print(f"Total realized P&L: Rs.{total_realized:,.2f}")
    print(f"Still open at end of window: {len(open_positions)} position(s), "
          f"unrealized P&L (marked to last close): Rs.{unrealized_pnl:,.2f}")
    print(f"Combined (realized + unrealized): Rs.{total_realized + unrealized_pnl:,.2f}")
    print(f"Return on starting capital: {(total_realized + unrealized_pnl) / settings.STARTING_CAPITAL * 100:.2f}%")

    nifty_reporting = nifty[nifty.index.date >= reporting_start_date]
    if len(nifty_reporting) > 1:
        nifty_return = (nifty_reporting["Close"].iloc[-1] / nifty_reporting["Close"].iloc[0] - 1) * 100
        print(f"\nFor comparison, Nifty 50 itself returned {nifty_return:+.2f}% over the same window "
              f"(buy-and-hold, no trading).")


if __name__ == "__main__":
    main()
