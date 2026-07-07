"""
Entry point. Run this to backtest the currently active strategies against
historical data, and print a per-strategy plus combined report.

    python main.py

This is a BACKTEST run by default -- it fetches historical data and simulates
trades, it does not place any real orders (that only happens if
config.LIVE_TRADING is True AND you call the live signal-generation path,
which is a separate, later piece of this project -- see ARCHITECTURE.md).

Each active strategy can trade its own symbol universe (config.STRATEGY_SYMBOLS)
and independently opts in or out of the Nifty market-regime filter
(strategy.uses_regime_filter) -- see strategies/base.py.

Before any strategy runs, every symbol is passed through the fundamentals
health check (config.USE_FUNDAMENTALS_FILTER) -- see fundamentals/fundamental_agent.py.
A strategy can never trade a symbol that failed the fundamentals check,
regardless of what its price chart looks like.
"""

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from strategies.market_regime import build_regime_series
from strategies.technical_agent import STRATEGY_REGISTRY
from fundamentals.fundamental_agent import filter_universe
from risk.risk_manager import RiskManager
from backtest.backtester import run_backtest, BacktestResult
from reporting.report_generator import build_report_text, send_whatsapp_message


def run_strategy(strategy_key: str, data: dict, regime_series, eligible_symbols: set) -> BacktestResult:
    strategy_cls = STRATEGY_REGISTRY.get(strategy_key)
    if strategy_cls is None:
        print(f"WARNING: unknown strategy '{strategy_key}', skipping.")
        return BacktestResult(starting_capital=settings.STARTING_CAPITAL)

    strategy = strategy_cls()
    requested_symbols = settings.STRATEGY_SYMBOLS.get(strategy_key, settings.SYMBOLS)
    # only trade symbols that both the strategy config wants AND passed the
    # fundamentals health check -- a strategy can never override a fundamentals rejection
    symbols_for_strategy = [s for s in requested_symbols if s in eligible_symbols]

    skipped = [s for s in requested_symbols if s not in eligible_symbols]
    if skipped:
        print(f"[{strategy_key}] Skipping (failed fundamentals check): {', '.join(skipped)}")

    combined = BacktestResult(starting_capital=settings.STARTING_CAPITAL)

    effective_regime = regime_series if (strategy.uses_regime_filter and settings.USE_MARKET_REGIME_FILTER) else None

    for symbol in symbols_for_strategy:
        price_history = data.get(symbol)
        if price_history is None:
            print(f"WARNING: no data for {symbol}, skipping in {strategy_key}.")
            continue

        risk_manager = RiskManager(
            capital=settings.STARTING_CAPITAL,
            risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
            daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
        )
        result = run_backtest(symbol, price_history, strategy, risk_manager, regime_series=effective_regime)
        regime_note = "regime-filtered" if effective_regime is not None else "no regime filter"
        print(f"\n[{strategy_key}, {regime_note}] {symbol}: {result.summary()}")
        combined.trades.extend(result.trades)

    combined.ending_capital = combined.starting_capital + combined.total_pnl
    return combined


def main():
    all_symbols = set()
    for strategy_key in settings.ACTIVE_STRATEGIES:
        all_symbols.update(settings.STRATEGY_SYMBOLS.get(strategy_key, settings.SYMBOLS))

    eligible_symbols = set(all_symbols)
    if settings.USE_FUNDAMENTALS_FILTER:
        print("\n" + "#" * 60)
        print("FUNDAMENTALS HEALTH CHECK")
        print("#" * 60)
        eligible_list, fundamentals_results = filter_universe(sorted(all_symbols), settings.FUNDAMENTALS_CRITERIA)
        eligible_symbols = set(eligible_list)
        for r in fundamentals_results:
            verdict = "PASS" if r.passed else "FAIL"
            print(f"\n[{verdict}] {r.symbol}")
            for reason in r.reasons:
                print(f"    - {reason}")
        if not eligible_symbols:
            print("\nNo symbols passed the fundamentals check. Nothing to backtest.")
            return

    print(f"\nFetching historical data for {len(eligible_symbols)} symbols...")
    data = fetch_all(sorted(eligible_symbols), period="5y")

    if not data:
        print("No data fetched for any symbol. Check your internet connection or ticker symbols.")
        return

    print("Fetching Nifty 50 index history for the market-regime filter...")
    nifty = fetch_nifty(period="5y")
    regime_series = build_regime_series(nifty)

    overall = BacktestResult(starting_capital=settings.STARTING_CAPITAL)

    for strategy_key in settings.ACTIVE_STRATEGIES:
        print("\n" + "#" * 60)
        print(f"STRATEGY: {strategy_key}")
        print("#" * 60)
        result = run_strategy(strategy_key, data, regime_series, eligible_symbols)
        print(f"\n[{strategy_key}] COMBINED: {result.summary()}")
        overall.trades.extend(result.trades)

    overall.ending_capital = overall.starting_capital + overall.total_pnl

    print("\n" + "=" * 60)
    print("OVERALL RESULT ACROSS ALL ACTIVE STRATEGIES")
    print(overall.summary())
    print("=" * 60)

    report_text = build_report_text(overall, "backtest run (all active strategies)")
    send_whatsapp_message(report_text, settings.WHATSAPP_TO_NUMBER)


if __name__ == "__main__":
    main()
