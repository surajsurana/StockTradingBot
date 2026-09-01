"""
Portfolio B's daily cycle -- wires the agent stack (Fundamental Agent,
News Agent, Research Analyst, Portfolio Manager, Risk Manager) against
its own live watchlist (portfolio_b/engine.py's get_watchlist(),
user-editable via Telegram, see portfolio_b/telegram_bot.py) and existing
holdings, on Portfolio B's own isolated capital (portfolio_b/state.py).
Structurally identical to portfolio_c/daily.py in every way EXCEPT
candidate sourcing: Portfolio C reads an anchor strategy's own signal,
Portfolio B builds a synthetic one from price action alone (see
portfolio_b/engine.py's build_watchlist_signal) -- everything downstream
(agent pipeline, allocation, next-day-open fills, idempotency) is the
same discipline, deliberately duplicated rather than shared with
portfolio_c/ so a bug in one can never touch the other's live state.

Same next-day-open, no-lookahead fill discipline and DISCLOSED
SIMPLIFICATION as Portfolio C: quantity is decided at signal time,
only the fill PRICE is deferred to the next real Open; an overnight gap
through the stop or past affordability abandons the entry rather than
force-filling it.
"""

import datetime
from typing import Callable, Optional

from config import settings as agent_settings
from deployment.paper_trading_engine import _get_open_price
from fundamentals.fundamental_agent import check_health, fetch_fundamentals
from news.news_agent import analyze_news_cached, disabled_news_assessment
from portfolio.portfolio_manager import TradeCandidate, allocate
from portfolio_b import state as pbs
from portfolio_b.engine import build_watchlist_signal, get_watchlist
from research.research_analyst import ResearchAssessment, analyze_stock
from risk.risk_manager import RiskManager
from strategies.price_action import compute_price_action
from swing_research.backtesting_engine import _hit_stop, _trade_pnl


def _build_risk_manager(capital: float) -> RiskManager:
    """Same risk discipline as portfolio_c/daily.py's own
    _build_risk_manager() -- config.settings' production RiskManager
    numbers, applied to Portfolio B's own isolated capital."""
    return RiskManager(
        capital=capital,
        risk_per_trade_pct=agent_settings.RISK_PER_TRADE_PCT,
        max_open_positions=agent_settings.MAX_OPEN_POSITIONS,
        max_deployed_capital_pct=agent_settings.MAX_DEPLOYED_CAPITAL_PCT,
        daily_loss_circuit_breaker_pct=agent_settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
        max_capital_per_trade_pct=agent_settings.MAX_CAPITAL_PER_TRADE_PCT,
    )


def _evaluate_symbol(symbol: str, technical_signals: dict, price_history, api_key: str,
                      entry_price: Optional[float] = None,
                      fetch_fundamentals_fn: Callable = fetch_fundamentals,
                      news_call_fn: Optional[Callable] = None,
                      research_call_fn: Optional[Callable] = None) -> Optional[ResearchAssessment]:
    """Identical pipeline to portfolio_c/daily.py's own _evaluate_symbol()
    -- see that module's docstring. Duplicated, not imported, to keep the
    two portfolios' code paths structurally independent."""
    price_action = compute_price_action(price_history, entry_price=entry_price)

    try:
        metrics = fetch_fundamentals_fn(symbol)
    except Exception:
        return None
    fundamentals_result = check_health(symbol, metrics, agent_settings.FUNDAMENTALS_CRITERIA)

    if agent_settings.USE_NEWS_AGENT:
        news_assessment = analyze_news_cached(symbol, api_key=api_key,
                                               max_items=agent_settings.NEWS_MAX_ARTICLES,
                                               call_fn=news_call_fn)
    else:
        news_assessment = disabled_news_assessment(symbol)

    return analyze_stock(symbol, technical_signals, fundamentals_result, news_assessment,
                          api_key=api_key, call_fn=research_call_fn, price_action=price_action)


