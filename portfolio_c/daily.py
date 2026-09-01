"""
Portfolio C's daily cycle -- wires the agent stack (Fundamental Agent,
News Agent, Research Analyst, Portfolio Manager, Risk Manager) against
today's anchor-strategy candidates and existing holdings, on Portfolio
C's own isolated capital (portfolio_c/state.py). Never imports
deployment/paper_trading_engine.py's stateful, strategy_key-keyed
functions (_load_portfolio/_save_portfolio/run_daily/...) -- only the
handful of PURE, side-effect-free helpers it also uses internally
(_get_open_price, and swing_research.backtesting_engine's _hit_stop/
_trade_pnl), so a bug here structurally cannot touch Portfolio A's files.

Same next-day-open, no-lookahead fill discipline Portfolio A uses
(deployment/paper_trading_engine.py's fill_timing="next_day_open"): a
decision made from TODAY's close-based data is queued, then filled
against the REAL next trading day's Open -- never today's own close.

DISCLOSED SIMPLIFICATION vs. Portfolio A's own fill mechanics: quantity
is decided at signal time (Risk Manager sizes off the signal's own
entry_price, i.e. the close the signal was detected at), not re-sized
against the real fill price the next day. Only the FILL PRICE itself is
deferred to the next real Open, for P&L accuracy. If the overnight gap
makes the real fill unaffordable (cost > cash) or crosses through the
stop, the entry is abandoned, never force-filled -- same safety guard
Portfolio A's own _resolve_pending_fills() applies.
"""

import datetime
from typing import Callable, Optional

from config import settings as agent_settings
from deployment.paper_trading_engine import _get_open_price
from fundamentals.fundamental_agent import check_health, fetch_fundamentals
from news.news_agent import analyze_news_cached, disabled_news_assessment
from portfolio.portfolio_manager import TradeCandidate, allocate
from portfolio_c import state as pcs
from portfolio_c.engine import collect_anchor_candidates
from research.research_analyst import ResearchAssessment, analyze_stock
from risk.risk_manager import RiskManager
from strategies.price_action import compute_price_action
from swing_research.backtesting_engine import _hit_stop, _trade_pnl


def _build_risk_manager(capital: float) -> RiskManager:
    """Reuses the SAME risk discipline config.settings' production
    RiskManager uses (RISK_PER_TRADE_PCT, MAX_OPEN_POSITIONS,
    MAX_DEPLOYED_CAPITAL_PCT, DAILY_LOSS_CIRCUIT_BREAKER_PCT,
    MAX_CAPITAL_PER_TRADE_PCT) rather than inventing new numbers for
    Portfolio C -- just applied to Portfolio C's own isolated capital."""
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
    """
    Same Fundamental + News + price-action + Research Analyst pipeline
    monitor_positions.py's evaluate_holding() already uses for a held
    position -- reused here for BOTH new candidates (entry_price=None)
    and existing holdings (entry_price=the position's own entry, so
    price_action's pct_since_entry is populated).

    Returns None (never raises) if fundamentals couldn't be fetched -- a
    data hiccup should skip this symbol today, never be misread as a
    verdict either way.
    """
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


