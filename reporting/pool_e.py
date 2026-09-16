"""
Pool E (crypto paper books, 2026-09-13) for the daily Telegram summary and
the dashboard -- every figure BOTH pre-tax and post-tax, per explicit
direction ("the details must show pre and post tax profits both").

The books live under deployment/state/pool_e/<strategy_key>/ in the
paper-trading engine's own layout (portfolio.json + trades.jsonl), kept in
USDT with fractional quantities. The engine books RAW price P&L into cash
(no fees, no tax) -- this module derives the rest from the trade records
with the SAME cost/tax model the research verdict used
(swing_research/crypto_costs.py):

  raw        = price P&L
  fees       = 0.30%/side + 10 bps/side spread on both legs
  pre-tax    = raw - fees
  tax        = 31.2% of each POSITIVE raw gain (no relief on losers)
  post-tax   = pre-tax - tax
  TDS        = 1% of every sale, refundable -- shown, never deducted

Open positions get the same treatment on their unbooked gain (fees on
both legs as if closed now). Rupee figures use the USD/INR rate passed in
(USDT ~ USD; the small Indian USDT premium is ignored).
"""

import glob
import os
from datetime import date
from typing import Optional

from swing_research.crypto_costs import INDIA_VDA_TAX_RATE, INDIA_VDA_TDS_RATE, CryptoCostModel

POOL_E_DIRNAME = "pool_e"
POOL_E_STARTING_CAPITAL_USDT = 1_000.0
POOL_E_NAMES = {"crypto_trend_timing": "Crypto Trend Timing (Faber 10-month SMA)",
                "crypto_tsmom": "Crypto Time-Series Momentum 12m (MOP 2012)",
                "crypto_trend_timing_weekly": "Crypto Weekly Trend Timing (Faber, weekly)",
                "crypto_trend_timing_daily": "Crypto Daily Trend Timing (Faber, daily)"}


def _read_json(path: str) -> Optional[dict]:
    from reporting.pool_summary import _read_json as rj
    return rj(path)


def _read_jsonl(path: str) -> list:
    from reporting.pool_summary import _read_jsonl as rjl
    return rjl(path)


def _ledger(raw: float, buy_value: float, sell_value: float, model: CryptoCostModel, tax_rate: float) -> dict:
    fees = model.round_trip_cost(buy_value, sell_value)
    tax = max(raw, 0.0) * tax_rate
    return {"raw": raw, "fees": fees, "pre_tax": raw - fees, "tax": tax, "post_tax": raw - fees - tax,
            "tds": sell_value * INDIA_VDA_TDS_RATE}


def _sum(ledgers: list) -> dict:
    keys = ("raw", "fees", "pre_tax", "tax", "post_tax", "tds")
    return {k: round(sum(l[k] for l in ledgers), 2) for k in keys}


