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
   Known simplification: risk-rule settings themselves (RISK_PER_TRADE_PCT,
   MAX_OPEN_POSITIONS, etc.) still come straight from config.settings, not
   from whatever Chief Investment AI decided last -- there's no persistence
   of its monthly plan yet. Update config/settings.py by hand to match the
   latest monthly plan until that wiring exists.
"""

import sys

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from data.nifty500_universe import get_nifty500_symbols
from strategies.market_regime import build_regime_series
from strategies.technical_agent import get_technical_signals, first_available_signal
from fundamentals.fundamental_agent import fetch_fundamentals, check_health
from news.news_agent import analyze_news
from research.research_analyst import analyze_stock
from portfolio.portfolio_manager import allocate, build_decision_log, TradeCandidate
from risk.risk_manager import RiskManager
from execution.execution_engine import ExecutionEngine, fetch_available_capital
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


def get_universe(limit: int = None) -> list:
    if settings.USE_NIFTY500_UNIVERSE:
        try:
            symbols = get_nifty500_symbols()
        except FileNotFoundError as e:
            print(f"WARNING: {e}\nFalling back to config.SYMBOLS.")
            symbols = list(settings.SYMBOLS)
    else:
        symbols = sorted({s for key in settings.ACTIVE_STRATEGIES
                           for s in settings.STRATEGY_SYMBOLS.get(key, settings.SYMBOLS)})

    if limit is not None:
        symbols = symbols[:limit]
    return symbols


def run_stage1_scan(symbols: list, regime_series) -> list:
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

        technical_signals = get_technical_signals(symbol, price_history, regime_series)
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
        news_assessment = analyze_news(symbol, api_key=settings.ANTHROPIC_API_KEY,
                                        max_items=settings.NEWS_MAX_ARTICLES)
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


def main():
    limit, force_paper = parse_cli_args()
    live_trading = settings.LIVE_TRADING and not force_paper

    print("=" * 60)
    print("DAILY RUN -- " + ("LIVE TRADING" if live_trading
                              else "PAPER MODE" + (" (forced by --paper)" if force_paper else "")))
    print("=" * 60)

    capital = get_available_capital(live_trading)
    if capital is None:
        return

    symbols = get_universe(limit=limit)
    print(f"Universe: {len(symbols)} symbol(s)"
          + (f" (limited via --limit={limit})" if limit else ""))

    print("\nFetching Nifty 50 regime...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)

    survivors = run_stage1_scan(symbols, regime_series)

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
        capital=capital,
        risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
        daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
    )
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

    report_lines = [
        f"*Daily run -- {'LIVE' if live_trading else 'PAPER'} mode*",
        "",
        f"Capital available: Rs.{capital:,.2f}",
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
