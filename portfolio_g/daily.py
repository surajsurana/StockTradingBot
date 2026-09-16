"""
Pool G's daily cycle (2026-09-17) -- meant to run TWICE a day (morning
and evening IST), each time acting on the LIVE last-traded price, not a
daily bar close, since crypto trades all day and the point of this book
is to be responsive within the day, unlike Pool E's once-daily books.

Order every run, no exceptions:
  1. STOP-LOSS FIRST, mechanical, no LLM involved. Every open position's
     live price is checked against its stop (18% below entry, set at
     the time of entry and never moved). A stop that has been crossed
     is closed immediately, at the stop level, before the LLM is even
     called -- this book's downside protection never waits on a model
     call or a schedule.
  2. The LLM (portfolio_g/agent.py) is given today's snapshot for the
     five majors and whatever survived step 1, and returns one
     BUY/SELL/HOLD/AVOID call per coin with a reason.
  3. SELL calls close at the live price; BUY calls open a new position
     sized to at most 25% of the book, with the same 18% mechanical
     stop set immediately. HOLD/AVOID do nothing.
Every decision -- including HOLD/AVOID, which never touch the book --
is written to decision_log.jsonl, so the full reasoning trail exists
even for the coins nothing happened to.
"""

import datetime
from typing import Optional

from portfolio_g.agent import MAX_SLEEVE_PCT, STOP_LOSS_PCT, get_decisions
from portfolio_g.state import append_decision_log, append_trade, load_portfolio, save_portfolio

RISK_PCT_PER_UNIT = MAX_SLEEVE_PCT * STOP_LOSS_PCT   # quantity formula collapses to `capital_fraction / entry`


def build_snapshot(daily_history: dict, live_prices: dict) -> dict:
    """daily_history: {symbol: DataFrame of daily OHLCV, >=300 rows}.
    live_prices: {symbol: last-traded price}. Returns the snapshot dict
    agent.build_snapshot_prompt() expects."""
    snapshot = {}
    for symbol, df in daily_history.items():
        if symbol not in live_prices or df is None or df.empty:
            continue
        close = df["Close"]
        price = float(live_prices[symbol])
        prior_1d = float(close.iloc[-2]) if len(close) >= 2 else price
        prior_7d = float(close.iloc[-8]) if len(close) >= 8 else price
        prior_30d = float(close.iloc[-31]) if len(close) >= 31 else price
        sma_300 = float(close.tail(300).mean()) if len(close) >= 300 else float(close.mean())
        snapshot[symbol] = {
            "price": price,
            "chg_1d_pct": (price / prior_1d - 1) * 100 if prior_1d else 0.0,
            "chg_7d_pct": (price / prior_7d - 1) * 100 if prior_7d else 0.0,
            "chg_30d_pct": (price / prior_30d - 1) * 100 if prior_30d else 0.0,
            "vs_300d_sma_pct": (price / sma_300 - 1) * 100 if sma_300 else 0.0,
        }
    return snapshot


def _held_view(positions: dict, live_prices: dict, now: datetime.datetime) -> dict:
    held = {}
    for symbol, p in positions.items():
        price = float(live_prices.get(symbol, p["entry_price"]))
        entry_dt = datetime.datetime.fromisoformat(p["entry_time"])
        held[symbol] = {
            "entry_price": float(p["entry_price"]), "days_held": max(0, (now - entry_dt).days),
            "unrealized_pct": (price / float(p["entry_price"]) - 1) * 100 if p["entry_price"] else 0.0,
        }
    return held


def _check_stops(portfolio: dict, live_prices: dict, now: datetime.datetime) -> list:
    stopped = []
    for symbol in list(portfolio["positions"].keys()):
        pos = portfolio["positions"][symbol]
        price = live_prices.get(symbol)
        if price is None or float(price) > float(pos["stop_loss"]):
            continue
        qty = float(pos["quantity"])
        pnl = (float(pos["stop_loss"]) - float(pos["entry_price"])) * qty
        portfolio["cash"] += float(pos["stop_loss"]) * qty
        trade = {"symbol": symbol, "entry_date": pos["entry_date"], "entry_time": pos["entry_time"],
                 "exit_date": now.date().isoformat(), "exit_time": now.isoformat(timespec="minutes"),
                 "entry_price": pos["entry_price"], "exit_price": pos["stop_loss"], "quantity": qty,
                 "pnl": round(pnl, 4), "exit_reason": "stop_loss", "reason": "mechanical 18% stop, no LLM call"}
        append_trade(trade)
        del portfolio["positions"][symbol]
        stopped.append(trade)
    return stopped


