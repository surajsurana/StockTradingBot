"""
The one Telegram message of the day (added 2026-09-10, per explicit
direction): capital deployed, cash, unrealised and realised P&L for every
paper-trading pool -- A (active Rs.1,00,000 swing books), A1 (the legacy
Rs.10,00,000 books winding down), B, C and D -- read straight from each
pool's own state files under deployment/state/. Replaces the per-strategy
fill alerts, the per-pool daily messages and Pool A's own end-of-day
summary (all gated off by deployment.settings.TELEGRAM_SINGLE_DAILY_SUMMARY).

Definitions, identical to the ledger snapshot shown 2026-09-10:
  deployed   = sum(entry_price x quantity) over open positions (cost)
  cash       = the book's cash field
  unrealised = sum((latest price - entry_price) x quantity); latest price
               from price_fn (yfinance's most recent daily close), falling
               back to entry price for a symbol with no quote
  realised   = sum of pnl over the book's trades.jsonl (cumulative since
               the book started), plus "today" = the part with exit_date
               == today. Pool D has no overnight book: realised only, with
               today's figure from its own realized_pnl_today_by_symbol.

Pure functions over the filesystem + an injectable price_fn, so the
formatter and the numbers are unit-testable without Telegram or yfinance.
"""

import glob
import json
import os
from datetime import date
from typing import Callable, Optional

POOL_A_DIRNAME = "paper_trading"
POOL_A1_DIRNAME = "paper_trading_legacy"
POOL_B_DIRNAME = "portfolio_b"
POOL_C_DIRNAME = "portfolio_c"
POOL_D_DIRNAME = "pool_d"


def _read_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _book(book_dir: str, key: str, display_name: str, today: date, prices: dict) -> dict:
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    positions = pf.get("positions") or {}
    trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
    deployed = sum(float(p["entry_price"]) * int(p["quantity"]) for p in positions.values())
    unrealised = sum((float(prices.get(sym, p["entry_price"])) - float(p["entry_price"])) * int(p["quantity"])
                     for sym, p in positions.items())
    realised = sum(float(t.get("pnl", 0) or 0) for t in trades)
    realised_today = sum(float(t.get("pnl", 0) or 0) for t in trades if t.get("exit_date") == today.isoformat())
    return {
        "key": key, "display_name": display_name, "positions": len(positions),
        "deployed": round(deployed, 2), "cash": round(float(pf.get("cash", 0) or 0), 2),
        "unrealised": round(unrealised, 2), "realised": round(realised, 2), "realised_today": round(realised_today, 2),
        "updated_today": pf.get("last_processed_date") == today.isoformat(),
        "exists": bool(pf),
    }


def _held_symbols(state_dir: str) -> set:
    symbols = set()
    for pattern in (f"{POOL_A_DIRNAME}/*/portfolio.json", f"{POOL_A1_DIRNAME}/*/portfolio.json",
                    f"{POOL_B_DIRNAME}/portfolio.json", f"{POOL_C_DIRNAME}/portfolio.json"):
        for path in glob.glob(os.path.join(state_dir, pattern)):
            pf = _read_json(path) or {}
            symbols |= set((pf.get("positions") or {}).keys())
    return symbols


def _pool_totals(books: list) -> dict:
    return {
        "positions": sum(b["positions"] for b in books),
        "deployed": round(sum(b["deployed"] for b in books), 2),
        "cash": round(sum(b["cash"] for b in books), 2),
        "unrealised": round(sum(b["unrealised"] for b in books), 2),
        "realised": round(sum(b["realised"] for b in books), 2),
        "realised_today": round(sum(b["realised_today"] for b in books), 2),
        "books": len(books),
        "updated_today": sum(1 for b in books if b["updated_today"]),
        "not_updated": [b["display_name"] for b in books if not b["updated_today"]],
    }


