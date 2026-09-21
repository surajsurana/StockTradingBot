"""
Turn the advice into things YOU do: the concrete orders to place in Groww on the next trading day, and nothing else.

The system watches everything silently. It only speaks when there is an order to place, such as
  * a limit buy for money that has arrived in your account,
  * a limit sell,
  * a stop-loss sell order placed as a GTT ("good till triggered": a standing order that stays active for months and
    fires by itself when the price reaches the trigger, whether that is a fall (stop-loss) or a rise (a higher sell
    price), so nobody has to keep watching that price),
  * a sell at market once a limit sell has had its chance and did not fill.
ADVICE ONLY: nothing here places an order.

Completed tasks are remembered in deployment/state/advice_done.json (a list of {id, ts}); the dashboard's
"Done" button writes it. For buys the record also drives the two-part rule: the first half goes in, and the
second half is offered 14 days later.
"""

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

DONE_FILE = "advice_done.json"
ID_RE = re.compile(r"^[A-Za-z0-9:._-]{1,80}$")
BUY_WINDOW_DAYS = 30        # a buy cycle: first part, then the rest after WAIT days, then quiet until the window ends
BUY_WAIT_DAYS = 14


def next_trading_day(today: date) -> date:
    """The next weekday after `today`. Exchange holidays are not known here, so a holiday may show a day early."""
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def load_done(state_dir: Optional[str]) -> List[dict]:
    if not state_dir:
        return []
    path = os.path.join(state_dir, DONE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [r for r in data if isinstance(r, dict) and ID_RE.match(str(r.get("id", ""))) and r.get("ts")]
    except (OSError, ValueError):
        return []


def mark_done(state_dir: str, task_id: str, now: Optional[datetime] = None) -> None:
    if not ID_RE.match(task_id or ""):
        raise ValueError("bad task id")
    records = load_done(state_dir)
    records.append({"id": task_id, "ts": (now or datetime.now()).isoformat(timespec="seconds")})
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, DONE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records[-500:], f)
    os.replace(tmp, os.path.join(state_dir, DONE_FILE))


def _ages(done: list, task_id: str, today: date) -> List[int]:
    return sorted((today - date.fromisoformat(r["ts"][:10])).days for r in done if r["id"] == task_id)


def _is_done(done: list, task_id: str) -> bool:
    return any(r["id"] == task_id for r in done)


