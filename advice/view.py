"""
The Advice tab: how your real Groww portfolio compares with your rules, how to invest this month's
deposit, what returns to aim for, and where your money could be in 1 to 10 years.

ADVICE ONLY. Nothing here places or changes an order. Projections are what-if arithmetic on assumed
returns (see advice/rules.py), not forecasts: markets can do much better or worse than any of the three
scenarios for years at a time.
"""

import math
from datetime import date
from typing import Callable, Optional

from advice.rules import FUND_BUCKET, load_rules

HORIZONS = (1, 2, 3, 5, 10)
STOCKS = "Individual stocks"


def _value(h: dict) -> float:
    return float(h["value"]) if h.get("value") is not None else float(h["invested"])


def bucket_of(symbol: str) -> str:
    return FUND_BUCKET.get(symbol, STOCKS)


def portfolio_shape(mine: dict, family_of: Callable[[str], str]) -> dict:
    """Holdings summed by bucket and by company (demerged units count with their parent)."""
    buckets: dict = {}
    families: dict = {}
    total = 0.0
    for h in mine["holdings"]:
        v = _value(h)
        total += v
        b = bucket_of(h["symbol"])
        buckets[b] = buckets.get(b, 0.0) + v
        if b == STOCKS:
            fam = family_of(h["symbol"])
            f = families.setdefault(fam, {"key": fam, "value": 0.0, "symbols": []})
            f["value"] += v
            f["symbols"].append(h["symbol"])
    return {"total": total, "buckets": buckets, "families": families}


def _status(weight: float, lo: Optional[float], hi: Optional[float]) -> str:
    if hi is not None and weight > hi:
        return "over"
    if lo is not None and weight < lo:
        return "under"
    return "ok"


def check_rules(shape: dict, mine: dict, rules: dict, segment_of: Callable[[str], str]) -> dict:
    """Bucket table plus a list of findings where the portfolio breaks a rule."""
    total = shape["total"] or 1.0
    pct = lambda v: v / total * 100
    rows, flags = [], []
    for name, lim in rules["buckets"].items():
        v = shape["buckets"].get(name, 0.0)
        w = pct(v)
        st = _status(w, lim.get("min"), lim.get("max"))
        rows.append({"bucket": name, "value": round(v, 2), "weight": round(w, 1), "min": lim.get("min"), "target": lim.get("target"),
                     "max": lim.get("max"), "status": st, "gap": round(lim["target"] / 100 * total - v, 2) if lim.get("target") is not None else None})
        if st == "over":
            flags.append({"severity": "over", "title": f"{name} is above its limit",
                          "detail": f"{w:.1f}% of the portfolio against a limit of {lim['max']}%. Send new money elsewhere until it is back under; no need to sell."})
        elif st == "under":
            flags.append({"severity": "under", "title": f"{name} is below its range",
                          "detail": f"{w:.1f}% against a minimum of {lim['min']}%. New deposits should go here first."})
    for name, g in rules.get("groups", {}).items():
        v = sum(shape["buckets"].get(m, 0.0) for m in g["members"])
        w = pct(v)
        rows.append({"bucket": name + " (combined)", "value": round(v, 2), "weight": round(w, 1), "min": None, "target": None, "max": g.get("max"),
                     "status": _status(w, None, g.get("max")), "gap": None, "group": True})
        if g.get("max") is not None and w > g["max"]:
            flags.append({"severity": "over", "title": f"{name} together is above its limit",
                          "detail": f"{w:.1f}% against a limit of {g['max']}%."})
    st_rules = rules["stocks"]
    fams = sorted(shape["families"].values(), key=lambda f: -f["value"])
    for f in fams:
        w = pct(f["value"])
        if w > st_rules["trim_above"]:
            flags.append({"severity": "over", "title": f"{f['key']} is a large single position",
                          "detail": f"{w:.1f}% of the portfolio (trim above {st_rules['trim_above']}%)."})
    small = [f for f in fams if f["value"] < st_rules["min_position"]]
    if small:
        flags.append({"severity": "info", "title": f"{len(small)} holdings are too small to matter",
                      "detail": "Under ₹{:,}: ".format(st_rules["min_position"]) + ", ".join(f"{f['key']} (₹{f['value']:,.0f})" for f in small) + ". Each is either worth building up or tidying away."})
    lo, hi = st_rules["count"]
    if len(fams) > hi:
        flags.append({"severity": "over", "title": "More companies than planned",
                      "detail": f"{len(fams)} separate companies against a plan of {lo}-{hi}. New stock money should wait until this comes down."})
    elif len(fams) < lo:
        flags.append({"severity": "under", "title": "Fewer companies than planned", "detail": f"{len(fams)} against a plan of {lo}-{hi}."})
    by_industry: dict = {}
    for f in fams:
        for sym in f["symbols"]:
            h = next(x for x in mine["holdings"] if x["symbol"] == sym)
            by_industry[segment_of(sym)] = by_industry.get(segment_of(sym), 0.0) + _value(h)
    for seg, v in sorted(by_industry.items(), key=lambda kv: -kv[1]):
        if pct(v) > st_rules["industry_max"]:
            flags.append({"severity": "over", "title": f"{seg} is a large share of the portfolio", "detail": f"{pct(v):.1f}% against a limit of {st_rules['industry_max']}% per industry."})
    for name, c in st_rules.get("clusters", {}).items():
        v = sum(_value(h) for h in mine["holdings"] if h["symbol"] in c["members"])
        if pct(v) > c["max"]:
            flags.append({"severity": "over", "title": f"{name} are bunched together",
                          "detail": f"{', '.join(c['members'])} are {pct(v):.1f}% of the portfolio and tend to move together; limit {c['max']}%."})
    order = {"over": 0, "under": 1, "info": 2}
    flags.sort(key=lambda x: order[x["severity"]])
    return {"buckets": rows, "flags": flags, "companies": len(fams), "industries": {k: round(pct(v), 1) for k, v in sorted(by_industry.items(), key=lambda kv: -kv[1])}}


