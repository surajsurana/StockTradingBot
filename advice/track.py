"""
The track record: every buy or sell the Advice page asks you to do is logged with the price on that day and the
Nifty 50 price, and later scored: did a bought stock do better than the Nifty since, and did a sold stock do worse?

A stop-loss order is a protective order, not a call on direction, so it is logged but not scored.
Fewer than 30 days of history is "too early": a month of prices proves nothing. The log is written by the evening
job; the dashboard only reads it.
"""

import json
import os
from datetime import date
from typing import Callable, Dict, List, Optional

LOG_FILE = "advice_log.json"
MIN_DAYS = 30
RELOG_AFTER_DAYS = 30       # the same standing task (for example a monthly buy) is logged again only after this long


def load_log(state_dir: Optional[str]) -> List[dict]:
    if not state_dir:
        return []
    try:
        with open(os.path.join(state_dir, LOG_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(state_dir: str, log: List[dict]) -> None:
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, LOG_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log[-2000:], f, indent=1)
    os.replace(tmp, os.path.join(state_dir, LOG_FILE))


def log_tasks(state_dir: str, tasks: list, prices: Dict[str, float], nifty: Optional[float], today: date) -> List[dict]:
    """Add tasks not seen recently. `prices` is symbol -> last price. Returns the entries added."""
    log = load_log(state_dir)
    added = []
    for t in tasks:
        if t["kind"] not in ("Buy", "Sell", "Stop-loss"):
            continue
        sym = t["symbols"][0]
        recent = [e for e in log if e["id"] == t["id"] and (today - date.fromisoformat(e["date"])).days < RELOG_AFTER_DAYS]
        px = prices.get(sym) or t.get("price")
        if recent or not px:
            continue
        e = {"id": t["id"], "date": today.isoformat(), "name": t["name"], "symbol": sym, "kind": t["kind"], "price": round(float(px), 2),
             "nifty": round(float(nifty), 2) if nifty else None, "last": round(float(px), 2), "last_nifty": round(float(nifty), 2) if nifty else None,
             "last_date": today.isoformat()}
        log.append(e)
        added.append(e)
    if added:
        _save(state_dir, log)
    return added


def update_last(state_dir: str, prices: Dict[str, float], nifty: Optional[float], today: date) -> None:
    """Refresh each entry's latest price so a stock you have since sold can still be scored."""
    log = load_log(state_dir)
    for e in log:
        if e["symbol"] in prices:
            e["last"], e["last_nifty"], e["last_date"] = round(float(prices[e["symbol"]]), 2), (round(float(nifty), 2) if nifty else e.get("last_nifty")), today.isoformat()
    _save(state_dir, log)


def track_view(log: List[dict], today: date) -> dict:
    rows, scored = [], []
    for e in reversed(log):
        days = (date.fromisoformat(e["last_date"]) - date.fromisoformat(e["date"])).days
        move = (e["last"] / e["price"] - 1) * 100 if e.get("price") else None
        bench = (e["last_nifty"] / e["nifty"] - 1) * 100 if e.get("nifty") and e.get("last_nifty") else None
        excess = move - bench if move is not None and bench is not None else None
        if e["kind"] == "Stop-loss":
            verdict = "not scored (protective order)"
        elif e["symbol"] == "NIFTYBEES":
            verdict = "not scored (this is the benchmark)"
        elif days < MIN_DAYS or excess is None:
            verdict = "too early"
        else:
            good = excess > 0 if e["kind"] == "Buy" else excess < 0
            verdict = "right call" if good else "wrong call"
            scored.append((good, excess if e["kind"] == "Buy" else -excess))
        rows.append({"date": e["date"], "name": e["name"], "kind": e["kind"], "price": e["price"], "last": e["last"], "move": None if move is None else round(move, 1),
                     "nifty": None if bench is None else round(bench, 1), "days": days, "verdict": verdict})
    return {"rows": rows, "scored": len(scored), "right": sum(1 for g, _ in scored if g),
            "avg_edge": round(sum(x for _, x in scored) / len(scored), 1) if scored else None, "total": len(log), "min_days": MIN_DAYS}