def build_pool_summary(state_dir: str, active_pool_a: dict, price_fn: Callable[[list], dict],
                       today: Optional[date] = None) -> dict:
    """
    active_pool_a: {strategy_key: display_name} of the registry's
    PAPER_TRADING strategies (the caller reads the registry; this stays
    filesystem-only). price_fn(symbols) -> {symbol: latest price}.
    """
    today = today or date.today()
    prices = price_fn(sorted(_held_symbols(state_dir))) if callable(price_fn) else {}

    pool_a = [_book(os.path.join(state_dir, POOL_A_DIRNAME, key), key, name, today, prices)
              for key, name in active_pool_a.items()]
    pool_a1 = [_book(d, os.path.basename(d.rstrip("/\\")), os.path.basename(d.rstrip("/\\")), today, prices)
               for d in sorted(glob.glob(os.path.join(state_dir, POOL_A1_DIRNAME, "*/")))]
    pool_b = [_book(os.path.join(state_dir, POOL_B_DIRNAME), "portfolio_b", "Portfolio B", today, prices)]
    pool_c = [_book(os.path.join(state_dir, POOL_C_DIRNAME), "portfolio_c", "Portfolio C", today, prices)]

    d_pf = _read_json(os.path.join(state_dir, POOL_D_DIRNAME, "portfolio.json")) or {}
    d_trades = _read_jsonl(os.path.join(state_dir, POOL_D_DIRNAME, "trades.jsonl"))
    d_today = (sum(float(v) for v in (d_pf.get("realized_pnl_today_by_symbol") or {}).values())
               if d_pf.get("last_processed_date") == today.isoformat() else 0.0)
    pool_d = {
        "realised": round(sum(float(t.get("pnl", 0) or 0) for t in d_trades), 2),
        "realised_today": round(d_today, 2),
        "trades_total": len(d_trades),
        "trades_today": sum(int(v) for v in (d_pf.get("trades_today_by_symbol") or {}).values())
        if d_pf.get("last_processed_date") == today.isoformat() else 0,
        "updated_today": d_pf.get("last_processed_date") == today.isoformat(),
    }

    pools = {"A": _pool_totals(pool_a), "A1": _pool_totals(pool_a1), "B": _pool_totals(pool_b),
             "C": _pool_totals(pool_c)}
    overall = {
        "deployed": round(sum(p["deployed"] for p in pools.values()), 2),
        "cash": round(sum(p["cash"] for p in pools.values()), 2),
        "unrealised": round(sum(p["unrealised"] for p in pools.values()), 2),
        "realised": round(sum(p["realised"] for p in pools.values()) + pool_d["realised"], 2),
        "realised_today": round(sum(p["realised_today"] for p in pools.values()) + pool_d["realised_today"], 2),
        "positions": sum(p["positions"] for p in pools.values()),
    }
    return {"as_of": today.isoformat(), "pools": pools, "pool_d": pool_d, "overall": overall,
            "books": {"A": pool_a, "A1": pool_a1, "B": pool_b, "C": pool_c}}


def inr(amount: float, signed: bool = False) -> str:
    """Indian grouping (12,34,567) with a minus sign in front of the rupee
    symbol; signed=True adds a leading + for positives."""
    n = int(round(amount))
    sign = "-" if n < 0 else ("+" if signed and n > 0 else "")
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{sign}Rs.{s}"


def _pool_block(title: str, p: dict, detail: str) -> list:
    return [
        f"*{title}* -- {detail}",
        f"Deployed {inr(p['deployed'])} | Cash {inr(p['cash'])}",
        f"Unrealised {inr(p['unrealised'], signed=True)} | Realised {inr(p['realised'], signed=True)} "
        f"(today {inr(p['realised_today'], signed=True)})",
        "",
    ]


def format_pool_summary(summary: dict) -> str:
    """Telegram Markdown. No underscores anywhere (Telegram's legacy
    Markdown treats them as italics and rejects unbalanced ones)."""
    pools, d, o = summary["pools"], summary["pool_d"], summary["overall"]
    day = date.fromisoformat(summary["as_of"]).strftime("%a %d %b %Y")
    lines = [f"*Paper Trading -- {day}*", ""]
    a = pools["A"]
    lines += _pool_block("Pool A", a, f"{a['positions']} positions, {a['updated_today']}/{a['books']} books updated")
    a1 = pools["A1"]
    lines += _pool_block("Pool A1 (legacy, winding down)", a1,
                         f"{a1['positions']} positions, {a1['updated_today']}/{a1['books']} books updated")
    lines += _pool_block("Pool B", pools["B"], f"{pools['B']['positions']} positions")
    lines += _pool_block("Pool C", pools["C"], f"{pools['C']['positions']} positions")
    lines += [
        f"*Pool D (intraday)* -- {d['trades_today']} trades today, {d['trades_total']} total",
        f"Realised {inr(d['realised'], signed=True)} (today {inr(d['realised_today'], signed=True)})",
        "",
        f"*All pools* -- {o['positions']} positions",
        f"Deployed {inr(o['deployed'])} | Cash {inr(o['cash'])}",
        f"Unrealised {inr(o['unrealised'], signed=True)} | Realised {inr(o['realised'], signed=True)} "
        f"(today {inr(o['realised_today'], signed=True)})",
    ]
    missing = a["not_updated"] + a1["not_updated"] + pools["B"]["not_updated"] + pools["C"]["not_updated"]
    if not d["updated_today"]:
        missing.append("Pool D")
    if missing:
        lines += ["", "Not updated today: " + ", ".join(m.replace("_", " ") for m in missing)]
    return "\n".join(lines)