def _book(book_dir: str, key: str, today: date, prices: dict, model: CryptoCostModel, tax_rate: float) -> dict:
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    positions = pf.get("positions") or {}
    trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
    open_rows, open_ledgers = [], []
    for symbol, p in positions.items():
        entry, qty = float(p["entry_price"]), float(p["quantity"])
        price = float(prices.get(symbol, entry))
        led = _ledger((price - entry) * qty, entry * qty, price * qty, model, tax_rate)
        open_ledgers.append(led)
        open_rows.append({"symbol": symbol, "quantity": qty, "entry_price": entry, "price": price,
                          "priced": symbol in prices, "entry_date": p.get("entry_date"),
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
    return {
        "key": key, "display_name": POOL_E_NAMES.get(key, key), "exists": bool(pf),
        "positions": len(positions), "deployed": deployed, "cash": cash,
        "starting_capital": float(pf.get("starting_capital", POOL_E_STARTING_CAPITAL_USDT) or 0),
        "capital": round(cash + deployed - booked["raw"], 2),        # what the book was given (raw, like every pool)
        "cash_after_tax": round(cash - booked["fees"] - booked["tax"], 2),
        "unbooked": unbooked, "booked": booked, "booked_today": booked_today,
        "open_positions": open_rows, "trades_total": len(trades),
        "recent_trades": list(reversed(trades[-15:])),
        "updated_today": pf.get("last_processed_date") == today.isoformat(),
        "last_processed_date": pf.get("last_processed_date"),
    }


def _inr(usdt: dict, rate: float) -> dict:
    return {k: round(v * rate, 2) for k, v in usdt.items()}


def build_pool_e(state_dir: str, crypto_prices: Optional[dict], usdinr: float, today: Optional[date] = None,
                 model: CryptoCostModel = CryptoCostModel(), tax_rate: float = INDIA_VDA_TAX_RATE) -> dict:
    today = today or date.today()
    prices = crypto_prices or {}
    books = [_book(d, os.path.basename(d.rstrip("/\\")), today, prices, model, tax_rate)
             for d in sorted(glob.glob(os.path.join(state_dir, POOL_E_DIRNAME, "*/")))]
    books = [b for b in books if b["exists"]]
    totals = {
        "positions": sum(b["positions"] for b in books),
        "deployed": round(sum(b["deployed"] for b in books), 2),
        "cash": round(sum(b["cash"] for b in books), 2),
        "capital": round(sum(b["capital"] for b in books), 2),
        "cash_after_tax": round(sum(b["cash_after_tax"] for b in books), 2),
        "unbooked": _sum([b["unbooked"] for b in books]) if books else _sum([]),
        "booked": _sum([b["booked"] for b in books]) if books else _sum([]),
        "booked_today": _sum([b["booked_today"] for b in books]) if books else _sum([]),
        "trades_total": sum(b["trades_total"] for b in books),
    }
    flat = {k: totals[k] for k in ("deployed", "cash", "capital", "cash_after_tax")}
    return {
        "exists": bool(books), "books": books, "usdt": totals, "usdinr": usdinr,
        "inr": {**_inr(flat, usdinr), "unbooked": _inr(totals["unbooked"], usdinr),
                "booked": _inr(totals["booked"], usdinr), "booked_today": _inr(totals["booked_today"], usdinr)},
        "updated_today": bool(books) and all(b["updated_today"] for b in books),
        "not_updated": [b["display_name"] for b in books if not b["updated_today"]],
        "tax_rate": tax_rate, "cost_model": model.describe(),
    }


def usdt(amount: float, signed: bool = False) -> str:
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    return f"{sign}{abs(amount):,.2f} USDT"


def format_pool_e_block(f: dict) -> list:
    """Telegram lines (legacy Markdown -- no underscores)."""
    from reporting.pool_summary import inr
    u, r, rate = f["usdt"], f["inr"], f["usdinr"]
    if not f["exists"]:
        return ["*Pool E (crypto)* -- no book yet", ""]
    ub, bk, td = u["unbooked"], u["booked"], u["booked_today"]
    return [
        f"*Pool E (crypto, USDT; Rs. at {rate:.1f}/USD)* -- {u['positions']} positions, {u['trades_total']} trades",
        f"Deployed {usdt(u['deployed'])} ({inr(r['deployed'])}) | Cash {usdt(u['cash'])} ({inr(r['cash'])})",
        f"Unbooked pre-tax {usdt(ub['pre_tax'], True)} / post-tax {usdt(ub['post_tax'], True)} "
        f"({inr(r['unbooked']['post_tax'], True)})",
        f"Booked raw {usdt(bk['raw'], True)} | fees {usdt(bk['fees'])} | tax {usdt(bk['tax'])}",
        f"Booked pre-tax {usdt(bk['pre_tax'], True)} / post-tax {usdt(bk['post_tax'], True)} "
        f"({inr(r['booked']['post_tax'], True)}; today post-tax {usdt(td['post_tax'], True)})",
        f"TDS withheld, refundable {usdt(bk['tds'])}",
        "",
    ]
