"""
The tax planner for your real Groww holdings: which lots are long-term, where the gains and losses sit, how much
of this year's tax-free long-term gain limit is still unused, and what could be done before 31 March.

Assumptions (India, shares and equity funds, from FY 2024-25):
  * long-term = held more than 12 months, taxed at 12.5% on gains above Rs 1.25 lakh a year
  * short-term = held 12 months or less, taxed at 20%
  * losses can be set against gains of the same or later kind, and carried forward for 8 years if booked and reported
  * lots are matched first-in first-out, the way the broker reports them
Surcharge and cess are ignored, and this is a planning aid, not tax advice: check with your CA before acting.

A symbol is analysed only when the orders explain exactly the shares held now; shares that arrived free
(demergers) or changed by a split or bonus are left out and listed as "not analysed".
Suggested actions appear as tasks only from January to March, when they can still count for the year.
"""

from datetime import date, timedelta
from typing import Dict, List, Optional

from advice.rules import FUND_BUCKET

LTCG_RATE = 12.5
STCG_RATE = 20.0
LTCG_EXEMPT = 125000.0
LOSS_MIN = 10000.0          # only losses at least this big are worth a round trip
SEASON_MONTHS = (1, 2, 3)


def fy_label(today: date) -> str:
    y = today.year if today.month >= 4 else today.year - 1
    return f"FY{str(y)[2:]}-{str(y + 1)[2:]}"


def open_lots(orders: list, holdings_qty: Dict[str, float]) -> Dict[str, list]:
    """First-in first-out open lots per symbol: [{date, qty, cost}] (cost per share). Symbols whose remaining
    quantity does not match the holding are omitted."""
    lots: Dict[str, list] = {}
    for d, sym, side, qty, value in sorted(orders, key=lambda o: o[0]):
        book = lots.setdefault(sym, [])
        if side == "B":
            book.append({"date": d, "qty": float(qty), "cost": float(value) / float(qty) if qty else 0.0})
        else:
            left = float(qty)
            while left > 1e-9 and book:
                take = min(left, book[0]["qty"])
                book[0]["qty"] -= take
                left -= take
                if book[0]["qty"] <= 1e-9:
                    book.pop(0)
    return {s: b for s, b in lots.items() if s in holdings_qty and abs(sum(x["qty"] for x in b) - holdings_qty[s]) < 0.5 and b}


def _realised(fy_rows: list, today: date) -> dict:
    row = next((r for r in fy_rows if r["fy"] == fy_label(today)), None) or {}
    return {"short": float(row.get("short_term", 0)) + float(row.get("intraday", 0)), "long": float(row.get("long_term", 0)), "fy": fy_label(today)}


