"""
Runs a few times during market hours (market open, midday, close -- matches
ARCHITECTURE.md's "checks signals a few times a day, not a tick loop" design
principle) to have the same agent team that picks new trades also watch the
ones already open.

Why this exists: run_daily.py only opens new positions once a day. The hard
stop-loss/target for each position is enforced by a GTT order placed at entry
(execution/execution_engine.py's _place_gtt_exit) -- Zerodha's own servers
handle that trigger, no live quotes or polling needed. But a GTT only reacts
to price. This script is the "judgment" layer on top of that: it re-runs
Technical + Fundamental + News + Research Analyst against every symbol
currently held, exactly as run_daily.py does for new candidates, and exits
early if the picture has turned unfavorable for reasons a price trigger alone
wouldn't catch (bad news, deteriorating fundamentals, a technical reversal).

Usage:
    python monitor_positions.py                 # checks all current holdings
    python monitor_positions.py --paper          # forces paper-equivalent
                                                  # behavior (skips the check
                                                  # entirely -- see below)

Only meaningful once LIVE_TRADING is on -- paper mode has no real Kite
holdings to check (see run_daily.py's get_current_holdings for why), so
--paper here just short-circuits to "nothing to do", matching run_daily.py's
own --paper override semantics.
"""

import sys

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from strategies.base import Signal
from strategies.market_regime import build_regime_series
from strategies.technical_agent import get_technical_signals
from fundamentals.fundamental_agent import fetch_fundamentals, check_health
from news.news_agent import analyze_news_cached, disabled_news_assessment, ClaudeAPIError
from research.research_analyst import analyze_stock
from risk.risk_manager import ApprovedTrade
from execution.execution_engine import ExecutionEngine
from execution.positions import fetch_all_holdings
from execution.position_state import reconcile_closed_positions, load_known_positions
from auth.kite_auto_login import ensure_fresh_kite_session
from cio.plan_state import load_monthly_plan, effective_active_strategies
from reporting.telegram_notifier import send_telegram_message


def parse_cli_args():
    force_paper = "--paper" in sys.argv[1:]
    return force_paper


def evaluate_holding(symbol: str, regime_series, active_strategies: list):
    """
    Re-runs the same Technical + Fundamental + News + Research Analyst
    pipeline run_daily.py uses for new candidates, against an already-held
    symbol. Returns the ResearchAssessment, or None if price history/
    fundamentals couldn't be fetched (treated as "skip this check", not as
    an exit signal -- a data-fetch hiccup shouldn't trigger a real sell).
    """
    try:
        price_history = fetch_all([symbol], period="1y").get(symbol)
    except Exception as e:
        print(f"WARNING: could not fetch price history for {symbol}: {e}")
        return None
    if price_history is None or len(price_history) < 60:
        return None

    technical_signals = get_technical_signals(symbol, price_history, regime_series, active_strategies)

    try:
        metrics = fetch_fundamentals(symbol)
    except Exception as e:
        print(f"WARNING: could not fetch fundamentals for {symbol}: {e}")
        return None
    fundamentals_result = check_health(symbol, metrics, settings.FUNDAMENTALS_CRITERIA)

    if settings.USE_NEWS_AGENT:
        # analyze_news_cached (not analyze_news) -- this function runs several
        # times a day against the same held symbols, so it skips the Claude
        # call and reuses the last verdict whenever the headlines haven't
        # actually changed since the previous check.
        news_assessment = analyze_news_cached(symbol, api_key=settings.ANTHROPIC_API_KEY,
                                               max_items=settings.NEWS_MAX_ARTICLES)
    else:
        news_assessment = disabled_news_assessment(symbol)

    return analyze_stock(symbol, technical_signals, fundamentals_result, news_assessment,
                          api_key=settings.ANTHROPIC_API_KEY)


