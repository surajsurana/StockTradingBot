"""
The results reader: after a company reports a new quarter, check the one number its research note said would
decide the call, and turn the watch into a sell, a part-sell, or a cleared watch.

Each rule lives in advice/notes.py, for example:
    {"symbol": "OLECTRA", "metric": "ebitda_margin", "baseline_period": "2026-06-30",
     "need_gain_pts": 1.0, "if_fail": "sell", "label": "EBITDA margin"}
meaning "the first quarter reported after 30 Jun 2026 must show an EBITDA margin at least 1.0 point above the
30 Jun 2026 quarter, otherwise sell". Both quarters come from the same source (Yahoo Finance's quarterly
statements), so a difference in how Yahoo defines the margin cancels out.

It is a number check, not a judgement about the story behind it; the reasons stay in the note. A decision
(ok or fail) is remembered and not re-opened; a quarter that has not appeared yet stays "pending".
The check runs in the evening job (network); the dashboard only reads the saved file.
"""

import json
import os
from datetime import date
from typing import Callable, Dict, Optional

RESULTS_FILE = "advice_results.json"


def fetch_quarterly(symbol: str) -> Dict[str, dict]:
    """{quarter-end ISO date: {revenue, ebitda_margin, op_margin, net_income}} from Yahoo, newest included."""
    import yfinance as yf
    q = yf.Ticker(symbol + ".NS").quarterly_income_stmt
    out: Dict[str, dict] = {}
    if q is None or not len(q):
        return out

    def row(names):
        for n in names:
            if n in q.index:
                return q.loc[n]
        return None
    rev, ebitda = row(["Total Revenue", "Operating Revenue"]), row(["EBITDA", "Normalized EBITDA"])
    opi, ni = row(["Operating Income", "EBIT"]), row(["Net Income"])

    def val(r, col):
        if r is None:
            return None
        v = r[col]
        return None if v != v else float(v)
    for col in q.columns:
        r = val(rev, col)
        if not r or r <= 0:
            continue
        e, o = val(ebitda, col), val(opi, col)
        out[col.date().isoformat()] = {"revenue": r, "ebitda_margin": e / r * 100 if e is not None else None,
                                       "op_margin": o / r * 100 if o is not None else None, "net_income": val(ni, col)}
    return out


def evaluate(rule: dict, metrics: Dict[str, dict]) -> dict:
    """Compare the first quarter reported after the baseline with the baseline quarter."""
    metric = rule["metric"]
    base = (metrics.get(rule["baseline_period"]) or {}).get(metric)
    if base is None:
        return {"status": "error", "detail": "baseline quarter not available"}
    later = sorted(p for p in metrics if p > rule["baseline_period"] and metrics[p].get(metric) is not None)
    if not later:
        return {"status": "pending"}
    p = later[0]
    new = metrics[p][metric]
    delta = new - base
    return {"status": "ok" if delta >= rule["need_gain_pts"] else "fail", "period": p, "baseline": round(base, 1), "new": round(new, 1),
            "delta": round(delta, 1), "label": rule["label"], "need": rule["need_gain_pts"], "if_fail": rule["if_fail"], "symbol": rule["symbol"]}


def load_results(state_dir: Optional[str]) -> dict:
    if not state_dir:
        return {}
    try:
        with open(os.path.join(state_dir, RESULTS_FILE), encoding="utf-8") as f:
            return json.load(f).get("results", {})
    except (OSError, ValueError):
        return {}


def refresh(state_dir: str, notes: dict, today: date, fetch: Optional[Callable[[str], dict]] = None) -> dict:
    """Check every note that has a results rule. Final decisions are kept; pending ones are looked at again."""
    fetch = fetch or fetch_quarterly
    results = load_results(state_dir)
    for key, note in notes.items():
        rule = note.get("results_rule")
        if not rule or results.get(key, {}).get("status") in ("ok", "fail"):
            continue
        try:
            results[key] = {**evaluate(rule, fetch(rule["symbol"])), "checked": today.isoformat()}
        except Exception as e:   # a Yahoo outage must never break the evening job; try again tomorrow
            results[key] = {"status": "error", "detail": f"{type(e).__name__} while fetching results", "checked": today.isoformat()}
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, RESULTS_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=1)
    os.replace(tmp, os.path.join(state_dir, RESULTS_FILE))
    return results