def deposit_plan(shape: dict, mine: dict, deposit: float, rules: dict) -> dict:
    """Split a deposit across the buckets furthest below their target, then into orders with limit prices."""
    total_after = shape["total"] + deposit
    lims = rules["buckets"]
    caps = {b: (l["max"] / 100 * total_after if l.get("max") is not None else math.inf) for b, l in lims.items()}
    group_room = {}
    for g in rules.get("groups", {}).values():
        used = sum(shape["buckets"].get(m, 0.0) for m in g["members"])
        group_room[tuple(g["members"])] = max(0.0, g["max"] / 100 * total_after - used)
    room = {}
    for b, l in lims.items():
        if l.get("target") is None:
            continue
        have = shape["buckets"].get(b, 0.0)
        r = min(l["target"] / 100 * total_after - have, caps[b] - have)
        for members, gr in group_room.items():
            if b in members:
                r = min(r, gr)
        room[b] = max(0.0, r)
    alloc = {b: 0.0 for b in lims}
    need = sum(room.values())
    if deposit > 0 and need > 0:
        for b, r in room.items():
            alloc[b] = min(r, deposit * r / need) if need > deposit else r
    left = deposit - sum(alloc.values())
    for b in ("India index funds", "US tech and IT funds"):    # anything left over goes to the core funds, up to their maximum
        if left <= 0.5:
            break
        headroom = max(0.0, caps[b] - shape["buckets"].get(b, 0.0) - alloc[b])
        add = min(left, headroom)
        alloc[b] += add
        left -= add
    prices = {h["symbol"]: h.get("price") for h in mine["holdings"]}
    o = rules["orders"]
    rows, notes = [], []
    stock_money = alloc.get(STOCKS, 0.0)
    for b, amt in alloc.items():
        if b == STOCKS or amt <= 0:
            continue
        split = o["fund_split"].get(b)
        if not split:
            continue
        funds = {s: amt * w / sum(split.values()) for s, w in split.items()}
        big = max(funds, key=funds.get)
        for s in list(funds):                                   # merge orders below the minimum into the biggest one
            if s != big and funds[s] < o["min_order"]:
                funds[big] += funds.pop(s)
        rows.append({"bucket": b, "amount": round(amt, 2), "funds": funds})
    # a bucket whose whole share is under the minimum order is folded into the largest bucket
    if len(rows) > 1:
        rows.sort(key=lambda r: -r["amount"])
        keep = [rows[0]]
        for r in rows[1:]:
            if r["amount"] < o["min_order"]:
                notes.append(f"₹{r['amount']:,.0f} for {r['bucket']} is below the ₹{o['min_order']:,} minimum order, so it was added to {rows[0]['bucket']}.")
                for s, a in r["funds"].items():
                    top = keep[0]
                    tgt = max(top["funds"], key=top["funds"].get)
                    top["funds"][tgt] += a
                keep[0]["amount"] = round(keep[0]["amount"] + r["amount"], 2)
            else:
                keep.append(r)
        rows = keep
    out_rows = []
    for r in rows:
        funds = []
        for s, a in sorted(r["funds"].items(), key=lambda kv: -kv[1]):
            px = prices.get(s)
            if not px:
                funds.append({"symbol": s, "amount": round(a, 2), "price": None, "limit": None, "qty": None, "spend": None, "part_qty": []})
                continue
            limit = math.floor(px * (1 - o["limit_below_pct"] / 100) * 100) / 100
            qty = int(a // limit)
            n = max(1, o["parts"])
            base, extra = divmod(qty, n)
            parts = [base + (1 if i < extra else 0) for i in range(n)]
            funds.append({"symbol": s, "amount": round(a, 2), "price": round(px, 2), "limit": limit, "qty": qty, "spend": round(qty * limit, 2), "part_qty": parts})
        out_rows.append({"bucket": r["bucket"], "amount": r["amount"], "funds": funds})
    after = {b: round((shape["buckets"].get(b, 0.0) + sum(r["amount"] for r in out_rows if r["bucket"] == b)) / total_after * 100, 1) for b in lims}
    if stock_money > 0.5:
        notes.append(f"₹{stock_money:,.0f} of room exists for new individual stocks. Ideas for that come from the stock screener (a later step); until then it stays in cash or goes to the index funds.")
    elif shape["buckets"].get(STOCKS, 0.0) / shape["total"] * 100 > lims[STOCKS].get("max", 100):
        notes.append("Individual stocks are above their limit, so none of this deposit goes into stocks.")
    return {"deposit": round(deposit, 2), "rows": out_rows, "unallocated": round(max(0.0, left), 2), "stock_room": round(stock_money, 2), "after": after, "notes": notes}


def blended_returns(rules: dict) -> dict:
    """Yearly return per scenario for the target mix (the mix new money is steering toward)."""
    tgt = {b: l["target"] for b, l in rules["buckets"].items() if l.get("target") is not None}
    tot = sum(tgt.values())
    ar = rules["assumed_returns"]
    return {k: round(sum(tgt[b] / tot * ar[b][k] for b in tgt), 2) for k in ("low", "base", "high")}


def project(start: float, monthly: float, stepup_pct: float, annual_pct: float, years: int = 10) -> list:
    """Portfolio value at the end of each year 0..years: growth at annual_pct, a deposit at each month end,
    the monthly amount rising by stepup_pct at the start of every year."""
    mr = (1 + annual_pct / 100) ** (1 / 12) - 1
    v, out = start, [start]
    for y in range(years):
        m = monthly * (1 + stepup_pct / 100) ** y
        for _ in range(12):
            v = v * (1 + mr) + m
        out.append(v)
    return out


def invest_history(reports: Optional[dict], today: date) -> dict:
    """Month-by-month net money added (deposits minus withdrawals) and what to plan on going forward."""
    months = [m for m in (reports or {}).get("monthly", []) if m.get("net") is not None and m["month"] < today.strftime("%Y-%m")]
    last = months[-12:]
    avg = lambda xs: (sum(m["net"] for m in xs) / len(xs)) if xs else 0.0
    a3, a6, a12 = avg(last[-3:]), avg(last[-6:]), avg(last)
    default = max(0, round(a6 / 5000) * 5000)
    trend = "steady"
    if a6 and a3 > a6 * 1.15:
        trend = "rising"
    elif a6 and a3 < a6 * 0.85:
        trend = "falling"
    return {"months": [{"month": m["month"], "net": m["net"], "deposited": m["deposited"], "withdrawn": m["withdrawn"]} for m in last],
            "avg3": round(a3), "avg6": round(a6), "avg12": round(a12), "default_monthly": default, "trend": trend}


def build_advice(mine: Optional[dict], reports: Optional[dict], today: date, params: Optional[dict],
                 family_of: Callable[[str], str], segment_of: Callable[[str], str], state_dir: Optional[str] = None) -> Optional[dict]:
    if not mine or not mine.get("holdings"):
        return None
    rules = load_rules(state_dir)
    params = params or {}
    shape = portfolio_shape(mine, family_of)
    hist = invest_history(reports, today)
    monthly = float(params["monthly"]) if params.get("monthly") is not None else float(hist["default_monthly"])
    stepup = float(params["stepup"]) if params.get("stepup") is not None else 0.0
    deposit = float(params["deposit"]) if params.get("deposit") is not None else monthly
    returns = blended_returns(rules)
    for k in ("low", "base", "high"):
        if params.get(k) is not None:
            returns[k] = float(params[k])
    start = shape["total"]
    series = {k: project(start, monthly, stepup, returns[k]) for k in ("low", "base", "high")}
    contrib = project(start, monthly, stepup, 0.0)
    infl = rules["inflation"]
    horizons = [{"years": n, "invested": round(contrib[n]), "low": round(series["low"][n]), "base": round(series["base"][n]), "high": round(series["high"][n]),
                 "gain_base": round(series["base"][n] - contrib[n]), "base_real": round(series["base"][n] / (1 + infl / 100) ** n)} for n in HORIZONS]
    base1 = series["base"][1] - contrib[1]
    head = (reports or {}).get("headline") or {}
    actual_years = [{"label": y["label"], "return_pct": y["return_pct"], "bench_pct": y["bench_return_pct"]} for y in (reports or {}).get("years", []) if y["label"] >= "2023"]
    return {
        "rules": {"buckets": rules["buckets"], "groups": rules["groups"], "stocks": rules["stocks"], "orders": {k: v for k, v in rules["orders"].items() if k != "fund_split"}},
        "params": {"monthly": round(monthly), "stepup": stepup, "deposit": round(deposit), "returns": returns, "inflation": infl},
        "check": check_rules(shape, mine, rules, segment_of),
        "deposit_plan": deposit_plan(shape, mine, deposit, rules),
        "history": hist,
        "targets": {"yearly": returns, "quarterly": {k: round(((1 + v / 100) ** 0.25 - 1) * 100, 2) for k, v in returns.items()},
                    "next_year_gain_base": round(base1), "total": round(start), "actual_xirr": head.get("xirr_pct"), "bench_xirr": head.get("bench_xirr_pct"),
                    "actual_years": actual_years},
        "projection": {"start": round(start), "horizons": horizons,
                       "series": {"invested": [round(x) for x in contrib], **{k: [round(x) for x in v] for k, v in series.items()}}},
    }