def _process_existing_positions(portfolio: dict, data: dict, as_of_date: datetime.date,
                                 anchor_candidates: dict, api_key: str,
                                 fetch_fundamentals_fn: Callable = fetch_fundamentals,
                                 news_call_fn: Optional[Callable] = None,
                                 research_call_fn: Optional[Callable] = None) -> tuple:
    """
    For every open position: mechanical stop-loss first (price-triggered,
    filled SAME DAY at the stop -- needs only today's already-known
    Low/High, so no lookahead concern, same as Portfolio A). If the stop
    wasn't hit, re-run the full agent pipeline; an "unfavorable" verdict
    queues an exit for tomorrow's real Open (same monitor_positions.py
    convention: a deteriorating picture the price alone hasn't confirmed
    yet).

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
            pcs.append_trade(trade)
            realized_exits.append(trade)
            continue

        # Still-qualifying anchor signal today counts as its own technical
        # input, same as a fresh candidate would see -- an empty dict (no
        # entry in anchor_candidates for this symbol) reads to Research
        # Analyst as "no active strategies reported a signal today",
        # exactly the language _describe_technical() already uses.
        technical_signals = {k: v for k, v in anchor_candidates.get(symbol, {}).items()}
        assessment = _evaluate_symbol(symbol, technical_signals, frame, api_key,
                                       entry_price=pos["entry_price"],
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
    """
    Fills anything queued on a PRIOR day (pending_entries/pending_exits)
    against TODAY's real Open -- the second half of the next-day-open
    discipline. Mutates portfolio in place. Returns (new_entries, new_exits).
    """
    new_entries, new_exits = [], []

    for symbol in list(portfolio["pending_exits"].keys()):
        if symbol not in portfolio["positions"]:
            portfolio["pending_exits"].pop(symbol)   # already closed some other way (e.g. stop hit first)
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
        pcs.append_trade(trade)
        new_exits.append(trade)

    for symbol in list(portfolio["pending_entries"].keys()):
        if symbol in portfolio["positions"]:
            portfolio["pending_entries"].pop(symbol)   # already opened some other way
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
        # Overnight gap moved price through its own planned stop, or made
        # the fill unaffordable -- abandoned, never force-filled. Same
        # guard Portfolio A's own _resolve_pending_fills() applies.
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


def _evaluate_new_candidates(anchor_candidates: dict, portfolio: dict, data: dict,
                              as_of_date: datetime.date, api_key: str,
                              fetch_fundamentals_fn: Callable = fetch_fundamentals,
                              news_call_fn: Optional[Callable] = None,
                              research_call_fn: Optional[Callable] = None) -> tuple:
    """
    Runs the full agent pipeline for every anchor candidate not already
    held or already pending, builds TradeCandidates for whichever came
    back "favorable", and allocates them via Portfolio Manager + Risk
    Manager against Portfolio C's own isolated capital. Approved trades
    are QUEUED (pending_entries), not filled today.

    Returns (decision_log_entries: list[dict]). Mutates portfolio in
    place (pending_entries only -- cash/positions are untouched until
    _resolve_pending() fills these tomorrow).
    """
    decision_entries = []
    trade_candidates = []
    already_committed = set(portfolio["positions"]) | set(portfolio["pending_entries"])

    for symbol, signals_by_strategy in anchor_candidates.items():
        if symbol in already_committed:
            continue
        df = data.get(symbol)
        if df is None or df.empty:
            continue

        assessment = _evaluate_symbol(symbol, signals_by_strategy, df.sort_index(), api_key,
                                       entry_price=None, fetch_fundamentals_fn=fetch_fundamentals_fn,
                                       news_call_fn=news_call_fn, research_call_fn=research_call_fn)
        if assessment is None:
            continue

        # first_available_signal-equivalent: Risk Manager needs ONE
        # concrete signal to size against -- the first anchor strategy
        # that flagged this symbol today, same simplification
        # strategies/technical_agent.py's own first_available_signal()
        # already documents and accepts.
        chosen_signal = next(iter(signals_by_strategy.values()))

        entry = {
            "date": as_of_date.isoformat(), "symbol": symbol, "kind": "new_candidate",
            "candidates_considered": list(signals_by_strategy.keys()),
            "information_seen": {"anchor_signals": {k: v.reason for k, v in signals_by_strategy.items()}},
            "agent_outputs": {"verdict": assessment.verdict, "confidence": assessment.confidence,
                               "reasoning": assessment.reasoning},
        }

        if assessment.verdict != "favorable":
            entry["rejections"] = f"Research Analyst verdict was '{assessment.verdict}', not favorable"
            decision_entries.append(entry)
            continue

        trade_candidates.append((symbol, TradeCandidate(symbol=symbol, signal=chosen_signal,
                                                          research_assessment=assessment), entry))

    risk_manager = _build_risk_manager(portfolio["cash"])
    for symbol, pos in portfolio["positions"].items():
        risk_manager.open_positions_count += 1
        risk_manager.capital_deployed += pos["quantity"] * pos["entry_price"]

    decisions = allocate([tc for _, tc, _ in trade_candidates], risk_manager)
    # allocate() does NOT return decisions in the same order as its input:
    # rejected candidates stay in input order, but approved ones are
    # re-sorted by confidence (see portfolio/portfolio_manager.py's own
    # allocate() docstring/Step 2) -- so decisions must be matched back to
    # candidates by symbol, never by position. Each symbol appears at
    # most once in trade_candidates (one entry per anchor_candidates key),
    # so this lookup is unambiguous.
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


def run_portfolio_c_daily(data: dict, as_of_date: Optional[datetime.date] = None,
                           api_key: Optional[str] = None,
                           fetch_fundamentals_fn: Callable = fetch_fundamentals,
                           news_call_fn: Optional[Callable] = None,
                           research_call_fn: Optional[Callable] = None,
                           force: bool = False) -> dict:
    """
    Full daily cycle, in order:
      1. Resolve anything queued on a PRIOR day against today's real Open.
      2. Re-check existing holdings (mechanical stop, same-day; agent
         re-check, queued for tomorrow).
      3. Collect today's anchor candidates, run the agent pipeline,
         allocate via Portfolio Manager/Risk Manager, queue approvals.
      4. Record today's equity, persist the portfolio, append every
         decision to decision_log.jsonl.

    IDEMPOTENCY: same convention as deployment/paper_trading_engine.py's
    run_daily() -- portfolio["last_processed_date"] records the last date
    actually processed; calling this again for a date <= that is a safe
    no-op (status "skipped_already_processed") unless force=True, so a
    cron retry or an accidental double-invocation can never double-count
    a day's entries/exits/LLM calls.

    data: {symbol: OHLCV DataFrame} -- the frozen swing universe, same
    shape data/fetch_historical.py's fetch_all() returns. Must cover both
    today's anchor candidates and every currently-held symbol.

    Returns a result dict mirroring deployment/paper_trading_engine.py's
    run_daily() shape where it makes sense to: status, as_of_date,
    new_entries, new_exits, open_positions, cash, mark_to_market_equity.
    """
    as_of_date = as_of_date or datetime.date.today()
    api_key = api_key or agent_settings.ANTHROPIC_API_KEY
    portfolio = pcs.load_portfolio()

    last_processed = portfolio.get("last_processed_date")
    if not force and last_processed is not None and datetime.date.fromisoformat(last_processed) >= as_of_date:
        return {"status": "skipped_already_processed", "as_of_date": as_of_date.isoformat(),
                "last_processed_date": last_processed}

    anchor_candidates = collect_anchor_candidates(data, as_of_date)

    resolved_entries, resolved_exits = _resolve_pending(portfolio, data, as_of_date)

    realized_exits, exit_decision_entries = _process_existing_positions(
        portfolio, data, as_of_date, anchor_candidates, api_key,
        fetch_fundamentals_fn=fetch_fundamentals_fn, news_call_fn=news_call_fn,
        research_call_fn=research_call_fn)

    entry_decision_entries = _evaluate_new_candidates(
        anchor_candidates, portfolio, data, as_of_date, api_key,
        fetch_fundamentals_fn=fetch_fundamentals_fn, news_call_fn=news_call_fn,
        research_call_fn=research_call_fn)

    for entry in exit_decision_entries + entry_decision_entries:
        pcs.append_decision_log(entry)

    positions_value = sum(pos["quantity"] * pos["entry_price"] for pos in portfolio["positions"].values())
    mark_to_market_equity = portfolio["cash"] + positions_value
    pcs.append_daily_equity(as_of_date, cash=portfolio["cash"], equity=mark_to_market_equity)

    portfolio["last_processed_date"] = as_of_date.isoformat()
    pcs.save_portfolio(portfolio)

    return {
        "status": "processed", "as_of_date": as_of_date.isoformat(),
        "new_entries": resolved_entries, "new_exits": resolved_exits + realized_exits,
        "open_positions": len(portfolio["positions"]), "cash": portfolio["cash"],
        "mark_to_market_equity": mark_to_market_equity,
    }


def resolve_portfolio_c_at_open(fetch_open_data_fn: Callable[[], dict],
                                 as_of_date: Optional[datetime.date] = None) -> dict:
    """
    NEAR-MARKET-OPEN runner, same purpose as
    deployment/paper_trading_engine.py's resolve_pending_fills_at_open():
    the anchor strategies are End-of-Day (signals need the full day's
    close), so this does NOT detect new candidates -- it only resolves
    entries/exits already QUEUED by a PRIOR day's EOD run_portfolio_c_daily()
    call, against TODAY's real, now-available market Open. Intended for a
    separate ~9:30 IST cron entry.

    This does NOT change what price a fill uses -- run_portfolio_c_daily()
    already fills queued items against the real Open whenever it runs,
    even if that's not until the 15:45 IST EOD call. What this DOES change
    is how soon you find out: without this, a fill that happened at
    9:15 IST market open wouldn't be reported until the evening's EOD
    Telegram message. Deliberately reuses _resolve_pending() -- the EXACT
    same fill logic the EOD cycle's own step 1 uses -- so there is only
    ever one implementation of "how a queued signal becomes a real fill."

    Does NOT touch last_processed_date or daily_equity.jsonl -- both stay
    solely the EOD call's responsibility, exactly like Portfolio A's own
    equivalent function -- so that day's later EOD run is unaffected by
    this call having already happened.

    fetch_open_data_fn: zero-arg callable returning {symbol: DataFrame}
    -- deliberately a separate, lighter fetch than the EOD call's full
    3y pull (only needs each pending symbol's today bar's Open), left to
    the caller (see run_portfolio_c.py's --resolve-at-open) to decide how.

    Returns {"status": "processed", "as_of_date": ..., "new_entries": [...],
    "new_exits": [...]} -- empty lists if nothing was resolvable yet (no
    pending items, or today's Open isn't in the data provider yet --
    safely retried tomorrow morning, or picked up as a fallback by that
    day's own EOD call, whichever comes first).
    """
    as_of_date = as_of_date or datetime.date.today()
    portfolio = pcs.load_portfolio()

    if not portfolio.get("pending_entries") and not portfolio.get("pending_exits"):
        return {"status": "processed", "as_of_date": as_of_date.isoformat(),
                "new_entries": [], "new_exits": []}

    data = fetch_open_data_fn()
    new_entries, new_exits = _resolve_pending(portfolio, data, as_of_date)
    pcs.save_portfolio(portfolio)

    return {"status": "processed", "as_of_date": as_of_date.isoformat(),
            "new_entries": new_entries, "new_exits": new_exits}
