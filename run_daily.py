"""
THE daily entry point once you're ready to let the system act for real.

    python run_daily.py                 # scans the full Nifty 500 universe
    python run_daily.py --limit=50       # only scans the first 50 symbols (fast, for testing)
    python run_daily.py --paper          # forces paper mode for this run, ignoring config.LIVE_TRADING

Run this once each morning, before or right after market open. It ties every
agent built so far into one real pass over the market:

STAGE 1 (cheap, no LLM cost -- yfinance only):
  For every symbol in the universe (Nifty 500 by default, config.SYMBOLS as
  a smaller fallback if config.USE_NIFTY500_UNIVERSE is False):
    - Technical Agent (strategies/technical_agent.py): does any active
      strategy propose a trade today?
    - Fundamental Agent (fundamentals/fundamental_agent.py): is the company
      financially healthy?
  Only symbols that BOTH pass fundamentals AND have an active technical
  signal move on to Stage 2 -- this is what keeps the expensive stage cheap,
  since most of the market won't have anything going on any given day.

STAGE 2 (paid, calls Claude -- only for Stage 1 survivors):
  - News Agent: real-time sentiment read.
  - Research Analyst: synthesizes Technical + Fundamental + News into one
    verdict per symbol.

STAGE 3 (decision + action):
  - Portfolio Manager: confidence-weighted sizing, prioritizes the strongest
    convictions if capital is limited, produces one final decision per
    candidate with a reason.
  - Execution Engine: places the approved trades -- paper-logs them by
    default, or real Kite orders if config.LIVE_TRADING is True (and
    --paper wasn't passed on the command line as an extra safety override).
    Live orders use LIMIT pricing (through signal.entry_price by
    config.LIMIT_ORDER_BUFFER_PCT) rather than plain MARKET orders -- Kite
    rejects plain MARKET orders via API unless "market protection" is
    configured on the account (confirmed via test_live_order.py).
  - Sends a summary to Telegram (config.TELEGRAM_BOT_TOKEN/CHAT_ID), falling
    back to printing if Telegram isn't configured yet.

IMPORTANT -- before running this for real:
1. Kite's access_token expires every day. You must run the login flow
   (test_kite_connection.py's steps) each morning and put the fresh
   access_token into config.settings.KITE_ACCESS_TOKEN BEFORE running this
   script with LIVE_TRADING = True. An expired token will cause every live
   order to fail -- this script does not refresh it for you.
2. Scanning the full Nifty 500 universe means ~450 yfinance calls for price
   history plus ~450 for fundamentals -- this can take a while (well over
   10 minutes) depending on your connection. Run it with enough lead time
   before market open, or use --limit=N while testing so you're not
   waiting on a full scan every time.
3. Capital is read live from your Kite account (via fetch_available_capital),
   NOT from config.STARTING_CAPITAL -- so position sizing always reflects
   whatever's actually in your account right now, even if that changes day
   to day. In LIVE mode this is mandatory: if fetching your real balance
   fails for any reason (stale token, network issue), the run aborts rather
   than sizing trades against a guessed number. In PAPER mode it's
   best-effort: it'll use your real Kite balance if credentials are set up
   and reachable, otherwise it quietly falls back to
   config.settings.STARTING_CAPITAL as a placeholder.
4. Chief Investment AI's monthly plan (data/monthly_plan.json, see
   cio/plan_state.py) now actually constrains this run: active_strategies
   comes from the plan if one exists (else config.ACTIVE_STRATEGIES), and
   trades are sized against min(real Kite capital, plan.capital_allocated)
   -- never against money that isn't really in the account, and never past
   what Chief Investment AI actually authorized for the month. Run
   monthly_review.py (scheduled for the 1st of each month) to update the
   plan. RISK_PER_TRADE_PCT/MAX_OPEN_POSITIONS/MAX_DEPLOYED_CAPITAL_PCT/
   DAILY_LOSS_CIRCUIT_BREAKER_PCT are still static config.settings values --
   Chief Investment AI's plan doesn't touch those, only capital and strategy
   selection.
"""