def _process_existing_positions(portfolio: dict, data: dict, as_of_date: datetime.date, api_key: str,
                                 fetch_fundamentals_fn: Callable = fetch_fundamentals,
                                 news_call_fn: Optional[Callable] = None,
                                 research_call_fn: Optional[Callable] = None) -> tuple:
    """
    Same mechanical-stop-then-agent-recheck pattern as portfolio_c/daily.py's
    own _process_existing_positions() -- no anchor_candidates parameter here
    since Portfolio B has no anchor strategy at all; technical_signals is
    always {} (Research Analyst reads "no active strategies reported a
    signal today", same as any Portfolio C symbol no anchor flagged).

    Returns (realized_exits: list[dict], decision_log_entries: list[dict]).
    Mutates portfolio in place (positions, cash, pending_exits).
    """
    realized_exits = []
    decision_entries = []

    for symbol in list(portfolio["positions"].keys()):
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        frame = df.sort_index()
        rows_today = frame[frame.index.date == as_of_date]
        if rows_today.empty:
            continue
        row = rows_today.iloc[0]
        pos = portfolio["positions"][symbol]

        if _hit_stop(pos["direction"], float(row["Low"]), float(row["High"]), pos["stop_loss"]):
            exit_price = pos["stop_loss"]
            pnl = _trade_pnl(pos["direction"], pos["entry_price"], exit_price, pos["quantity"])
            portfolio["cash"] += pos["quantity"] * exit_price
            portfolio["positions"].pop(symbol)
            trade = {"symbol": symbol, "direction": pos["direction"], "entry_date": pos["entry_date"],
                      "exit_date": as_of_date.isoformat(), "entry_price": pos["entry_price"],
                      "exit_price": exit_price, "quantity": pos["quantity"], "pnl": pnl,
                      "exit_reason": "stop_loss", "strategy_name": pos.get("strategy_name", "")}
            pbs.append_trade(trade)
            realized_exits.append(trade)
            continue

        assessment = _evaluate_symbol(symbol, {}, frame, api_key, entry_price=pos["entry_price"],
                                       fetch_fundamentals_fn=fetch_fundamentals_fn,
                                       news_call_fn=news_call_fn, research_call_fn=research_call_fn)
        if assessment is None:
            continue

        decision_entries.append({
            "date": as_of_date.isoformat(), "symbol": symbol, "kind": "held_position_recheck",
            "agent_outputs": {"verdict": assessment.verdict, "confidence": assessment.confidence,
                               "reasoning": assessment.reasoning},
        })

        if assessment.verdict == "unfavorable":
            portfolio["pending_exits"][symbol] = {"exit_reason": "unfavorable_verdict",
                                                     "signal_date": as_of_date.isoformat()}

    return realized_exits, decision_entries


def _resolve_pending(portfolio: dict, data: dict, as_of_date: datetime.date) -> tuple:
    """Identical to portfolio_c/daily.py's own _resolve_pending() -- fills
    anything queued on a PRIOR day against TODAY's real Open. Mutates
    portfolio in place. Returns (new_entries, new_exits)."""
    new_entries, new_exits = [], []

    for symbol in list(portfolio["pending_exits"].keys()):
        if symbol not in portfolio["positions"]:
            portfolio["pending_exits"].pop(symbol)
            continue
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        open_price = _get_open_price(df.sort_index(), as_of_date)
        if open_price is None:
            continue
        pending = portfolio["pending_exits"].pop(symbol)
        pos = portfolio["positions"].pop(symbol)
        pnl = _trade_pnl(pos["direction"], pos["entry_price"], open_price, pos["quantity"])
        portfolio["cash"] += pos["quantity"] * open_price
        trade = {"symbol": symbol, "direction": pos["direction"], "entry_date": pos["entry_date"],
                  "exit_date": as_of_date.isoformat(), "entry_price": pos["entry_price"],
                  "exit_price": open_price, "quantity": pos["quantity"], "pnl": pnl,
                  "exit_reason": pending["exit_reason"], "strategy_name": pos.get("strategy_name", "")}
        pbs.append_trade(trade)
        new_exits.append(trade)

    for symbol in list(portfolio["pending_entries"].keys()):
        if symbol in portfolio["positions"]:
            portfolio["pending_entries"].pop(symbol)
            continue
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        open_price = _get_open_price(df.sort_index(), as_of_date)
        if open_price is None:
            continue
        pending = portfolio["pending_entries"].pop(symbol)
        quantity = pending["quantity"]
        cost = quantity * open_price
        risk_per_share = (open_price - pending["stop_loss"] if pending["direction"] == "BUY"
                           else pending["stop_loss"] - open_price)
        if quantity < 1 or cost > portfolio["cash"] or risk_per_share <= 0:
            continue
        portfolio["cash"] -= cost
        portfolio["positions"][symbol] = {
            "direction": pending["direction"], "entry_price": open_price,
            "entry_date": as_of_date.isoformat(), "quantity": quantity,
            "stop_loss": pending["stop_loss"], "target": pending["target"],
            "strategy_name": pending["strategy_name"], "confidence": pending["confidence"],
        }
        new_entries.append({"symbol": symbol, "entry_price": open_price, "quantity": quantity,
                              "stop_loss": pending["stop_loss"], "signal_date": pending["signal_date"]})

    return new_entries, new_exits


