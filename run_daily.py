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
from news.news_agent import analyze_news_cached, disabled_news_assessment, ClaudeAPIError
from macro.macro_strategist import assess_macro_conditions
from research.research_analyst import analyze_stock
from portfolio.portfolio_manager import allocate, build_decision_log, TradeCandidate
from risk.risk_manager import RiskManager
from execution.execution_engine import ExecutionEngine, fetch_available_capital
from execution.positions import fetch_holdings
from execution.position_state import reconcile_closed_positions, record_new_position
from auth.kite_auto_login import ensure_fresh_kite_session
from cio.chief_investment_ai import MonthlyPlan
from cio.plan_state import (
    load_monthly_plan, save_monthly_plan,
    effective_active_strategies, effective_capital_cap, effective_risk_per_trade_pct,
    bump_capital_cap_to_real_capital,
)
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


def exclude_held_symbols(symbols: list, holdings: list) -> list:
    """
    Drops any symbol already held in the real account from today's scan.

    run_daily.py can now run several times a day (see cron). Without this, a
    symbol that signaled and got bought in an earlier run could signal again
    in a later run (technical strategies only look at today's candle, which
    doesn't know it already triggered a buy a few hours ago) and get bought
    a second time -- silently pyramiding into the same position with a
    second, conflicting GTT order, rather than a deliberate decision to add
    to it. Adding to winners on purpose is a legitimate strategy, but not
    one this system currently decides to do -- so for now, already-held
    symbols are simply skipped.
    """
    held_symbols = {h.symbol for h in holdings}
    return [s for s in symbols if s not in held_symbols]


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


def run_stage1_scan(symbols: list, regime_series, active_strategies: list) -> tuple[list, dict]:
    """
    Cheap scan (no LLM calls): fetches price history + fundamentals for
    every symbol, returns only those that pass fundamentals AND have at
    least one active technical signal today.

    Returns (survivors, rejection_counts). survivors is a list of dicts:
    {"symbol", "technical_signals", "fundamentals_result"}. rejection_counts
    tallies why everyone else was rejected:
      - "insufficient_history": fewer than 60 days of price history (new/thin
        listing), or the yfinance fetch itself failed for that symbol.
      - "no_signal": had enough history, but no active strategy's entry
        condition fired today (e.g. no MA crossover, not oversold).
      - "failed_fundamentals": had a signal, but the company didn't pass the
        health check (debt/ROE/revenue-growth), or its fundamentals couldn't
        be fetched at all.
    """
    print(f"\nStage 1: scanning {len(symbols)} symbols (Technical + Fundamentals, no LLM cost)...")
    survivors = []
    rejection_counts = {"insufficient_history": 0, "no_signal": 0, "failed_fundamentals": 0}

    for i, symbol in enumerate(symbols, start=1):
        if i % PROGRESS_EVERY == 0 or i == len(symbols):
            print(f"  ... scanned {i}/{len(symbols)}")

        try:
            price_history = fetch_all([symbol], period="1y").get(symbol)
        except Exception as e:
            print(f"WARNING: could not fetch price history for {symbol}: {e}")
            rejection_counts["insufficient_history"] += 1
            continue
        if price_history is None or len(price_history) < 60:
            rejection_counts["insufficient_history"] += 1
            continue  # not enough history yet, or fetch failed silently

        technical_signals = get_technical_signals(symbol, price_history, regime_series, active_strategies)
        has_signal = any(sig is not None for sig in technical_signals.values())
        if not has_signal:
            rejection_counts["no_signal"] += 1
            continue

        try:
            metrics = fetch_fundamentals(symbol)
        except Exception as e:
            print(f"WARNING: could not fetch fundamentals for {symbol}: {e}")
            rejection_counts["failed_fundamentals"] += 1
            continue
        fundamentals_result = check_health(symbol, metrics, settings.FUNDAMENTALS_CRITERIA)
        if not fundamentals_result.passed:
            rejection_counts["failed_fundamentals"] += 1
            continue

        survivors.append({
            "symbol": symbol,
            "technical_signals": technical_signals,
            "fundamentals_result": fundamentals_result,
        })

    print(f"Stage 1 complete: {len(survivors)} symbol(s) passed both filters and move to Stage 2 "
          f"({format_stage1_rejections(rejection_counts)}).")
    return survivors, rejection_counts