def main():
    force_paper = parse_cli_args()
    live_trading = settings.LIVE_TRADING and not force_paper

    print("=" * 60)
    print("POSITION MONITOR -- " + ("LIVE" if live_trading
                                     else "PAPER MODE" + (" (forced by --paper)" if force_paper else "")))
    print("=" * 60)

    if not live_trading:
        print("Not in live mode -- nothing to monitor (paper mode has no real Kite holdings).")
        return

    if not ensure_fresh_kite_session(settings):
        print("\nABORTING -- could not establish a valid Kite session.")
        send_telegram_message(
            "*Position monitor aborted*\n\nCould not establish a valid Kite session.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    try:
        holdings = fetch_all_holdings(settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN)
    except Exception as e:
        print(f"\nABORTING -- could not fetch current holdings: {e}")
        send_telegram_message(
            f"*Position monitor aborted*\n\nCould not fetch current holdings: {e}",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    closed = reconcile_closed_positions(
        holdings, settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN,
        settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
    )
    if closed:
        print(f"Reconciled {len(closed)} closure(s) since last check: {', '.join(closed)}")

    if not holdings:
        print("No open positions to check.")
        send_telegram_message("*Position monitor*\n\nNo open positions to check.",
                               settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        return

    print("\nFetching Nifty 50 regime...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)

    active_strategies = effective_active_strategies(load_monthly_plan(), settings)

    execution_engine = ExecutionEngine(
        live_trading=True,
        api_key=settings.KITE_API_KEY,
        access_token=settings.KITE_ACCESS_TOKEN,
        limit_order_buffer_pct=settings.LIMIT_ORDER_BUFFER_PCT,
    )

    known_positions = load_known_positions()
    exited_symbols = []
    checked_lines = []

    for holding in holdings:
        print(f"\nChecking {holding.symbol}...")
        assessment = evaluate_holding(holding.symbol, regime_series, active_strategies)
        if assessment is None:
            checked_lines.append(f"- {holding.symbol}: could not complete a fresh check -- left as-is.")
            continue

        print(f"  Fresh verdict: {assessment.verdict.upper()} ({assessment.confidence:.0%})")

        if assessment.verdict == "unfavorable":
            known = known_positions.get(holding.symbol)
            gtt_id = known.gtt_id if known else None

            exit_signal = Signal(
                symbol=holding.symbol, direction="SELL", entry_price=holding.average_price,
                stop_loss=holding.average_price, target=holding.average_price, confidence=assessment.confidence,
                strategy_name="monitor_positions", reason=assessment.reasoning,
            )
            sell_result = execution_engine.place_order(ApprovedTrade(
                signal=exit_signal, quantity=holding.quantity,
                capital_deployed=holding.quantity * holding.average_price,
            ))
            print(f"  Exited early: {sell_result}")

            if sell_result.get("status") == "success":
                if gtt_id is not None:
                    execution_engine.cancel_gtt(gtt_id)

                exited_symbols.append(holding.symbol)
                checked_lines.append(
                    f"- {holding.symbol}: EXITED EARLY (verdict turned unfavorable, "
                    f"{assessment.confidence:.0%} confidence) -- {assessment.reasoning}"
                )
            else:
                # The exit SELL itself failed -- do NOT cancel the GTT and do NOT
                # mark this as exited. This position is still really held, so its
                # GTT stop-loss/target must stay in place; treating a failed exit
                # as successful would leave a real position with no protection.
                print(f"WARNING: exit SELL order for {holding.symbol} did not succeed -- "
                      f"GTT left in place, still counted as held. Result: {sell_result}")
                checked_lines.append(
                    f"- {holding.symbol}: verdict turned unfavorable ({assessment.confidence:.0%} "
                    f"confidence) but the exit order failed -- still held, GTT stop-loss/target "
                    f"unaffected. {assessment.reasoning}"
                )
        else:
            checked_lines.append(
                f"- {holding.symbol}: held ({assessment.verdict}, {assessment.confidence:.0%} confidence)"
            )

    # Early exits placed above still show up in Kite holdings until the sell
    # settles, so reconciliation can't diff them out yet by comparing against
    # a fresh holdings fetch. Instead, reconcile against `holdings` with the
    # exited symbols filtered out by hand -- this is what makes
    # reconcile_closed_positions see them as "closed" right now, with the
    # correct reason, rather than waiting for a later run to (incorrectly)
    # attribute the closure to a GTT trigger.
    if exited_symbols:
        reconcile_closed_positions(
            [h for h in holdings if h.symbol not in exited_symbols],
            settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN,
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
            reason="Exited early by monitor_positions.py (unfavorable verdict)",
        )

    report = (
        f"*Position monitor -- checked {len(holdings)} position(s)*\n\n"
        + "\n".join(checked_lines)
    )
    send_telegram_message(report, settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    try:
        main()
    except ClaudeAPIError as e:
        print(f"\nABORTING -- {e}")
        send_telegram_message(
            f"*Position monitor aborted -- could not reach Claude*\n\n{e}\n\n"
            f"Open positions were not re-checked this run. Their GTT stop-loss/target "
            f"orders remain active and unaffected.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        sys.exit(1)