def _evaluate_new_candidates(portfolio: dict, data: dict, as_of_date: datetime.date, api_key: str,
                              watchlist: Optional[list] = None,
                              fetch_fundamentals_fn: Callable = fetch_fundamentals,
                              news_call_fn: Optional[Callable] = None,
                              research_call_fn: Optional[Callable] = None) -> list:
    """
    Runs the full agent pipeline for every watchlist symbol not already
    held or pending, builds a synthetic Signal for each
    (build_watchlist_signal), and allocates whichever came back
    "favorable" via Portfolio Manager + Risk Manager against Portfolio
    B's own isolated capital. Approved trades are QUEUED, not filled
    today.

    watchlist: defaults to None, meaning "read the LIVE watchlist fresh"
    (get_watchlist()) -- deliberately NOT a mutable default argument
    bound at function-definition time, since /addstock and /removestock
    (portfolio_b/telegram_bot.py) can change deployment/state/portfolio_b/
    watchlist.json between calls, and every real caller must see
    whatever is current right now, not whatever it was when this module
    was first imported. Pass an explicit list only for tests.

    Returns decision_log_entries. Mutates portfolio in place
    (pending_entries only).
    """
    if watchlist is None:
        watchlist = get_watchlist()
    decision_entries = []
    trade_candidates = []
    already_committed = set(portfolio["positions"]) | set(portfolio["pending_entries"])

    for symbol in watchlist:
        if symbol in already_committed:
            continue
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        frame = df.sort_index()

        assessment = _evaluate_symbol(symbol, {}, frame, api_key, entry_price=None,
                                       fetch_fundamentals_fn=fetch_fundamentals_fn,
                                       news_call_fn=news_call_fn, research_call_fn=research_call_fn)
        if assessment is None:
            continue

        signal = build_watchlist_signal(symbol, frame)

        entry = {
            "date": as_of_date.isoformat(), "symbol": symbol, "kind": "watchlist_candidate",
            "candidates_considered": [symbol],
            "information_seen": {"synthetic_signal_reason": signal.reason},
            "agent_outputs": {"verdict": assessment.verdict, "confidence": assessment.confidence,
                               "reasoning": assessment.reasoning},
        }

        if assessment.verdict != "favorable":
            entry["rejections"] = f"Research Analyst verdict was '{assessment.verdict}', not favorable"
            decision_entries.append(entry)
            continue

        trade_candidates.append((symbol, TradeCandidate(symbol=symbol, signal=signal,
                                                          research_assessment=assessment), entry))

    risk_manager = _build_risk_manager(portfolio["cash"])
    for symbol, pos in portfolio["positions"].items():
        risk_manager.open_positions_count += 1
        risk_manager.capital_deployed += pos["quantity"] * pos["entry_price"]

    decisions = allocate([tc for _, tc, _ in trade_candidates], risk_manager)
    # See portfolio_c/daily.py's own decisions_by_symbol comment: allocate()
    # re-sorts approved candidates by confidence, so matching must be by
    # symbol, never position.
    decisions_by_symbol = {decision.symbol: decision for decision in decisions}

    for symbol, tc, entry in trade_candidates:
        decision = decisions_by_symbol[symbol]
        entry["final_ranking"] = decision.confidence
        if decision.approved:
            portfolio["pending_entries"][symbol] = {
                "direction": tc.signal.direction, "stop_loss": tc.signal.stop_loss,
                "target": tc.signal.target, "signal_date": as_of_date.isoformat(),
                "signal_price": tc.signal.entry_price, "strategy_name": tc.signal.strategy_name,
                "confidence": decision.confidence, "quantity": decision.quantity,
            }
            entry["allocation"] = {"quantity": decision.quantity, "capital_deployed": decision.capital_deployed,
                                    "risk_multiplier": decision.risk_multiplier}
        else:
            entry["rejections"] = decision.reason
        decision_entries.append(entry)

    return decision_entries