import sys
from datetime import date

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series
from strategies.technical_agent import get_technical_signals, first_available_signal
from fundamentals.fundamental_agent import fetch_fundamentals, check_health
from news.news_agent import analyze_news, disabled_news_assessment
from research.research_analyst import analyze_stock
from portfolio.portfolio_manager import allocate, build_decision_log, TradeCandidate
from risk.risk_manager import RiskManager
from execution.execution_engine import ExecutionEngine, fetch_available_capital
from execution.positions import fetch_holdings
from execution.position_state import reconcile_closed_positions, record_new_position
from auth.kite_auto_login import ensure_fresh_kite_session
from cio.chief_investment_ai import MonthlyPlan
from cio.plan_state import load_monthly_plan, save_monthly_plan, effective_active_strategies, effective_capital_cap
from reporting.telegram_notifier import send_telegram_message

PROGRESS_EVERY = 25  # print a progress line every N symbols during the Stage 1 scan


def parse_cli_args():
    limit = None
    force_paper = False
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
        elif arg == "--paper":
            force_paper = True
    return limit, force_paper


def get_universe(active_strategies: list, limit: int = None) -> list:
    if settings.USE_NIFTY500_UNIVERSE:
        try:
            symbols = get_nifty500_symbols()
        except FileNotFoundError as e:
            print(f"WARNING: {e}\nFalling back to config.SYMBOLS.")
            symbols = list(settings.SYMBOLS)
    else:
        symbols = sorted({s for key in active_strategies
                           for s in settings.STRATEGY_SYMBOLS.get(key, settings.SYMBOLS)})

    if limit is not None:
        symbols = symbols[:limit]
    return symbols


def run_stage1_scan(symbols: list, regime_series, active_strategies: list) -> list:
    """
    Cheap scan (no LLM calls): fetches price history + fundamentals for
    every symbol, returns only those that pass fundamentals AND have at
    least one active technical signal today.

    Returns a list of dicts: {"symbol", "technical_signals", "fundamentals_result"}
    """
    print(f"\nStage 1: scanning {len(symbols)} symbols (Technical + Fundamentals, no LLM cost)...")
    survivors = []

    for i, symbol in enumerate(symbols, start=1):
        if i % PROGRESS_EVERY == 0 or i == len(symbols):
            print(f"  ... scanned {i}/{len(symbols)}")

        try:
            price_history = fetch_all([symbol], period="1y").get(symbol)
        except Exception as e:
            print(f"WARNING: could not fetch price history for {symbol}: {e}")
            continue
        if price_history is None or len(price_history) < 60:
            continue  # not enough history yet, or fetch failed silently

        technical_signals = get_technical_signals(symbol, price_history, regime_series, active_strategies)
        has_signal = any(sig is not None for sig in technical_signals.values())
        if not has_signal:
            continue

        try:
            metrics = fetch_fundamentals(symbol)
        except Exception as e:
            print(f"WARNING: could not fetch fundamentals for {symbol}: {e}")
            continue
        fundamentals_result = check_health(symbol, metrics, settings.FUNDAMENTALS_CRITERIA)
        if not fundamentals_result.passed:
            continue

        survivors.append({
            "symbol": symbol,
            "technical_signals": technical_signals,
            "fundamentals_result": fundamentals_result,
        })

    print(f"Stage 1 complete: {len(survivors)} symbol(s) passed both filters and move to Stage 2.")
    return survivors


