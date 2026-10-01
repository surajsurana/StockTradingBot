"""
Pool I -- the US equities paper book (2026-10-01, per explicit direction: "lets
do USA market ... shall we backtest the strategies in the US markets and then
pass/fail and then promote to paper trading"). Both Phase-1 backtests against
real yfinance US data over the frozen S&P 500 universe (swing_research/universe_us.py)
PASSED the same walk-forward + out-of-sample checks every other strategy in this
program has to clear: Minervini Trend Template Filter (US), EXP-094, and
Cross-Sectional Momentum (US), EXP-095.

One book per PASS strategy under deployment/state/pool_i/<key>/, run by the SAME
deployment/paper_trading_engine.run_daily() every pool uses, with the SAME
swing_research Strategy class the research verdict was computed with -- exactly
Pool E's own relationship to its crypto strategies.

Differences from Pool E, all disclosed:
  - Data: yfinance US daily candles (data/fetch_historical.py), NYSE/NASDAQ
    trading days. Cron fires at 7:00 AM IST (01:30 UTC), weekday mornings
    Tue-Sat IST -- see run_pool_i.py's own docstring for why as_of_date is
    read from the data itself rather than computed from the clock.
  - Fills: next_day_open (unlike Pool E's same_day_close) -- US equities close
    daily, so "next day's open" is a real, distinct fill, not a same-instant
    fiction the way it would be for a market that never closes.
  - Cost model: $0 commission (realistic for a modern US discount broker), but
    a REAL India-resident US-equity tax model (added 2026-10-01, per explicit
    direction -- "i will also need to check taxes for this ... it should be
    as per what will actually apply"), not a placeholder: see
    swing_research/us_equity_costs.py for the full regime (long-term 20% flat,
    short-term at your actual progressive marginal slab via
    config.settings.US_EQUITY_OTHER_ANNUAL_INCOME) and what is still NOT
    modeled (dividends, LRS/TCS, indexation).

Rupee figures use the same USD/INR rate Pool E's USDT already treats as ~USD
(data/fetch_crypto.fetch_usdinr_rate()) -- exact here, since these are real USD,
not an approximation the way USDT is.
"""

import glob
import os
from datetime import date
from typing import Optional

from config import settings
from reporting.pool_summary import _read_json, _read_jsonl
from swing_research.us_equity_costs import USEquityCostModel, classify_and_tax, financial_year_start

POOL_I_DIRNAME = "pool_i"
# Seeded 2026-10-01 at a live USD/INR rate of 95.945 -- Rs 1,00,000-equivalent
# per strategy, matching one India strategy's post-wind-down seed (Pool A1),
# per explicit direction ("Same rupee-equivalent as one India strategy").
# A fixed dollar amount from here on, same discipline as Pool E's 1,000 USDT
# constant -- not re-derived from a live rate on every run.
POOL_I_STARTING_CAPITAL_USD = 1_042.26
POOL_I_NAMES = {
    "minervini_trend_template_filter_us": "Minervini Trend Template Filter (US)",
    "cross_sectional_momentum_us": "Cross-Sectional Momentum (US)",
}


def _ledger(raw: float, tax: float) -> dict:
    return {"raw": raw, "fees": 0.0, "pre_tax": raw, "tax": tax, "post_tax": raw - tax, "tds": 0.0}


def _sum(ledgers: list) -> dict:
    keys = ("raw", "fees", "pre_tax", "tax", "post_tax", "tds")
    return {k: round(sum(l[k] for l in ledgers), 2) for k in keys}