def tax_view(raw: Optional[dict], mine: dict, today: date, name_of=lambda s: s) -> Optional[dict]:
    if not raw or not raw.get("orders") or not mine.get("holdings"):
        return None
    hold = {h["symbol"]: h for h in mine["holdings"]}
    lots = open_lots(raw["orders"], {s: float(h["quantity"]) for s, h in hold.items() if h.get("quantity") is not None})
    realised = _realised(raw.get("fy", []), today)
    rows, lt_gain, lt_loss, st_gain, st_loss = [], 0.0, 0.0, 0.0, 0.0
    for sym, book in lots.items():
        price = hold[sym].get("price")
        if not price:
            continue
        r = {"symbol": sym, "name": name_of(sym), "price": price, "lt_gain": 0.0, "lt_loss": 0.0, "st_gain": 0.0, "st_loss": 0.0, "lt_qty": 0.0, "near_lt": []}
        for lot in book:
            gain = lot["qty"] * (price - lot["cost"])
            bought = date.fromisoformat(lot["date"])
            if (today - bought).days > 365:
                r["lt_qty"] += lot["qty"]
                r["lt_gain" if gain >= 0 else "lt_loss"] += gain
            else:
                r["st_gain" if gain >= 0 else "st_loss"] += gain
                if gain > 0 and (bought + timedelta(days=366) - today).days <= 60:
                    r["near_lt"].append({"date": (bought + timedelta(days=366)).isoformat(), "gain": round(gain, 2), "qty": lot["qty"]})
        lt_gain += r["lt_gain"]; lt_loss += r["lt_loss"]; st_gain += r["st_gain"]; st_loss += r["st_loss"]
        rows.append({**{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}, "oldest": book[0]["date"]})
    rows.sort(key=lambda r: -(r["lt_gain"] + r["st_gain"] + r["lt_loss"] + r["st_loss"]))
    left = max(0.0, LTCG_EXEMPT - max(0.0, realised["long"]))

    # gain harvesting: sell long-term lots with gains up to the unused exemption, then buy them back
    harvest, room = [], left
    for sym in sorted((r["symbol"] for r in rows if r["lt_gain"] > 0), key=lambda s: (s not in FUND_BUCKET, -next(x["lt_gain"] for x in rows if x["symbol"] == s))):
        if room < 1000:
            break
        price, gained, sold_qty = hold[sym]["price"], 0.0, 0
        for lot in lots[sym]:
            if (today - date.fromisoformat(lot["date"])).days <= 365 or price <= lot["cost"]:
                continue
            per = price - lot["cost"]
            q = int(min(lot["qty"], (room - gained) // per))
            if q > 0:
                gained += q * per
                sold_qty += q
        if sold_qty:
            harvest.append({"symbol": sym, "name": name_of(sym), "qty": sold_qty, "gain": round(gained, 2), "price": price, "saves": round(gained * LTCG_RATE / 100, 2), "fund": sym in FUND_BUCKET})
            room -= gained
    losses = sorted(({"symbol": r["symbol"], "name": r["name"], "loss": round(r["lt_loss"] + r["st_loss"], 2), "term": "long-term" if abs(r["lt_loss"]) >= abs(r["st_loss"]) else "short-term",
                      "qty": float(hold[r["symbol"]]["quantity"]), "price": r["price"]}
                     for r in rows if r["lt_loss"] + r["st_loss"] <= -LOSS_MIN), key=lambda x: x["loss"])
    return {"fy": realised["fy"], "realised_short": round(realised["short"], 2), "realised_long": round(realised["long"], 2),
            "unrealised": {"lt_gain": round(lt_gain, 2), "lt_loss": round(lt_loss, 2), "st_gain": round(st_gain, 2), "st_loss": round(st_loss, 2)},
            "exemption_left": round(left, 2), "rows": rows, "harvest": harvest, "losses": losses,
            "in_season": today.month in SEASON_MONTHS, "days_to_year_end": (date(today.year if today.month <= 3 else today.year + 1, 3, 31) - today).days,
            "not_analysed": sorted(s for s in hold if s not in lots),
            "rates": {"lt": LTCG_RATE, "st": STCG_RATE, "exempt": LTCG_EXEMPT}}


def tax_tasks(tax: Optional[dict], done: list, when_iso: str, skip_keys: set) -> List[dict]:
    """Optional year-end actions, only from January to March. `skip_keys` are symbols that already have a sell task."""
    if not tax or not tax["in_season"]:
        return []
    ids = {r["id"] for r in done}
    tasks = []
    for h in tax["harvest"][:6]:
        tid = f"taxgain:{h['symbol']}:{tax['fy']}"
        if tid in ids or h["symbol"] in skip_keys:
            continue
        tasks.append({"id": tid, "key": h["symbol"], "name": h["name"], "symbols": [h["symbol"]], "kind": "Tax", "order": 4, "due": when_iso, "price": h["price"],
                      "title": f"Sell {h['qty']} units and buy the same {h['qty']} units back the same day: books about ₹{h['gain']:,.0f} of long-term gain inside this year's tax-free limit.",
                      "detail": f"Saves about ₹{h['saves']:,.0f} of future tax (12.5% on that gain), for the small cost of the round trip. Optional.",
                      "why": ["Long-term gains up to ₹1.25 lakh a year are tax-free; selling and re-buying resets your cost to today's price, so that gain is never taxed later."]})
    for l in tax["losses"][:3]:
        tid = f"taxloss:{l['symbol']}:{tax['fy']}"
        if tid in ids or l["symbol"] in skip_keys:
            continue
        tasks.append({"id": tid, "key": l["symbol"], "name": l["name"], "symbols": [l["symbol"]], "kind": "Tax", "order": 4, "due": when_iso, "price": l["price"],
                      "title": f"Sell all {l['qty']:g} shares and buy them back the same day: books a {l['term']} loss of about ₹{-l['loss']:,.0f}.",
                      "detail": "The loss can be set against gains now or carried forward for 8 years (it must be reported). Optional; skip if you do not expect taxable gains. Cancel any stop-loss or GTT on it first and place it again after buying back.",
                      "why": ["Buying back the same day keeps you invested while the loss is booked for tax."]})
    return tasks