def run_stage2_research(survivors: list) -> list:
    """
    Expensive stage (calls Claude twice per symbol): News Agent + Research
    Analyst for every Stage 1 survivor. Returns a list of TradeCandidate.
    """
    print(f"\nStage 2: researching {len(survivors)} candidate(s) (News Agent + Research Analyst, calls Claude)...")
    candidates = []

    for item in survivors:
        symbol = item["symbol"]
        technical_signals = item["technical_signals"]
        fundamentals_result = item["fundamentals_result"]

        print(f"  Researching {symbol}...")
        if settings.USE_NEWS_AGENT:
            news_assessment = analyze_news(symbol, api_key=settings.ANTHROPIC_API_KEY,
                                            max_items=settings.NEWS_MAX_ARTICLES)
        else:
            news_assessment = disabled_news_assessment(symbol)
        research_result = analyze_stock(
            symbol, technical_signals, fundamentals_result, news_assessment,
            api_key=settings.ANTHROPIC_API_KEY,
        )
        print(f"    Verdict: {research_result.verdict.upper()} ({research_result.confidence:.0%})")

        candidate_signal = first_available_signal(technical_signals)
        candidates.append(TradeCandidate(
            symbol=symbol, signal=candidate_signal, research_assessment=research_result,
        ))

    return candidates


def get_available_capital(live_trading: bool):
    """
    Determines how much capital to size trades against today.

    LIVE mode: mandatory real fetch from Kite. Refuses to guess -- if the
    fetch fails for any reason (stale KITE_ACCESS_TOKEN, network issue,
    unexpected response), returns None so the caller aborts the run rather
    than sizing real orders against a number that might be wrong.

    PAPER mode: best-effort. Uses your real Kite balance if credentials are
    filled in and reachable (so paper runs simulate against realistic
    capital too), otherwise quietly falls back to config.STARTING_CAPITAL --
    paper trading can still proceed on a placeholder number.
    """
    if live_trading:
        print("\nFetching real available capital from Kite (required for live trading)...")
        try:
            capital = fetch_available_capital(settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN)
        except Exception as e:
            print(f"\nABORTING -- could not fetch real capital from Kite: {e}")
            return None
        if capital <= 0:
            print(f"\nABORTING -- available capital is Rs.{capital:,.2f}, nothing to trade with.")
            return None
        print(f"Real available capital: Rs.{capital:,.2f}")
        return capital

    if settings.KITE_API_KEY and settings.KITE_ACCESS_TOKEN:
        try:
            capital = fetch_available_capital(settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN)
            print(f"\nUsing your real Kite balance for this paper run: Rs.{capital:,.2f}")
            return capital
        except Exception as e:
            print(f"\nNOTE: could not fetch real capital from Kite ({e}); "
                  f"using config.STARTING_CAPITAL instead for this paper run.")
    return settings.STARTING_CAPITAL


def get_current_holdings(live_trading: bool) -> list:
    """
    Real current Kite holdings, used to seed RiskManager (so MAX_OPEN_POSITIONS
    and deployed-capital limits respect what's actually held across days, not
    just what happened in this run) and to reconcile closures (a position that
    disappeared since last check was closed by a GTT trigger or by
    monitor_positions.py's own early exit).

    Only meaningful in LIVE mode -- paper mode never actually buys anything on
    Kite, so treating real holdings as "the bot's positions" there would be
    wrong (a paper "position" would never show up in real holdings, and
    reconciliation would immediately -- incorrectly -- think it had closed).
    Live mode requires this to succeed (same abort-rather-than-guess policy as
    get_available_capital): sizing/reconciling against an unknown position
    picture risks breaching real risk limits.
    """
    if not live_trading:
        return []
    return fetch_holdings(settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN)


