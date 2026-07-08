"""
Chief Investment AI's monthly cycle -- the piece that was built
(cio/chief_investment_ai.py) but never actually run for real. Reviews last
month's REAL closed trades (execution/position_state.py's
closed_trades_log.csv, via reporting/trade_history.py -- not a backtest
stand-in), sets next month's capital/target/active-strategies envelope, and
sends both to Telegram.

Deliberately has no Kite dependency -- the only place a live capital number
matters is seeding the very first-ever plan, and run_daily.py already
bootstraps data/monthly_plan.json itself (it fetches real capital every
morning anyway). This script only reads a CSV, calls Claude, writes a JSON
file, and sends Telegram -- nothing here needs today's Kite session to be
valid, which keeps a job that runs 12x/year from depending on daily-token
auth timing.

Usage, scheduled on the 1st of each month before run_daily.py's morning run
(see the cron line in the deployment notes):

    python monthly_review.py
"""

from datetime import date, timedelta

from config import settings
from data.fetch_historical import fetch_nifty
from strategies.market_regime import build_regime_series, is_bullish_on
from cio.chief_investment_ai import MonthlyPlan, review_month, plan_month
from cio.plan_state import load_monthly_plan, save_monthly_plan
from reporting.trade_history import load_closed_trades_for_month
from reporting.report_generator import build_monthly_plan_text, build_monthly_review_text
from reporting.telegram_notifier import send_telegram_message


def main():
    today = date.today()
    current_month_label = today.strftime("%B %Y")
    last_month_end = today.replace(day=1) - timedelta(days=1)
    last_month_label = last_month_end.strftime("%B %Y")

    print("=" * 60)
    print(f"MONTHLY REVIEW -- reviewing {last_month_label}, planning {current_month_label}")
    print("=" * 60)

    plan = load_monthly_plan()
    review = None

    if plan is None:
        print("No existing plan found -- this is the first month. Bootstrapping a starting plan.")
        plan = MonthlyPlan(
            month_label=last_month_label,
            capital_allocated=settings.STARTING_CAPITAL,
            target_return_pct=3.0,
            active_strategies=list(settings.ACTIVE_STRATEGIES),
            risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
            notes="Starting plan (no prior month to compare against yet).",
        )
    else:
        print(f"Loaded existing plan from {plan.month_label}. Reviewing {last_month_label}'s real closed trades...")
        result = load_closed_trades_for_month(last_month_end.year, last_month_end.month, plan.capital_allocated)
        print(f"Found {len(result.trades)} closed trade(s) with known P&L in {last_month_label}.")
        review = review_month(last_month_label, plan, result, api_key=settings.ANTHROPIC_API_KEY)
        print(f"Review: target {'met' if review.target_met else 'missed'} ({review.actual_return_pct:.1f}% actual)")

    print("\nFetching Nifty regime for the planning step...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)
    market_is_bullish = is_bullish_on(regime_series, nifty.index[-1])

    print(f"\nPlanning {current_month_label}...")
    new_plan = plan_month(current_month_label, plan, review, market_is_bullish, api_key=settings.ANTHROPIC_API_KEY)
    print(f"New plan: capital Rs.{new_plan.capital_allocated:,.2f}, target {new_plan.target_return_pct:.1f}%, "
          f"strategies {new_plan.active_strategies}")

    save_monthly_plan(new_plan)
    print("Saved new plan to data/monthly_plan.json.")

    if review is not None:
        send_telegram_message(
            build_monthly_review_text(last_month_label, plan.capital_allocated, plan.target_return_pct, result),
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
    send_telegram_message(
        build_monthly_plan_text(new_plan.month_label, new_plan.capital_allocated, new_plan.target_return_pct,
                                 new_plan.active_strategies, risk_per_trade_pct=new_plan.risk_per_trade_pct,
                                 notes=new_plan.notes),
        settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
    )


if __name__ == "__main__":
    main()
