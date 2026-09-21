"""
The new-ideas screener: looks across the Nifty 500 for good businesses that are not too expensive and are not
already in your portfolio, and lists the best few with the reasons.

It scores each company 0-100 from numbers that can be checked (return on equity, debt, cash flow, growth,
valuation against growth, trend). Banks and other financial companies are left out because those measures
mean something different for them. The scores come from Yahoo Finance's free statements, which are patchy for
Indian companies, so a candidate is a starting point for your own research and NOT a tested recommendation.

Run weekly on the server (a few hundred lookups, about 20 minutes): `python refresh_screener.py`.
The dashboard only reads the saved file. New-stock buy orders appear on the Advice page only when your rules
leave room for individual stocks; until then the list is for information.
"""

import csv
import json
import os
import time
from datetime import date
from typing import Callable, Dict, List, Optional

SCREENER_FILE = "advice_screener.json"
MIN_MCAP_CR = 5000.0
MIN_SCORE = 65


def load_universe(path: Optional[str] = None) -> List[dict]:
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "nifty500_constituents.csv")
    with open(path, encoding="utf-8") as f:
        return [{"symbol": r["Symbol"], "name": r["Company Name"], "industry": r["Industry"]} for r in csv.DictReader(f)
                if r["Industry"] != "Financial Services"]


def _cagr(series: list) -> Optional[float]:
    """Yearly growth from the oldest to the newest of the numbers (given newest first)."""
    vals = [v for v in series if v is not None and v == v]
    if len(vals) < 3 or vals[-1] <= 0 or vals[0] <= 0:
        return None
    return ((vals[0] / vals[-1]) ** (1 / (len(vals) - 1)) - 1) * 100


def fetch_metrics(symbol: str) -> Optional[dict]:
    """Numbers for one company from Yahoo Finance, or None if too little is available."""
    import yfinance as yf
    t = yf.Ticker(symbol + ".NS")
    fin, bs, cf = t.financials, t.balance_sheet, t.cashflow

    def row(df, names):
        if df is None or not len(df):
            return []
        for n in names:
            if n in df.index:
                return [None if v != v else float(v) for v in df.loc[n].tolist()]
        return []
    rev, ni = row(fin, ["Total Revenue", "Operating Revenue"]), row(fin, ["Net Income", "Net Income Common Stockholders"])
    eq, debt, fcf = row(bs, ["Stockholders Equity", "Common Stock Equity"]), row(bs, ["Total Debt"]), row(cf, ["Free Cash Flow"])
    if not rev or not ni or not eq or not eq[0] or eq[0] <= 0 or ni[0] is None:
        return None
    info = t.info or {}
    hist = t.history(period="14mo", auto_adjust=True)["Close"].dropna()
    if len(hist) < 200:
        return None
    price = float(hist.iloc[-1])
    return {"symbol": symbol, "price": round(price, 2), "mcap_cr": (info.get("marketCap") or 0) / 1e7,
            "roe": ni[0] / eq[0] * 100, "de": (debt[0] / eq[0]) if debt and debt[0] is not None else None,
            "fcf_pos": sum(1 for v in fcf if v is not None and v > 0), "fcf_n": sum(1 for v in fcf if v is not None),
            "rev_cagr": _cagr(rev), "ni_cagr": _cagr(ni), "pe": info.get("trailingPE"), "fpe": info.get("forwardPE"),
            "vs200": (price / float(hist.rolling(200).mean().iloc[-1]) - 1) * 100, "off_high": (price / float(hist.iloc[-252:].max()) - 1) * 100}


def score(m: dict) -> dict:
    """0-100 from checkable numbers, with a plain-language reason for each point earned and each watch-out."""
    pts, why, warn = 0, [], []

    def add(n, text):
        nonlocal pts
        pts += n
        why.append(text)
    roe = m.get("roe")
    if roe is not None:
        if roe >= 15: add(20, f"return on equity {roe:.0f}%")
        elif roe >= 12: add(12, f"return on equity {roe:.0f}%")
    de = m.get("de")
    if de is not None:
        if de <= 0.5: add(15, "low debt")
        elif de <= 1.0: add(8, "moderate debt")
        else: warn.append(f"debt is {de:.1f} times equity")
    if m.get("fcf_n"):
        if m["fcf_pos"] >= 3: add(15, f"free cash flow positive in {m['fcf_pos']} of {m['fcf_n']} years")
        elif m["fcf_pos"] == 2: add(7, "free cash flow positive in 2 years")
        else: warn.append("weak free cash flow")
    rc, nc = m.get("rev_cagr"), m.get("ni_cagr")
    if rc is not None:
        if rc >= 12: add(15, f"revenue growing {rc:.0f}% a year")
        elif rc >= 8: add(8, f"revenue growing {rc:.0f}% a year")
    if nc is not None:
        if nc >= 12: add(15, f"profit growing {nc:.0f}% a year")
        elif nc >= 8: add(8, f"profit growing {nc:.0f}% a year")
    pe = m.get("pe")
    if pe and nc and nc > 0:
        peg = pe / nc
        if peg <= 1.5: add(10, f"price is {peg:.1f} times its profit growth")
        elif peg <= 2.5: add(5, f"price is {peg:.1f} times its profit growth")
        else: warn.append(f"expensive against its growth ({pe:.0f} times earnings)")
    if pe and pe <= 35:
        add(5, f"{pe:.0f} times earnings")
    if m.get("vs200") is not None:
        if m["vs200"] >= 0: add(5, "above its 200-day average")
        else: warn.append("below its 200-day average")
    return {"score": pts, "why": why, "warn": warn}


def load_screener(state_dir: Optional[str]) -> dict:
    if not state_dir:
        return {}
    try:
        with open(os.path.join(state_dir, SCREENER_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh(state_dir: str, today: date, universe: Optional[List[dict]] = None, fetch: Callable[[str], Optional[dict]] = fetch_metrics,
            pause: float = 0.4, limit: Optional[int] = None) -> dict:
    """Score the whole universe and save the ones that clear the bar. Failures are skipped, never fatal."""
    universe = universe if universe is not None else load_universe()
    out: List[dict] = []
    tried = ok = 0
    for u in universe[:limit]:
        tried += 1
        try:
            m = fetch(u["symbol"])
        except Exception:
            m = None
        if m and m.get("mcap_cr", 0) >= MIN_MCAP_CR and m.get("ni_cagr") is not None and (m.get("roe") or 0) > 0:
            s = score(m)
            if s["score"] >= MIN_SCORE:
                ok += 1
                out.append({**u, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in m.items() if k != "symbol"}, **s})
        time.sleep(pause)
    out.sort(key=lambda r: -r["score"])
    data = {"as_of": today.isoformat(), "screened": tried, "passed": ok, "candidates": out[:40]}
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, SCREENER_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(state_dir, SCREENER_FILE))
    return data


def pick(screener: dict, held: set, segment_of: Callable[[str], str], over_segments: set, top: int = 10) -> List[dict]:
    """The best candidates not already held and not in an industry group that is already too big."""
    out = []
    for c in screener.get("candidates", []):
        if c["symbol"] in held or segment_of(c["symbol"]) in over_segments:
            continue
        out.append(c)
    return out[:top]