def main():
    limit, force_paper = parse_cli_args()
    live_trading = settings.LIVE_TRADING and not force_paper

    print("=" * 60)
    print("DAILY RUN -- " + ("LIVE TRADING" if live_trading
                              else "PAPER MODE" + (" (forced by --paper)" if force_paper else "")))
    print("=" * 60)

    session_ok = ensure_fresh_kite_session(settings)
    if live_trading and not session_ok:
        print("\nABORTING -- could not establish a valid Kite session (auto-login failed or "
              "isn't configured). Run refresh_kite_token.py manually, or fill in "
              "KITE_USER_ID/KITE_PASSWORD/KITE_TOTP_SECRET in config/settings.py.")
        send_telegram_message(
            "*Daily run aborted*\n\nCould not establish a valid Kite session for live trading.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    capital = get_available_capital(live_trading)
    if capital is None:
        return

    plan = load_monthly_plan()
    if plan is None and live_trading:
        # First-ever live run: seed a starting plan from the real capital we
        # just fetched, so Chief Investment AI has something to review and
        # adjust from next month, instead of waiting for monthly_review.py
        # to invent one from config placeholders.
        plan = MonthlyPlan(
            month_label=date.today().strftime("%B %Y"),
            capital_allocated=capital,
            target_return_pct=3.0,
            active_strategies=list(settings.ACTIVE_STRATEGIES),
            notes="Starting plan, bootstrapped from real capital on first live run.",
        )
        save_monthly_plan(plan)
        print(f"\nNo Chief Investment AI plan existed yet -- bootstrapped one "
              f"(capital Rs.{capital:,.2f}, strategies {plan.active_strategies}).")

    active_strategies = effective_active_strategies(plan, settings)
    capital_for_sizing = effective_capital_cap(plan, capital)
    if capital_for_sizing < capital:
        print(f"Chief Investment AI's monthly cap limits sizing to Rs.{capital_for_sizing:,.2f} "
              f"(real capital is Rs.{capital:,.2f}).")

    try:
        holdings = get_current_holdings(live_trading)
    except Exception as e:
        print(f"\nABORTING -- could not fetch current holdings from Kite: {e}")
        send_telegram_message(
            f"*Daily run aborted*\n\nCould not fetch current holdings from Kite: {e}",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    if live_trading:
        reconcile_closed_positions(
            holdings, settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN,
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )

    symbols = get_universe(active_strategies, limit=limit)
    print(f"Universe: {len(symbols)} symbol(s)"
          + (f" (limited via --limit={limit})" if limit else ""))

    print("\nFetching Nifty 50 regime...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)

    survivors = run_stage1_scan(symbols, regime_series, active_strategies)

    if not survivors:
        print("\nNo symbols passed Stage 1 today -- nothing to research or trade. This is a normal, "
              "quiet day; the system is not forcing trades that aren't there.")
        send_telegram_message(
            f"*Daily run -- no trades today*\n\nCapital available: Rs.{capital:,.2f}\n\n"
            f"No symbols passed both the technical signal and fundamentals checks today. "
            f"Nothing was researched or traded.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    candidates = run_stage2_research(survivors)

    risk_manager = RiskManager(
        capital=capital_for_sizing,
        risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
        daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
    )
    risk_manager.seed_existing_positions(holdings)
    decisions = allocate(candidates, risk_manager)

    execution_engine = ExecutionEngine(
        live_trading=live_trading,
        api_key=settings.KITE_API_KEY,
        access_token=settings.KITE_ACCESS_TOKEN,
        limit_order_buffer_pct=settings.LIMIT_ORDER_BUFFER_PCT,
    )

    print("\n" + "=" * 60)
    print(build_decision_log(decisions))
    print("=" * 60)

    executed = []
    for decision in decisions:
        if decision.approved and decision.approved_trade is not None:
            result = execution_engine.place_order(decision.approved_trade)
            executed.append((decision, result))
            if live_trading:
                signal = decision.approved_trade.signal
                record_new_position(
                    signal.symbol, decision.quantity, signal.entry_price, result.get("gtt_id"),
                )

    report_lines = [
        f"*Daily run -- {'LIVE' if live_trading else 'PAPER'} mode*",
        "",
        f"Capital available: Rs.{capital:,.2f}"
        + (f" (Chief Investment AI cap this month: Rs.{capital_for_sizing:,.2f})"
           if capital_for_sizing < capital else ""),
        f"Universe scanned: {len(symbols)}",
        f"Stage 1 survivors (signal + fundamentals passed): {len(survivors)}",
        f"Trades approved: {len([d for d in decisions if d.approved])}",
        f"Trades rejected: {len([d for d in decisions if not d.approved])}",
        "",
        build_decision_log(decisions),
    ]
    send_telegram_message("\n".join(report_lines), settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