def _day_short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def _day_label(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b')}"


def build_tasks(items: list, plan: dict, today: date, done: list) -> dict:
    """`items` are the per-company opinions from advice.view.build_items. Returns the tasks for the next trading day,
    plus the stocks the system is still watching for you (shown quietly, never as a task)."""
    when = next_trading_day(today)
    plan_funds = {f["symbol"]: f for r in plan["rows"] for f in r["funds"] if f.get("qty")}
    tasks, watching = [], []
    for it in items:
        key, sym, qty, price = it["key"], it["symbols"][0], it.get("qty") or 0.0, it.get("price")
        single = len(it["symbols"]) == 1 and qty
        base = {"key": key, "name": it["name"], "symbols": it["symbols"], "due": when.isoformat(), "why": it["why"], "price": price}
        if it.get("results_action") and it.get("rule_qty") and it.get("result"):
            res, rq, rsym = it["result"], it["rule_qty"], it["rule_symbol"]
            tid = f"res:{key}:{res['period']}"
            if not _is_done(done, tid):
                q = rq if it["results_action"] == "sell" else int(rq // 2)
                what = f"Sell all {rq:g} shares of {rsym}" if it["results_action"] == "sell" else f"Sell {q} of your {rq:g} {rsym} shares (half)"
                tasks.append({**base, "id": tid, "kind": "Sell", "order": 0,
                              "title": f"{what}: {res['label']} did not improve ({res['baseline']}% to {res['new']}%) in the quarter ending {_day_short(res['period'])}.",
                              "detail": "Place a limit sell order at or just below the live price."})
            continue
        if it["action"] == "Buy" and sym in plan_funds:
            f = plan_funds[sym]
            tid = f"buy:{sym}"
            ages = _ages(done, tid, today)
            recent = [a for a in ages if a < BUY_WINDOW_DAYS]
            if not recent:
                q = f["part_qty"][0] if f.get("part_qty") else f["qty"]
                part = "first part" if len(f.get("part_qty", [])) > 1 else "order"
            elif len(recent) == 1 and recent[0] >= BUY_WAIT_DAYS:
                q, part = f["qty"], "second part"
            else:
                continue
            if q <= 0:
                continue
            tasks.append({**base, "id": tid, "kind": "Buy", "order": 3,
                          "title": f"Place a limit buy order ({part}): {q} units at ₹{f['limit']:,.2f} or lower.",
                          "detail": f"About ₹{q * f['limit']:,.0f}. Last price ₹{f['price']:,.2f}."})
            continue
        if it["action"] == "Sell" and it.get("sell_limit") and single:
            lim = it["sell_limit"]
            tid = f"sell:{key}:{lim}"
            review = date.fromisoformat(it["review"]) if it.get("review") else None
            if _is_done(done, f"sellmkt:{key}"):
                continue
            if review and today >= review:
                tasks.append({**base, "id": f"sellmkt:{key}", "kind": "Sell", "order": 0,
                              "title": f"Sell all {qty:g} shares now at the market price: the limit order had until {review.day} {review.strftime('%b')} and has not filled.",
                              "detail": f"Last price ₹{price:,.1f}." if price else ""})
            elif not _is_done(done, tid):
                until = f" Keep it open until {review.day} {review.strftime('%b')}." if review else ""
                tasks.append({**base, "id": tid, "kind": "Sell", "order": 1,
                              "title": f"Place a sell order at a higher price (as a GTT): sell {qty:g} shares when the price rises to ₹{lim:,.1f}.{until}",
                              "detail": f"Today's price ₹{price:,.1f}; the price is a little above it so a bounce can fill it. A GTT stays active for months; a normal limit order lapses at the end of the day." if price else ""})
            continue
        if it["action"] == "Trim" and single and price:
            tid = f"trim:{key}:{int(round(it.get('trim_value', 0), -3))}"
            if not _is_done(done, tid):
                q = int(it["trim_value"] // price) if it.get("trim_value") else 0
                if q > 0:
                    tasks.append({**base, "id": tid, "kind": "Sell", "order": 2,
                                  "title": f"Sell {q} of your {qty:g} shares (about ₹{q * price:,.0f}) with a limit order at ₹{price:,.1f}.",
                                  "detail": "Brings it back to the size you set as the maximum."})
            continue
        if it["action"] == "Watch":
            eb = it.get("exit_below")
            if eb and single and price:
                if price <= eb:
                    tid = f"sellnow:{key}"
                    if not _is_done(done, tid):
                        tasks.append({**base, "id": tid, "kind": "Sell", "order": 0,
                                      "title": f"Sell all {qty:g} shares now: the price (₹{price:,.1f}) has fallen to your ₹{eb:,.0f} exit line.",
                                      "detail": ""})
                    continue
                tid = f"gtt:{key}:{eb:g}"
                if not _is_done(done, tid):
                    tasks.append({**base, "id": tid, "kind": "Stop-loss", "order": 1,
                                  "title": f"Place a stop-loss sell order (as a GTT): sell all {qty:g} shares if the price falls to ₹{eb:,.0f}.",
                                  "detail": f"Trigger ₹{eb:,.0f}, limit about ₹{eb * 0.975:,.0f} so it can fill. Today's price ₹{price:,.1f}. A GTT stays active for months, while an ordinary stop-loss lapses at the end of the day; once it is placed, Groww watches the price for you."})
                    continue
            watching.append({"name": it["name"], "symbols": it["symbols"], "trigger": it.get("trigger") or it["headline"], "review": it["review"]})
    tasks.sort(key=lambda t: (t["order"], t["name"]))
    return {"tasks": tasks, "when": when.isoformat(), "when_label": _day_label(when), "watching": watching,
            "hold_count": sum(1 for i in items if i["action"] == "Hold")}
