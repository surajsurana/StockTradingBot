"""
End-to-end test: runs the Chief Investment AI's monthly review + planning
cycle using a real (short) backtest as a stand-in for "last month's actual
results", then prints the WhatsApp-style monthly plan and review messages.

Run this on your own machine once your Anthropic API key is set in
config/settings.py:

    python test_chief_investment_ai.py

Important caveat, stated plainly: there's no real executed-trade history yet
(that only exists once you're live on Kite). So this script uses a recent
short backtest (last ~1 month of historical data) as a stand-in for "last
month's actual result", purely to exercise the Chief Investment AI's review
and planning logic end-to-end with real numbers. Once you're trading live,
this same review_month()/plan_month() pipeline should be fed your real
closed-trade history for the month instead of a backtest.

Cost note: this calls Claude twice (once for the review, once for the plan).
"""

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from strategies.market_regime import build_regime_series, is_bullish_on
from strategies.technical_agent import STRATEGY_REGISTRY
from risk.risk_manager import RiskManager
from backtest.backtester import run_backtest, BacktestResult
from cio.chief_investment_ai import review_month, plan_month, MonthlyPlan
from reporting.report_generator import build_monthly_plan_text, build_monthly_review_text


def run_stand_in_last_month_backtest() -> BacktestResult:
    """
    Runs a short (~1 month) backtest across the currently active strategies
    and symbols, purely as a stand-in for "last month's actual result" until
    real live trade history exists. See module docstring caveat.
    """
    all_symbols = sorted({s for key in settings.ACTIVE_STRATEGIES
                           for s in settings.STRATEGY_SYMBOLS.get(key, settings.SYMBOLS)})
    data = fetch_all(all_symbols, period="3mo")   # extra history so indicators can warm up
    nifty = fetch_nifty(period="3mo")
    regime_series = build_regime_series(nifty)

    combined = BacktestResult(starting_capital=settings.STARTING_CAPITAL)

    for strategy_key in settings.ACTIVE_STRATEGIES:
        strategy_cls = STRATEGY_REGISTRY[strategy_key]
        strategy = strategy_cls()
        symbols = [s for s in settings.STRATEGY_SYMBOLS.get(strategy_key, settings.SYMBOLS) if s in data]
        effective_regime = regime_series if (strategy.uses_regime_filter and settings.USE_MARKET_REGIME_FILTER) else None

        for symbol in symbols:
            risk_manager = RiskManager(
                capital=settings.STARTING_CAPITAL,
                risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
                max_open_positions=settings.MAX_OPEN_POSITIONS,
                max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
                daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
            )
            result = run_backtest(symbol, data[symbol], strategy, risk_manager, regime_series=effective_regime)
            combined.trades.extend(result.trades)

    combined.ending_capital = combined.starting_capital + combined.total_pnl
    return combined


def main():
    previous_plan = MonthlyPlan(
        month_label="June 2026",
        capital_allocated=settings.STARTING_CAPITAL,
        target_return_pct=3.0,
        active_strategies=list(settings.ACTIVE_STRATEGIES),
        notes="Starting plan (no prior month to compare against yet).",
    )

    print("Running a short stand-in backtest for 'last month's actual result'...")
    last_month_result = run_stand_in_last_month_backtest()
    print(f"Stand-in result: {len(last_month_result.trades)} trades closed, "
          f"P&L Rs.{last_month_result.total_pnl:,.2f}\n")

    print("Running Chief Investment AI review (calls Claude)...")
    review = review_month("July 2026", previous_plan, last_month_result, api_key=settings.ANTHROPIC_API_KEY)
    print(f"Target met: {review.target_met} (actual {review.actual_return_pct:.1f}%)")
    print(f"Reasoning: {review.reasoning}\n")

    print("Fetching Nifty regime for the planning step...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)
    market_is_bullish = is_bullish_on(regime_series, nifty.index[-1])

    print("Running Chief Investment AI plan for next month (calls Claude)...")
    next_plan = plan_month("July 2026", previous_plan, review, market_is_bullish,
                            api_key=settings.ANTHROPIC_API_KEY)

    print("\n" + "=" * 60)
    print(build_monthly_review_text(
        previous_plan.month_label, previous_plan.capital_allocated,
        previous_plan.target_return_pct, last_month_result,
    ))
    print(f"\nCIO reasoning: {review.reasoning}")
    print("=" * 60)
    print(build_monthly_plan_text(
        next_plan.month_label, next_plan.capital_allocated,
        next_plan.target_return_pct, next_plan.active_strategies, notes=next_plan.notes,
    ))
    print("=" * 60)


if __name__ == "__main__":
    main()