def _apply_decisions(portfolio: dict, decisions: list, live_prices: dict, now: datetime.datetime) -> dict:
    sold, bought, held_notes = [], [], []
    for d in decisions:
        price = live_prices.get(d.symbol)
        is_held = d.symbol in portfolio["positions"]
        if is_held:
            if d.action == "SELL" and price is not None:
                pos = portfolio["positions"].pop(d.symbol)
                qty = float(pos["quantity"])
                pnl = (float(price) - float(pos["entry_price"])) * qty
                portfolio["cash"] += float(price) * qty
                trade = {"symbol": d.symbol, "entry_date": pos["entry_date"], "entry_time": pos["entry_time"],
                         "exit_date": now.date().isoformat(), "exit_time": now.isoformat(timespec="minutes"),
                         "entry_price": pos["entry_price"], "exit_price": price, "quantity": qty,
                         "pnl": round(pnl, 4), "exit_reason": "llm_sell", "reason": d.reason}
                append_trade(trade)
                sold.append(trade)
            else:
                held_notes.append({"symbol": d.symbol, "action": "HOLD", "reason": d.reason})
        else:
            if d.action == "BUY" and price is not None and price > 0:
                # size to MAX_SLEEVE_PCT of current cash (the risk-vs-stop formula collapses to this --
                # see RISK_PCT_PER_UNIT's own comment), naturally capped by what cash can actually buy
                qty = round(portfolio["cash"] * MAX_SLEEVE_PCT / price, 6)
                cost = round(qty * price, 4)
                if qty > 1e-6 and cost <= portfolio["cash"]:
                    portfolio["cash"] -= cost
                    portfolio["positions"][d.symbol] = {
                        "entry_price": price, "entry_date": now.date().isoformat(),
                        "entry_time": now.isoformat(timespec="minutes"), "quantity": qty,
                        "stop_loss": round(price * (1 - STOP_LOSS_PCT), 6), "reasoning": d.reason,
                        "conviction": d.conviction,
                    }
                    bought.append({"symbol": d.symbol, "price": price, "quantity": qty, "cost": cost, "reason": d.reason})
            else:
                held_notes.append({"symbol": d.symbol, "action": d.action, "reason": d.reason})
    return {"sold": sold, "bought": bought, "notes": held_notes}


def run_pool_g_cycle(daily_history: dict, fetch_live_prices_fn, api_key: str, now: Optional[datetime.datetime] = None,
                     call_fn=None) -> dict:
    """One full cycle: stops, then the LLM call, then execution. Pure
    over its inputs except for the state files it reads/writes and the
    LLM call -- `fetch_live_prices_fn` and `call_fn` are both injectable
    for tests. Returns a summary dict for the caller to print/log."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    portfolio = load_portfolio()
    live_prices = fetch_live_prices_fn(sorted(daily_history.keys()))

    stopped = _check_stops(portfolio, live_prices, now)
    snapshot = build_snapshot(daily_history, live_prices)
    held = _held_view(portfolio["positions"], live_prices, now)

    if not snapshot:
        save_portfolio(portfolio)
        return {"status": "no_price_data", "stopped": stopped, "sold": [], "bought": [], "notes": []}

    result = get_decisions(snapshot, held, api_key, call_fn=call_fn)
    execution = _apply_decisions(portfolio, result["decisions"], live_prices, now)
    portfolio["last_run_at"] = now.isoformat(timespec="minutes")
    save_portfolio(portfolio)

    append_decision_log({
        "at": now.isoformat(timespec="minutes"), "web_search_used": result["web_search_used"],
        "decisions": [{"symbol": d.symbol, "action": d.action, "conviction": d.conviction, "reason": d.reason}
                      for d in result["decisions"]],
        "stopped": [t["symbol"] for t in stopped], "sold": [t["symbol"] for t in execution["sold"]],
        "bought": [b["symbol"] for b in execution["bought"]],
    })
    return {"status": "processed", "web_search_used": result["web_search_used"], "stopped": stopped,
            **execution, "cash": round(portfolio["cash"], 4)}
