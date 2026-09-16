"""
Pool G (crypto AI judgment book) for the daily Telegram summary and the
dashboard -- pre-tax and post-tax, the same convention as reporting/pool_e.py
and swing_research/crypto_costs.py's cost/tax model, applied here to a
LIVE book with no backtest behind it (see portfolio_g/state.py).

One shared book across the five majors (unlike Pool E's one-book-per-
strategy layout), so this module is simpler: a single portfolio.json and
trades.jsonl under deployment/state/pool_g/.
"""

import glob
import os
from datetime import date
from typing import Optional

from portfolio_g.state import PORTFOLIO_G_STARTING_CAPITAL_USDT
from reporting.pool_summary import _read_json, _read_jsonl
from swing_research.crypto_costs import INDIA_VDA_TAX_RATE, INDIA_VDA_TDS_RATE, CryptoCostModel

POOL_G_DIRNAME = "pool_g"


def _ledger(raw: float, buy_value: float, sell_value: float, model: CryptoCostModel, tax_rate: float) -> dict:
    fees = model.round_trip_cost(buy_value, sell_value)
    tax = max(raw, 0.0) * tax_rate
    return {"raw": raw, "fees": fees, "pre_tax": raw - fees, "tax": tax, "post_tax": raw - fees - tax,
            "tds": sell_value * INDIA_VDA_TDS_RATE}


def _sum(ledgers: list) -> dict:
    keys = ("raw", "fees", "pre_tax", "tax", "post_tax", "tds")
    return {k: round(sum(l[k] for l in ledgers), 2) for k in keys}


def build_pool_g(state_dir: str, crypto_prices: Optional[dict], usdinr: float, today: Optional[date] = None,
                 model: CryptoCostModel = CryptoCostModel(), tax_rate: float = INDIA_VDA_TAX_RATE) -> dict:
    today = today or date.today()
    prices = crypto_prices or {}
    book_dir = os.path.join(state_dir, POOL_G_DIRNAME)
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    positions = pf.get("positions") or {}
    trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
    decision_log = _read_jsonl(os.path.join(book_dir, "decision_log.jsonl"))

    open_rows, open_ledgers = [], []
    for symbol, p in positions.items():
        entry, qty = float(p["entry_price"]), float(p["quantity"])
        price = float(prices.get(symbol, entry))
        led = _ledger((price - entry) * qty, entry * qty, price * qty, model, tax_rate)
        open_ledgers.append(led)
        open_rows.append({"symbol": symbol, "quantity": qty, "entry_price": entry, "price": price,
                          "priced": symbol in prices, "entry_date": p.get("entry_date"), "reasoning": p.get("reasoning", ""),
                          "stop_loss": float(p.get("stop_loss", 0) or 0), "value": round(price * qty, 2),
                          "pct": round((price / entry - 1) * 100, 2) if entry else 0.0,
                          "unbooked_raw": round(led["raw"], 2), "unbooked_pre_tax": round(led["pre_tax"], 2),
                          "unbooked_post_tax": round(led["post_tax"], 2)})

    closed_ledgers, today_ledgers = [], []
    for t in trades:
        entry, exit_, qty = float(t.get("entry_price", 0) or 0), float(t.get("exit_price", 0) or 0), float(t.get("quantity", 0) or 0)
        led = _ledger(float(t.get("pnl", 0) or 0), entry * qty, exit_ * qty, model, tax_rate)
        closed_ledgers.append(led)
        if t.get("exit_date") == today.isoformat():
            today_ledgers.append(led)

    unbooked, booked, booked_today = _sum(open_ledgers), _sum(closed_ledgers), _sum(today_ledgers)
    deployed = round(sum(float(p["entry_price"]) * float(p["quantity"]) for p in positions.values()), 2)
    cash = round(float(pf.get("cash", 0) or 0), 2)
    last_decisions = decision_log[-1] if decision_log else None
    return {
        "exists": bool(pf), "positions": len(positions), "deployed": deployed, "cash": cash,
        "starting_capital": float(pf.get("starting_capital", PORTFOLIO_G_STARTING_CAPITAL_USDT) or 0),
        "capital": round(cash + deployed - booked["raw"], 2),
        "cash_after_tax": round(cash - booked["fees"] - booked["tax"], 2),
        "unbooked": unbooked, "booked": booked, "booked_today": booked_today,
        "open_positions": open_rows, "trades_total": len(trades),
        "recent_trades": list(reversed(trades[-15:])),
        "last_run_at": pf.get("last_run_at"),
        "runs_today": sum(1 for d in decision_log if str(d.get("at", "")).startswith(today.isoformat())),
        "last_decisions": (last_decisions or {}).get("decisions", []),
        "last_web_search_used": (last_decisions or {}).get("web_search_used"),
        "usdinr": usdinr, "tax_rate": tax_rate, "cost_model": model.describe(),
    }