def format_stage1_rejections(rejection_counts: dict) -> str:
    return (f"{rejection_counts['no_signal']} no signal, "
            f"{rejection_counts['failed_fundamentals']} failed fundamentals, "
            f"{rejection_counts['insufficient_history']} insufficient history")


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
            # analyze_news_cached, not analyze_news -- run_daily.py now runs
            # several times a day (see cron), so this skips the Claude call
            # and reuses the last verdict when a symbol's headlines haven't
            # changed since the previous check, same as monitor_positions.py.
            news_assessment = analyze_news_cached(symbol, api_key=settings.ANTHROPIC_API_KEY,
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
            risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
            notes="Starting plan, bootstrapped from real capital on first live run.",
        )
        save_monthly_plan(plan)
        print(f"\nNo Chief Investment AI plan existed yet -- bootstrapped one "
              f"(capital Rs.{capital:,.2f}, strategies {plan.active_strategies}).")
    elif plan is not None and live_trading and bump_capital_cap_to_real_capital(plan, capital):
        # Real capital grew past the plan's cap (funds were likely added) --
        # raise the cap to match immediately rather than waiting for next
        # month's clamped Chief Investment AI review. See
        # bump_capital_cap_to_real_capital()'s docstring for why this is
        # safe: a deposit is a fact about the account, not an AI judgment
        # call, so it isn't subject to the same +/-20%/month guardrail.
        save_monthly_plan(plan)
        print(f"\nReal capital (Rs.{capital:,.2f}) exceeds the current plan's cap -- "
              f"raised the cap to match.")

    active_strategies = effective_active_strategies(plan, settings)
    capital_for_sizing = effective_capital_cap(plan, capital)
    risk_per_trade_pct = effective_risk_per_trade_pct(plan, settings)
    if capital_for_sizing < capital:
        print(f"Chief Investment AI's monthly cap limits sizing to Rs.{capital_for_sizing:,.2f} "
              f"(real capital is Rs.{capital:,.2f}).")
    if risk_per_trade_pct != settings.RISK_PER_TRADE_PCT:
        print(f"Chief Investment AI's monthly plan sets risk per trade to "
              f"{risk_per_trade_pct:.2%} (config default is {settings.RISK_PER_TRADE_PCT:.2%}).")

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

    if settings.USE_MACRO_STRATEGIST:
        macro_assessment = assess_macro_conditions(api_key=settings.ANTHROPIC_API_KEY,
                                                     max_items=settings.MACRO_MAX_ARTICLES)
        print(f"\nMacro Strategist: {macro_assessment.risk_level.upper()} -- {macro_assessment.reasoning}")

        if macro_assessment.risk_level == "high":
            print("\nMacro risk is HIGH today -- skipping today's scan entirely. "
                  "Existing positions and their GTT stop-loss/target orders are unaffected.")
            send_telegram_message(
                f"*Daily run -- no new trades today*\n\nMacro Strategist flagged HIGH risk: "
                f"{macro_assessment.reasoning}\n\nNo new positions will be opened today. "
                f"Existing positions and their stop-loss/target orders are unaffected.",
                settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
            )
            return

        if macro_assessment.risk_level == "elevated":
            risk_per_trade_pct = risk_per_trade_pct * 0.5
            print(f"Macro risk is ELEVATED today -- halving risk per trade to "
                  f"{risk_per_trade_pct:.2%} for today only (not persisted to the monthly plan).")

    symbols = get_universe(active_strategies, limit=limit)
    before_exclusion = len(symbols)
    symbols = exclude_held_symbols(symbols, holdings)
    if len(symbols) < before_exclusion:
        print(f"Excluding {before_exclusion - len(symbols)} already-held symbol(s) from today's "
              f"scan (avoids buying more of a position already open).")
    print(f"Universe: {len(symbols)} symbol(s)"
          + (f" (limited via --limit={limit})" if limit else ""))

    print("\nFetching Nifty 50 regime...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)

    survivors, rejection_counts = run_stage1_scan(symbols, regime_series, active_strategies)

    if not survivors:
        print("\nNo symbols passed Stage 1 today -- nothing to research or trade. This is a normal, "
              "quiet day; the system is not forcing trades that aren't there.")
        send_telegram_message(
            f"*Daily run -- no trades today*\n\nCapital available: Rs.{capital:,.2f}\n\n"
            f"No symbols passed both the technical signal and fundamentals checks today "
            f"({format_stage1_rejections(rejection_counts)}). Nothing was researched or traded.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    candidates = run_stage2_research(survivors)

    risk_manager = RiskManager(
        capital=capital_for_sizing,
        risk_per_trade_pct=risk_per_trade_pct,
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
            signal = decision.approved_trade.signal
            if live_trading:
                if result.get("status") == "success":
                    record_new_position(
                        signal.symbol, decision.quantity, signal.entry_price, result.get("gtt_id"),
                    )
                else:
                    # Portfolio Manager approved this trade, but the actual Kite
                    # order failed (rejected, insufficient margin, etc.) -- don't
                    # record a position that doesn't exist. Surfaced loudly:
                    # this is real money and the Telegram summary below would
                    # otherwise claim capital was deployed when it wasn't.
                    print(f"WARNING: live order for {signal.symbol} did not succeed -- "
                          f"not recording a position. Result: {result}")
                    attempted_price = result.get("price")
                    send_telegram_message(
                        f"*Order failed -- {signal.symbol}*\n\nPortfolio Manager approved this "
                        f"trade, but the Kite order itself failed -- no position was opened, no "
                        f"capital was deployed."
                        + (f"\n\nAttempted price: Rs.{attempted_price:,.2f}" if attempted_price else "")
                        + f"\n\nReason: {result.get('message', result)}",
                        settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
                    )

    report_lines = [
        f"*Daily run -- {'LIVE' if live_trading else 'PAPER'} mode*",
        "",
        f"Capital available: Rs.{capital:,.2f}"
        + (f" (Chief Investment AI cap this month: Rs.{capital_for_sizing:,.2f})"
           if capital_for_sizing < capital else ""),
        f"Universe scanned: {len(symbols)}",
        f"Stage 1 survivors (signal + fundamentals passed): {len(survivors)}",
        f"Stage 1 rejected: {format_stage1_rejections(rejection_counts)}",
        f"Trades approved: {len([d for d in decisions if d.approved])}",
        f"Trades rejected: {len([d for d in decisions if not d.approved])}",
        "",
        build_decision_log(decisions),
    ]

    order_lines = [
        f"  - {decision.symbol}: order placed at Rs.{result['price']:,.2f}"
        for decision, result in executed
        if result.get("status") in ("success", "paper") and result.get("price") is not None
    ]
    if order_lines:
        report_lines.append("")
        report_lines.append("ORDER PRICES:")
        report_lines.extend(order_lines)

    send_telegram_message("\n".join(report_lines), settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    try:
        main()
    except ClaudeAPIError as e:
        print(f"\nABORTING -- {e}")
        send_telegram_message(
            f"*Daily run aborted -- could not reach Claude*\n\n{e}\n\n"
            f"No new trades were evaluated today. Existing positions and their GTT "
            f"stop-loss/target orders are unaffected.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        sys.exit(1)
