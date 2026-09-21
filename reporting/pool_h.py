"""
Pool H: your real long-term portfolio at Groww, presented like any other pool on the dashboard (Team card,
Live day rows, Strategies row, P&L statement line) in Live mode only.

Unlike the bot pools it is not run by a strategy and has no state files of its own. It is built on the fly from:
  * the live Groww holdings (quantity, average cost) and cash,
  * the order history downloaded from Groww (dates, quantities, values), for the trades and the start date,
  * Groww's own yearly reports (realised profit and the charges actually paid).
So new buys show up as holdings straight away, but appear as dated trades only after the order history is
re-downloaded. Sold trades are matched first-in first-out, the way the broker reports them.
Everything is gross of tax unless stated; the statement line uses the real charges paid plus an estimated tax
as if everything were sold today (long-term above Rs 1.25 lakh at 12.5%, short-term at 20%).
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

POOL = "H"
LABEL = "Pool H"
BOOK = "Long-term holdings"
INFO = {"pool": "H", "name": "Long-term (real money)",
        "text": "Your real long-term portfolio at Groww: index funds, gold, silver and stocks you buy and hold for years. You place the orders yourself; the Advice tab tells you what to do."}
HOW = {"entry": "You decide and place the buys yourself, usually every month from money you add, guided by the Advice tab.",
       "exit": "Sold rarely: when a stock's business weakens or a limit is hit, following the Advice tab.",
       "risk": "Set by your own rules: caps on gold and silver, on individual stocks and on any one company.",
       "source": "Real money at Groww, read only. Nothing here places an order."}


def fifo(orders: list) -> Tuple[List[dict], Dict[str, list]]:
    """Closed sells (one per day and symbol, matched to the oldest buys) and the lots still open.
    orders: [date, symbol, "B"/"S", qty, value]. Buys sort before sells on the same day."""
    lots: Dict[str, list] = {}
    closed: Dict[tuple, dict] = {}
    for d, sym, side, qty, value in sorted(orders, key=lambda o: (o[0], 0 if o[2] == "B" else 1)):
        qty, value = float(qty), float(value)
        if side == "B":
            lots.setdefault(sym, []).append({"date": d, "qty": qty, "cost": value / qty if qty else 0.0})
            continue
        rec = closed.setdefault((d, sym), {"date": d, "symbol": sym, "qty": 0.0, "value": 0.0, "cost": 0.0, "matched": 0.0, "oldest": None})
        rec["qty"] += qty
        rec["value"] += value
        left, book = qty, lots.get(sym, [])
        while left > 1e-9 and book:
            take = min(left, book[0]["qty"])
            rec["cost"] += take * book[0]["cost"]
            rec["matched"] += take
            rec["oldest"] = min(rec["oldest"] or book[0]["date"], book[0]["date"])
            book[0]["qty"] -= take
            left -= take
            if book[0]["qty"] <= 1e-9:
                book.pop(0)
    return sorted(closed.values(), key=lambda r: (r["date"], r["symbol"])), lots


def _realised_all(fy_rows: list) -> float:
    return sum(float(r.get("intraday", 0)) + float(r.get("short_term", 0)) + float(r.get("long_term", 0)) for r in fy_rows)


def _tax_estimate(tax: Optional[dict]) -> float:
    """Tax as if everything were sold today: short-term at 20%, long-term at 12.5% above the yearly Rs 1.25 lakh."""
    if not tax:
        return 0.0
    u = tax["unrealised"]
    lt = u["lt_gain"] + u["lt_loss"] + tax["realised_long"]
    st = u["st_gain"] + u["st_loss"] + tax["realised_short"]
    if st < 0:
        lt += st
    if lt < 0:
        st += lt
    return max(0.0, st) * tax["rates"]["st"] / 100 + max(0.0, lt - tax["rates"]["exempt"]) * tax["rates"]["lt"] / 100


def build(mine: dict, raw: dict, cash: Optional[float], today: date, tax: Optional[dict]) -> Optional[dict]:
    if not mine or not mine.get("holdings") or not raw or not raw.get("orders"):
        return None
    closed, lots = fifo(raw["orders"])
    last_order = max(o[0] for o in raw["orders"])
    holdings = mine["holdings"]
    invested = sum(h["invested"] for h in holdings)
    priced = [h for h in holdings if h.get("pnl") is not None]
    unrealised = sum(h["pnl"] for h in priced)
    realised = _realised_all(raw.get("fy", []))
    cash = float(cash or 0.0)
    fy = raw.get("fy", [])
    tot = lambda k: sum(float(r.get(k, 0)) for r in fy)
    gst, charges_all = tot("gst"), tot("charges")
    brokerage, stt, dp = tot("brokerage"), tot("stt"), tot("dp")
    exchange, sebi, stamp = tot("exchange"), tot("sebi"), tot("stamp")
    other = max(0.0, charges_all - gst - brokerage - stt - dp - exchange - sebi - stamp)      # IPFT and any small item not listed separately
    detail = {"brokerage": brokerage, "exchange": exchange + other, "sebi": sebi, "dp": dp, "stt": stt, "stamp": stamp, "gst": gst}
    charges = charges_all - gst
    capital = cash + invested - realised            # the same definition the other pools use: capital = cash + deployed - realised

    # ---- ledger rows: one per holding still open, and one per sale
    rows = []
    for h in holdings:
        sym = h["symbol"]
        book = lots.get(sym, [])
        first_buy = min((l["date"] for l in book), default=None)
        buys = [o[0] for o in raw["orders"] if o[1] == sym and o[2] == "B"]
        last_buy = max(buys, default=last_order)      # shares that arrived without an order (a demerger) sit with the newest history
        rows.append({"date": last_buy, "time": "", "action": "BUY", "symbol": sym, "qty": h["quantity"], "price": h.get("price") or h["avg_price"], "pool": LABEL, "book": BOOK,
                     "status": "Open", "fill_today": False, "pnl": h.get("pnl"), "bought_on": first_buy,
                     "held_days": (today - date.fromisoformat(first_buy)).days if first_buy else None, "entry_price": h["avg_price"], "note": "", "amount": round(h["invested"], 2),
                     "kind": "Long-term", "life": None, "partial": False, "pct": h.get("pct"), "pnl_today": h.get("today"), "symbol_key": sym + ".NS", "book_key": "long_term", "direction": "BUY"})
    for c in closed:
        if c["matched"] <= 0:
            continue
        frac = c["matched"] / c["qty"]
        pnl = c["value"] * frac - c["cost"]
        entry = c["cost"] / c["matched"]
        rows.append({"date": c["date"], "time": "", "action": "SELL", "symbol": c["symbol"], "qty": round(c["qty"], 4), "price": round(c["value"] / c["qty"], 2), "pool": LABEL, "book": BOOK,
                     "status": "Closed", "fill_today": False, "pnl": round(pnl, 2), "bought_on": c["oldest"], "held_days": (date.fromisoformat(c["date"]) - date.fromisoformat(c["oldest"])).days,
                     "entry_price": round(entry, 2), "note": "" if frac > 0.999 else "history incomplete (free or changed shares)", "amount": round(c["value"], 2),
                     "kind": "Long-term", "life": None, "partial": False, "pct": round((c["value"] * frac / c["cost"] - 1) * 100, 2) if c["cost"] else None, "pnl_today": None,
                     "symbol_key": c["symbol"] + ".NS", "book_key": "long_term", "direction": "SELL"})
    closed_pnl = [r["pnl"] for r in rows if r["status"] == "Closed"]
    first_order = min(o[0] for o in raw["orders"])

    pool = {"positions": len(holdings), "deployed": round(invested, 2), "cash": round(cash, 2), "unrealised": round(unrealised, 2), "realised": round(realised, 2),
            "realised_today": 0.0, "books": 1, "updated_today": 1, "not_updated": [], "capital": round(capital, 2)}
    strategy = {"key": "long_term", "sid": "LT-001", "name": BOOK, "pool": LABEL, "type": "Long-term", "verdict": "-", "status": "PRODUCTION", "experiment": "",
                "brief": INFO["text"], "capital": round(capital, 2), "pnl": round(unrealised + realised, 2), "started": first_order, "closed_trades": len(closed_pnl),
                "wins": sum(1 for x in closed_pnl if x > 0),
                "pools_breakdown": [{"pool": LABEL, "capital": round(capital, 2), "pnl": round(unrealised + realised, 2), "closed_trades": len(closed_pnl),
                                     "wins": sum(1 for x in closed_pnl if x > 0), "started": first_order}],
                "how": HOW, "research": {}, "variants": []}
    line = {"pool": LABEL, "key": "long_term", "sid": "LT-001", "name": BOOK, "type": "Long-term", "capital": round(capital, 2), "cash": round(cash, 2), "deployed": round(invested, 2),
            "realised": round(realised, 2), "unrealised": round(unrealised, 2), "charges": round(charges, 2), "gst": round(gst, 2), "tax": round(_tax_estimate(tax), 2), "tds": None,
            "tax_rate": 0.125, "detail": {k: round(v, 2) for k, v in detail.items()}, "taxable": True}
    return {"pool": pool, "info": INFO, "strategy": strategy, "line": line, "ledger": rows}


def add_pool_h(state: dict, mine: dict, raw: Optional[dict], cash: Optional[float], today: date, tax: Optional[dict]) -> bool:
    """Add Pool H to a dashboard state (Live mode). Returns False when there is nothing to add."""
    h = build(mine, raw, cash, today, tax)
    if not h:
        return False
    p = h["pool"]
    state["pools"] = {**state["pools"], POOL: p}
    o = dict(state["overall"])
    for k in ("deployed", "cash", "unrealised", "realised", "capital"):
        o[k] = round(float(o.get(k, 0) or 0) + p[k], 2)
    o["positions"] = int(o.get("positions", 0) or 0) + p["positions"]
    state["overall"] = o
    state["pools_info"] = list(state["pools_info"]) + [h["info"]]
    state["strategies"] = list(state["strategies"]) + [h["strategy"]]
    state["statement"] = list(state["statement"]) + [h["line"]]
    state["ledger"] = sorted(list(state["ledger"]) + h["ledger"], key=lambda a: (a["date"], a.get("time") or ""), reverse=True)
    return True