def run_portfolio_b_daily(data: dict, as_of_date: Optional[datetime.date] = None,
                           api_key: Optional[str] = None,
                           fetch_fundamentals_fn: Callable = fetch_fundamentals,
                           news_call_fn: Optional[Callable] = None,
                           research_call_fn: Optional[Callable] = None,
                           force: bool = False) -> dict:
    """
    Full daily cycle -- same shape and idempotency guarantee as
    portfolio_c/daily.py's run_portfolio_c_daily(), see that function's
    docstring for the full sequencing rationale. The one difference:
    step 3 evaluates the LIVE watchlist (portfolio_b/engine.py's
    get_watchlist()) instead of an anchor strategy's own signals.

    data: {symbol: OHLCV DataFrame} -- must cover every current watchlist symbol
    and every currently-held symbol.
    """
    as_of_date = as_of_date or datetime.date.today()
    api_key = api_key or agent_settings.ANTHROPIC_API_KEY
    portfolio = pbs.load_portfolio()

    last_processed = portfolio.get("last_processed_date")
    if not force and last_processed is not None and datetime.date.fromisoformat(last_processed) >= as_of_date:
        return {"status": "skipped_already_processed", "as_of_date": as_of_date.isoformat(),
                "last_processed_date": last_processed}

    resolved_entries, resolved_exits = _resolve_pending(portfolio, data, as_of_date)

    realized_exits, exit_decision_entries = _process_existing_positions(
        portfolio, data, as_of_date, api_key, fetch_fundamentals_fn=fetch_fundamentals_fn,
        news_call_fn=news_call_fn, research_call_fn=research_call_fn)

    entry_decision_entries = _evaluate_new_candidates(
        portfolio, data, as_of_date, api_key, fetch_fundamentals_fn=fetch_fundamentals_fn,
        news_call_fn=news_call_fn, research_call_fn=research_call_fn)

    for entry in exit_decision_entries + entry_decision_entries:
        pbs.append_decision_log(entry)

    positions_value = sum(pos["quantity"] * pos["entry_price"] for pos in portfolio["positions"].values())
    mark_to_market_equity = portfolio["cash"] + positions_value
    pbs.append_daily_equity(as_of_date, cash=portfolio["cash"], equity=mark_to_market_equity)

    portfolio["last_processed_date"] = as_of_date.isoformat()
    pbs.save_portfolio(portfolio)

    return {
        "status": "processed", "as_of_date": as_of_date.isoformat(),
        "new_entries": resolved_entries, "new_exits": resolved_exits + realized_exits,
        "open_positions": len(portfolio["positions"]), "cash": portfolio["cash"],
        "mark_to_market_equity": mark_to_market_equity,
    }


def resolve_portfolio_b_at_open(fetch_open_data_fn: Callable[[], dict],
                                 as_of_date: Optional[datetime.date] = None) -> dict:
    """Identical purpose to portfolio_c/daily.py's own
    resolve_portfolio_c_at_open() -- see that function's docstring.
    Near-open pass: resolves only already-queued fills, never touches
    last_processed_date or daily_equity.jsonl."""
    as_of_date = as_of_date or datetime.date.today()
    portfolio = pbs.load_portfolio()

    if not portfolio.get("pending_entries") and not portfolio.get("pending_exits"):
        return {"status": "processed", "as_of_date": as_of_date.isoformat(),
                "new_entries": [], "new_exits": []}

    data = fetch_open_data_fn()
    new_entries, new_exits = _resolve_pending(portfolio, data, as_of_date)
    pbs.save_portfolio(portfolio)

    return {"status": "processed", "as_of_date": as_of_date.isoformat(),
            "new_entries": new_entries, "new_exits": new_exits}