def _book(book_dir: str, key: str, today: date, prices: dict, model: USEquityCostModel) -> dict:
    """
    Short-term gains are taxed progressively (swing_research/us_equity_costs.py)
    -- each trade's marginal rate depends on the SAME financial year's gains
    already booked before it, so closed trades are processed in exit-date
    order, accumulating a running short-term total that resets at every
    April 1 FY boundary. Open positions are then taxed "as if sold today" on
    top of whichever FY's running total today falls in (same disclosed
    estimate convention every other pool's unbooked figure already uses).
    """
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    positions = pf.get("positions") or {}
    trades = sorted(_read_jsonl(os.path.join(book_dir, "trades.jsonl")), key=lambda t: t.get("exit_date") or "")

    closed_ledgers, today_ledgers = [], []
    fy_start, stcg_so_far = None, 0.0
    for t in trades:
        exit_date = t.get("exit_date")
        this_fy = financial_year_start(date.fromisoformat(exit_date)) if exit_date else fy_start
        if this_fy != fy_start:
            fy_start, stcg_so_far = this_fy, 0.0
        pnl = float(t.get("pnl", 0) or 0)
        result = classify_and_tax(t.get("entry_date"), exit_date, pnl, stcg_so_far, model)
        if not result["is_long_term"]:
            stcg_so_far += max(pnl, 0.0)
        led = _ledger(pnl, result["tax"])
        closed_ledgers.append(led)
        if exit_date == today.isoformat():
            today_ledgers.append(led)

    # Open positions: "as if sold today", stacked on this FY's real running total so far.
    today_fy = financial_year_start(today)
    if today_fy != fy_start:
        stcg_so_far = 0.0
    open_rows, open_ledgers = [], []
    for symbol, p in positions.items():
        entry, qty = float(p["entry_price"]), float(p["quantity"])
        price = float(prices.get(symbol, entry))
        pnl = (price - entry) * qty
        result = classify_and_tax(p.get("entry_date"), today.isoformat(), pnl, stcg_so_far, model)
        if not result["is_long_term"]:
            stcg_so_far += max(pnl, 0.0)
        led = _ledger(pnl, result["tax"])
        open_ledgers.append(led)
        open_rows.append({"symbol": symbol, "quantity": qty, "entry_price": entry, "price": price,
                          "priced": symbol in prices, "entry_date": p.get("entry_date"),
                          "stop_loss": float(p.get("stop_loss", 0) or 0), "value": round(price * qty, 2),
                          "pct": round((price / entry - 1) * 100, 2) if entry else 0.0,
                          "unbooked_raw": round(led["raw"], 2), "unbooked_pre_tax": round(led["pre_tax"], 2),
                          "unbooked_post_tax": round(led["post_tax"], 2), "is_long_term": result["is_long_term"]})

    unbooked, booked, booked_today = _sum(open_ledgers), _sum(closed_ledgers), _sum(today_ledgers)
    deployed = round(sum(float(p["entry_price"]) * float(p["quantity"]) for p in positions.values()), 2)
    cash = round(float(pf.get("cash", 0) or 0), 2)
    return {
        "key": key, "display_name": POOL_I_NAMES.get(key, key), "exists": bool(pf),
        "positions": len(positions), "deployed": deployed, "cash": cash,
        "starting_capital": float(pf.get("starting_capital", POOL_I_STARTING_CAPITAL_USD) or 0),
        "capital": round(cash + deployed - booked["raw"], 2),
        "cash_after_tax": round(cash - booked["tax"], 2),
        "unbooked": unbooked, "booked": booked, "booked_today": booked_today,
        "open_positions": open_rows, "trades_total": len(trades),
        "recent_trades": list(reversed(trades[-15:])),
        "updated_today": pf.get("last_processed_date") == today.isoformat(),
        "last_processed_date": pf.get("last_processed_date"),
    }


def _inr(usd: dict, rate: float) -> dict:
    return {k: round(v * rate, 2) for k, v in usd.items()}


def build_pool_i(state_dir: str, us_prices: Optional[dict], usdinr: float, today: Optional[date] = None,
                 dirname: str = POOL_I_DIRNAME,
                 other_annual_income: Optional[float] = None) -> dict:
    today = today or date.today()
    prices = us_prices or {}
    model = USEquityCostModel(other_annual_income if other_annual_income is not None
                              else settings.US_EQUITY_OTHER_ANNUAL_INCOME)
    books = [_book(d, os.path.basename(d.rstrip("/\\")), today, prices, model)
             for d in sorted(glob.glob(os.path.join(state_dir, dirname, "*/")))]
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
        "exists": bool(books), "books": books, "usd": totals, "usdinr": usdinr,
        "inr": {**_inr(flat, usdinr), "unbooked": _inr(totals["unbooked"], usdinr),
                "booked": _inr(totals["booked"], usdinr), "booked_today": _inr(totals["booked_today"], usdinr)},
        "updated_today": bool(books) and all(b["updated_today"] for b in books),
        "not_updated": [b["display_name"] for b in books if not b["updated_today"]],
        "other_annual_income": model.other_annual_income,
    }
