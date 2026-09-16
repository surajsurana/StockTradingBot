"""
Everything the live dashboard (dashboard/server.py) shows, as one JSON-
ready dict -- built from the same state files the daily Telegram summary
reads (reporting/pool_summary.py), plus per-position detail, recent
closed trades, Pool D's intraday state, each job's last-run evidence,
the strategy registry, and the static description of the agent team and
the two pipelines (research and the live daily cycle). Pure over the
filesystem + an injectable price_fn / clock, so it is unit-testable
without the network.
"""

import glob
import json
import os
from datetime import date, datetime, time as dtime
from typing import Callable, Optional

from deployment.base import is_crypto_record
from reporting.pool_summary import _book, _read_json, _read_jsonl, build_pool_summary  # noqa: F401

# Pool A1 (the legacy wind-down books) is deliberately absent from the
# dashboard, per explicit direction 2026-09-11 -- it stays in the daily
# Telegram summary only.
POOL_DIRS = {"A": "paper_trading", "B": "portfolio_b", "C": "portfolio_c"}
POOL_LABELS = {"A": "Pool A", "B": "Pool B", "C": "Pool C", "D": "Pool D", "F": "Pool E"}

# ----------------------------------------------------------------------------
# Static: the agent team and the pipelines. Kept as data so the page can
# draw them and highlight what ran today without hand-maintained HTML.
# ----------------------------------------------------------------------------
DESKS = [
    {"id": "research_lab", "name": "Intraday Research Lab (feeds Pool D)", "icon": "\U0001F52C",
     "blurb": "Dreams up intraday ideas and tests them to destruction on real 5-minute data."},
    {"id": "swing_research", "name": "Swing Research (feeds Pool A)", "icon": "\U0001F4DA",
     "blurb": "Takes strategies from the academic literature and proves them on years of NSE data."},
    {"id": "trading_desk", "name": "Trading Desk (Pools A and D)", "icon": "\U0001F4C8",
     "blurb": "Runs every approved strategy as a paper book, day after day."},
    {"id": "portfolio_team", "name": "Portfolio Team (Pools B and C)", "icon": "\U0001F9E0",
     "blurb": "AI analysts who debate each candidate the way a small fund's team would."},
    {"id": "crypto_desk", "name": "Crypto Desk (Pool E)", "icon": "\u20BF",
     "blurb": "Tests published crypto rules on Binance history, judged only after fees and India's 31.2% tax, "
              "and runs the survivors as a 1,000 USDT paper book."},
    {"id": "reporting", "name": "Reporting (all pools)", "icon": "\U0001F4E8",
     "blurb": "Keeps the books and sends the one message a day."},
]

# job = what a visitor sees on the card; detail = shown on click. status_from
# names the schedule job whose presence in today's log means "worked today";
# "market" means active while the market is open; None = works on request.
AGENTS = [
    {"id": "quant_researcher", "avatar": {"type": "robot", "body": "#3E7CB1", "eye": "#F2C14E", "shape": "round"}, "name": "Quant Researcher", "icon": "\U0001F4A1", "desk": "research_lab", "kind": "AI",
     "job": "Proposes new intraday ideas for Pool D", "status_from": None,
     "detail": "Writes a batch of fresh hypotheses -- each with a mechanism, rules and why it differs from "
               "everything already tried -- after reading the full history of what failed and why."},
    {"id": "research_director", "avatar": {"type": "robot", "body": "#5B4B8A", "eye": "#4CC383", "shape": "round"}, "name": "Research Director", "icon": "\U0001F9ED", "desk": "research_lab", "kind": "AI + rules",
     "job": "Picks what gets tested", "status_from": None,
     "detail": "Draws cross-cutting lessons from every past experiment, throws out ideas the lab has no data "
               "for, ranks the rest and runs the chosen one through the whole pipeline."},
    {"id": "backtesting_engineer", "avatar": {"type": "robot", "body": "#7A8B99", "eye": "#5FB7C0", "shape": "square"}, "name": "Backtesting Engineer", "icon": "\u2699\ufe0f", "desk": "research_lab", "kind": "Mechanical",
     "job": "Simulates trades bar by bar", "status_from": None,
     "detail": "Real Kite 5-minute candles, stops checked before targets, forced square-off at the close, "
               "no peeking ahead -- and, since EXP-011, net of real transaction costs."},
    {"id": "statistical_auditor", "avatar": {"type": "robot", "body": "#4E5D6C", "eye": "#E0A85A", "shape": "square"}, "name": "Statistical Auditor", "icon": "\u2696\ufe0f", "desk": "research_lab", "kind": "Rules only",
     "job": "Says PASS or REJECT (Pools A, D and E)", "status_from": None,
     "detail": "The gate nobody can talk round: enough trades, enough positive walk-forward windows, and a "
               "positive result on the untouched out-of-sample slice -- or it is a REJECT."},
    {"id": "performance_analyst", "avatar": {"type": "robot", "body": "#2E8B57", "eye": "#F2C14E", "shape": "round"}, "name": "Performance Analyst", "icon": "\U0001F4DD", "desk": "research_lab", "kind": "AI",
     "job": "Explains each verdict", "status_from": None,
     "detail": "After the verdict is decided, writes the plain-English story: which sectors, regimes and "
               "times of day carried or sank the result."},
    {"id": "knowledge_base", "avatar": {"type": "robot", "body": "#9C8C6E", "eye": "#B23A3A", "shape": "square"}, "name": "Knowledge Base", "icon": "\U0001F5C4\ufe0f", "desk": "research_lab", "kind": "Memory",
     "job": "Remembers every result", "status_from": None,
     "detail": "Every experiment's verdict and reason, plus standing rules like the 15-bps minimum-edge "
               "rule that all future ideas are checked against."},
    {"id": "published_research_analyst", "avatar": {"type": "robot", "body": "#B85C38", "eye": "#5FB7C0", "shape": "round"}, "name": "Literature Analyst", "icon": "\U0001F4D6", "desk": "swing_research", "kind": "Curated",
     "job": "Documents the published rules (Pools A and E)", "status_from": None,
     "detail": "For each strategy taken from a paper: the citation, the exact rules, the variant chosen, and "
               "every simplification with its estimated impact."},
    {"id": "swing_director", "avatar": {"type": "robot", "body": "#1F6F78", "eye": "#F2C14E", "shape": "round"}, "name": "Swing Director", "icon": "\U0001F3AF", "desk": "swing_research", "kind": "Mechanical + AI",
     "job": "Runs the multi-year backtests for Pool A", "status_from": None,
     "detail": "Full-period run for the headline numbers, walk-forward windows for the Auditor, "
               "benchmarks, and an evidence-quality score that ignores the outcome."},
    {"id": "evidence_quality", "avatar": {"type": "robot", "body": "#6C7A89", "eye": "#4CC383", "shape": "square"}, "name": "Evidence Scorer", "icon": "\U0001F4CF", "desk": "swing_research", "kind": "Rules only",
     "job": "Rates how trustworthy a result is", "status_from": None,
     "detail": "0-100 from trade count, out-of-sample trade count, window count and data coverage -- "
               "calculated before anyone looks at whether the strategy made money."},
    {"id": "deployment_manager", "avatar": {"type": "robot", "body": "#8E7C68", "eye": "#3E7CB1", "shape": "square"}, "name": "Registrar", "icon": "\U0001F4CB", "desk": "trading_desk", "kind": "Registry",
     "job": "Keeps the register for Pools A and E", "status_from": None,
     "detail": "Permanent SW-IDs, research verdicts, deployment status and the audit trail. Nothing "
               "trades unless it is marked PAPER_TRADING here."},
    {"id": "paper_trading_engine", "avatar": {"type": "robot", "body": "#5A6E7F", "eye": "#F2C14E", "shape": "square"}, "name": "Swing Trader", "icon": "\U0001F4BC", "desk": "trading_desk", "kind": "Mechanical",
     "job": "Runs the Pool A books after the close", "status_from": "eod_a",
     "detail": "Checks stops and targets, asks each strategy for exits and entries, queues the entries for "
               "the next open, marks the book and writes the report."},
    {"id": "pool_d_engine", "avatar": {"type": "robot", "body": "#3F4C5A", "eye": "#E0706A", "shape": "square"}, "name": "Intraday Trader", "icon": "\u26A1", "desk": "trading_desk", "kind": "Mechanical",
     "job": "Trades Pool D every 5 minutes", "status_from": "market",
     "detail": "Fetches today's bars for the Nifty 500, catches stops even on a missed poll, takes new "
               "signals on one shared Rs.1,00,000 book, squares off by 15:25."},
    {"id": "fundamental_agent", "avatar": {"type": "robot", "body": "#7B4F9D", "eye": "#4CC383", "shape": "round"}, "name": "Fundamentals Analyst", "icon": "\U0001F4CA", "desk": "portfolio_team", "kind": "AI",
     "job": "Checks the company's health (Pools B and C)", "status_from": "eod_c",
     "detail": "Reads the fundamentals of each candidate and grades them."},
    {"id": "news_agent", "avatar": {"type": "robot", "body": "#D9822B", "eye": "#1E2430", "shape": "round"}, "name": "News Analyst", "icon": "\U0001F4F0", "desk": "portfolio_team", "kind": "AI",
     "job": "Scans the headlines", "status_from": "eod_c",
     "detail": "Looks for event risk and sentiment in recent news about the candidate."},
    {"id": "research_analyst", "avatar": {"type": "robot", "body": "#3B6E8F", "eye": "#F2C14E", "shape": "round"}, "name": "Research Analyst", "icon": "\U0001F50E", "desk": "portfolio_team", "kind": "AI",
     "job": "Forms the verdict", "status_from": "eod_c",
     "detail": "Weighs the signal, the fundamentals and the news and says whether the setup is worth taking."},
    {"id": "portfolio_manager", "avatar": {"type": "robot", "body": "#1E2430", "eye": "#5FB7C0", "shape": "round"}, "name": "Portfolio Manager", "icon": "\U0001F454", "desk": "portfolio_team", "kind": "AI",
     "job": "Decides what makes the Pool B and C books", "status_from": "eod_c",
     "detail": "Chooses among the approved candidates and sets their weights."},
    {"id": "risk_manager_live", "avatar": {"type": "robot", "body": "#5E6B5E", "eye": "#B23A3A", "shape": "square"}, "name": "Risk Manager", "icon": "\U0001F6E1\ufe0f", "desk": "portfolio_team", "kind": "Rules",
     "job": "Sizes and vetoes", "status_from": "eod_c",
     "detail": "Sizes every position against its stop and blocks anything that breaches the book's limits."},
    {"id": "crypto_data", "avatar": {"type": "robot", "body": "#6E6E6E", "eye": "#F2C14E", "shape": "square"}, "name": "Crypto Data Feed", "icon": "\U0001F4E1", "desk": "crypto_desk", "kind": "Mechanical",
     "job": "Pulls Binance daily candles", "status_from": "pool_e",
     "detail": "Public Binance history for the five majors (BTC, ETH, BNB, XRP, SOL) back to 2017, seven days a "
               "week, plus the live USD/INR rate so every figure can be shown in rupees."},
    {"id": "crypto_tax", "avatar": {"type": "robot", "body": "#4E5D6C", "eye": "#E0A85A", "shape": "square"}, "name": "Tax Accountant", "icon": "\U0001F9FE", "desk": "crypto_desk", "kind": "Rules only",
     "job": "Takes fees and 31.2% tax off every trade", "status_from": None,
     "detail": "0.30% a side plus spread, then India's VDA tax: 31.2% of each profitable trade with no set-off for "
               "losers and fees not deductible; 1% TDS on sales shown as withheld and refundable. The Auditor "
               "only ever sees the post-tax trades; pre-tax is recorded alongside."},
    {"id": "crypto_trader", "avatar": {"type": "robot", "body": "#3F4C5A", "eye": "#4CC383", "shape": "square"}, "name": "Crypto Trader", "icon": "\u20BF", "desk": "crypto_desk", "kind": "Mechanical",
     "job": "Runs Pool E after the 00:00 UTC close", "status_from": "pool_e",
     "detail": "Same paper engine as Pool A on a 1,000 USDT book: month-end decisions from the strategy, 20% "
               "stops checked daily, fractional coins, fills at the close it just saw (crypto never closes)."},
    {"id": "pool_summary", "avatar": {"type": "robot", "body": "#7D6B8A", "eye": "#5FB7C0", "shape": "square"}, "name": "Bookkeeper", "icon": "\U0001F9FE", "desk": "reporting", "kind": "Mechanical",
     "job": "Sends the daily Telegram", "status_from": "summary",
     "detail": "Adds up deployed capital, cash, unrealised and realised P&L for every pool and sends the one "
               "message of the day at 16:05."},
]

# Two stories told as strips of steps with icons; the page draws them.
FLOWS = {
    "daily": {
        "title": "A trading day",
        "steps": [
            {"id": "pool_e", "icon": "\u20BF", "label": "05:45", "text": "Crypto Trader marks Pool E after the UTC close (every day)"},
            {"id": "prep", "icon": "\U0001F305", "label": "09:00", "text": "Intraday Trader studies 90 days of history for 457 stocks"},
            {"id": "open", "icon": "\U0001F514", "label": "09:30", "text": "Yesterday's queued swing orders fill at the open"},
            {"id": "ticks", "icon": "\u26A1", "label": "09:15-15:30", "text": "Pool D checks every stock every 5 minutes"},
            {"id": "eod_a", "icon": "\U0001F4BC", "label": "15:35", "text": "Swing Trader closes what needs closing, queues new entries"},
            {"id": "eod_c", "icon": "\U0001F9E0", "label": "15:45", "text": "Portfolio B & C team debates today's candidates"},
            {"id": "summary", "icon": "\U0001F4E8", "label": "16:05", "text": "Bookkeeper sends the one Telegram message"},
        ],
    },
    "research": {
        "title": "How a strategy earns its place",
        "steps": [
            {"id": "idea", "icon": "\U0001F4A1", "label": "Idea", "text": "From a published paper, or the Quant Researcher"},
            {"id": "rules", "icon": "\U0001F4D6", "label": "Rules", "text": "Written down exactly, every simplification disclosed"},
            {"id": "backtest", "icon": "\u2699\ufe0f", "label": "Backtest", "text": "Years of real data, no peeking ahead"},
            {"id": "audit", "icon": "\u2696\ufe0f", "label": "Audit", "text": "Statistical Auditor: PASS or REJECT, rules only (crypto: after fees and tax)"},
            {"id": "promote", "icon": "\U0001F4CB", "label": "Register", "text": "Gets an SW-ID and a Rs.1,00,000 paper book (crypto: 1,000 USDT in Pool E)"},
            {"id": "trade", "icon": "\U0001F4C8", "label": "Trade", "text": "Runs live in Pool A or Pool E, watched every day"},
        ],
    },
}


# Cron jobs as the page's schedule strip. (hour, minute) in IST; "every5" spans a window.
SCHEDULE = [
    {"id": "pool_e", "label": "Pool E crypto (after the 00:00 UTC close)", "at": "05:45", "log": "pool_e.log"},
    {"id": "prep", "label": "Pool D prepare", "at": "09:00", "log": "pool_d.log"},
    {"id": "ticks", "label": "Pool D ticks", "at": "09:15-15:30 every 5 min", "log": "pool_d.log"},
    {"id": "open", "label": "Fill-at-open passes (A, A1, B, C)", "at": "09:30-09:32", "log": "paper_trading_open.log"},
    {"id": "eod_a", "label": "Pool A end-of-day", "at": "15:35", "log": "paper_trading.log"},
    {"id": "eod_a1", "label": "Pool A1 end-of-day", "at": "15:40", "log": "pool_a1.log"},
    {"id": "eod_c", "label": "Portfolio C", "at": "15:45", "log": "portfolio_c.log"},
    {"id": "eod_b", "label": "Portfolio B", "at": "15:50", "log": "portfolio_b.log"},
    {"id": "summary", "label": "Telegram daily summary", "at": "16:05", "log": "daily_pool_summary.log"},
]


def _positions_detail(book_dir: str, prices: dict) -> list:
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    rows = []
    for symbol, p in (pf.get("positions") or {}).items():
        price = float(prices.get(symbol, p["entry_price"]))
        rows.append({
            "symbol": symbol.replace(".NS", ""), "entry_date": p.get("entry_date"), "qty": int(p["quantity"]),
            "entry": round(float(p["entry_price"]), 2), "price": round(price, 2),
            "stop": round(float(p.get("stop_loss", 0) or 0), 2),
            "invested": round(float(p["entry_price"]) * int(p["quantity"]), 2),
            "unbooked": round((price - float(p["entry_price"])) * int(p["quantity"]), 2),
            "pct": round((price / float(p["entry_price"]) - 1) * 100, 2) if p["entry_price"] else 0.0,
            "priced": symbol in prices, "strategy_name": p.get("strategy_name"),
        })
    rows.sort(key=lambda r: r["unbooked"], reverse=True)
    return rows


def _recent_trades(book_dir: str, limit: int = 10) -> list:
    trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
    out = []
    for t in trades[-limit:]:
        out.append({"symbol": str(t.get("symbol", "")).replace(".NS", ""), "entry_date": t.get("entry_date"),
                    "exit_date": t.get("exit_date"), "entry": t.get("entry_price"), "exit": t.get("exit_price"),
                    "qty": t.get("quantity"), "pnl": round(float(t.get("pnl", 0) or 0), 2),
                    "reason": t.get("exit_reason") or t.get("reason"), "direction": t.get("direction", "BUY")})
    return list(reversed(out))


def _log_last_modified(logs_dir: str, name: str) -> Optional[str]:
    path = os.path.join(logs_dir, name)
    if not os.path.exists(path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="minutes")


def _log_tail(logs_dir: str, name: str, lines: int = 6) -> list:
    path = os.path.join(logs_dir, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read().splitlines()
    return [l for l in content if l.strip()][-lines:]


def _with_capital(p: dict) -> dict:
    """Capital = cash + deployed (at cost) - realised: what the book was
    given, net of any capital wind-down withdrawals -- robust to a stale
    starting_capital field (PEAD's still reads Rs.10,00,000)."""
    p["capital"] = round(p["cash"] + p["deployed"] - p["realised"], 2)
    return p


def roadmap_view(roadmap: dict, registry_records: list) -> dict:
    """The Head-of-Research roadmap (swing_research/research_roadmap.py)
    reduced to what the Strategies tab shows: ranked candidates that
    could be researched now, and the ones waiting on data. Candidates
    whose key is already in the registry are dropped -- the roadmap's
    own candidate list lags promotions."""
    taken = {r.strategy_key for r in registry_records}

    def row(s, rank=None):
        c = s.candidate
        return {"rank": rank, "key": c.key, "name": c.name, "family": c.factor_family, "year": c.year,
                "authors": c.authors, "holding": c.typical_holding_period, "direction": c.direction,
                "score": s.total_score, "axes": {k: round(v, 1) for k, v in s.axis_scores.items()},
                "feasibility": s.feasibility_classification,
                "blockers": list(s.feasibility_reasons)[:2], "strengths": c.known_strengths,
                "weaknesses": c.known_weaknesses}

    from swing_research.research_roadmap import DEFERRED_BY_DIRECTION
    ready = [s for s in roadmap["researchable_now"] if s.candidate.key not in taken]
    deferred = [s for s in roadmap["deferred_pending_data"] if s.candidate.key not in taken]
    by_direction = [s for s in roadmap.get("deferred_by_direction", []) if s.candidate.key not in taken]
    rows = [row(s) for s in deferred]
    for s in by_direction:
        r = row(s)
        r["blockers"] = [DEFERRED_BY_DIRECTION[s.candidate.key]]
        rows.append(r)
    return {"ready": [row(s, i + 1) for i, s in enumerate(ready)], "deferred": rows,
            "weights": roadmap.get("weights", {})}


def build_dashboard_state(state_dir: str, logs_dir: str, registry_records: list, prices: dict,
                          prices_as_of: Optional[str], now: Optional[datetime] = None,
                          roadmap: Optional[dict] = None, mode: str = "paper",
                          crypto_prices: Optional[dict] = None, usdinr: Optional[float] = None) -> dict:
    now = now or datetime.now()
    today = now.date()
    active = {r.strategy_key: r.display_name for r in registry_records
              if str(getattr(r.deployment_status, "value", r.deployment_status)).endswith("PAPER_TRADING")
              and not is_crypto_record(r)}
    summary = build_pool_summary(state_dir, active, lambda symbols: {s: prices[s] for s in symbols if s in prices},
                                 today=today, crypto_prices=crypto_prices, usdinr=usdinr)

    books = []
    for pool, dirname in POOL_DIRS.items():
        if pool == "A":
            for b in summary["books"][pool]:
                book_dir = os.path.join(state_dir, dirname, b["key"])
                rec = next((r for r in registry_records if r.strategy_key == b["key"]), None)
                books.append({**b, "pool": pool, "sid": getattr(rec, "strategy_id", "") if rec else "",
                              "positions_detail": _positions_detail(book_dir, prices),
                              "recent_trades": _recent_trades(book_dir)})
        else:
            b = summary["books"][pool][0]
            book_dir = os.path.join(state_dir, dirname)
            books.append({**b, "pool": pool, "sid": "", "positions_detail": _positions_detail(book_dir, prices),
                          "recent_trades": _recent_trades(book_dir)})

    d_pf = _read_json(os.path.join(state_dir, "pool_d", "portfolio.json")) or {}
    d_trades = _read_jsonl(os.path.join(state_dir, "pool_d", "trades.jsonl"))
    d_state_path = os.path.join(state_dir, "pool_d", "portfolio.json")
    d_open = []
    for s, p in (d_pf.get("positions") or {}).items():
        price = float(prices.get(s, p["entry_price"]))
        sign = -1 if p.get("direction") == "SELL" else 1
        d_open.append({"symbol": s, **{k: p.get(k) for k in ("direction", "entry_price", "stop_loss", "target",
                                                               "quantity", "entry_timestamp")},
                       "price": round(price, 2), "priced": s in prices,
                       "unbooked": round(sign * (price - float(p["entry_price"])) * int(p["quantity"]), 2),
                       "pct": round(sign * (price / float(p["entry_price"]) - 1) * 100, 2) if p["entry_price"] else 0.0})
    pool_d = {
        **summary["pool_d"],
        "starting_capital": d_pf.get("starting_capital"),
        "unrealised": round(sum(p["unbooked"] for p in d_open), 2),
        "open_positions": d_open,
        "todays_trades": [t for t in d_trades if t.get("exit_date") == today.isoformat()],
        "recent_trades": list(reversed(d_trades[-15:])),
        "symbols_with_context": len(d_pf.get("context_by_symbol") or {}),
        "strategy": "VWAP Extension Exhaustion Fade (framework test; research verdict REJECT, EXP-008)",
        "strategies_live": 1,
        "last_tick": (datetime.fromtimestamp(os.path.getmtime(d_state_path)).isoformat(timespec="minutes")
                      if os.path.exists(d_state_path) else None),
    }

    schedule = []
    for job in SCHEDULE:
        schedule.append({**job, "last_log_write": _log_last_modified(logs_dir, job["log"]),
                         "tail": _log_tail(logs_dir, job["log"])})

    registry = [{"key": r.strategy_key, "sid": getattr(r, "strategy_id", ""), "name": r.display_name,
                 "verdict": str(getattr(r.research_verdict, "value", r.research_verdict)),
                 "status": str(getattr(r.deployment_status, "value", r.deployment_status)),
                 "experiment": getattr(r, "primary_experiment_id", ""), "family": getattr(r, "strategy_family", "")}
                for r in registry_records]

    market_open = now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)
    pools = {k: _with_capital(dict(v)) for k, v in summary["pools"].items() if k != "A1"}
    pool_d = _with_capital(pool_d)
    for b in books:
        _with_capital(b)
    pool_e = summary["pool_e"]
    for r in registry_records:
        if is_crypto_record(r):
            for b in pool_e["books"]:
                if b["key"] == r.strategy_key:
                    b["sid"] = getattr(r, "strategy_id", "")
    overall = dict(summary["overall"])
    overall["capital"] = round(sum(p["capital"] for p in pools.values()) + pool_d["capital"]
                               + pool_e["inr"]["capital"], 2)
    overall["unrealised"] = round(overall["unrealised"] + pool_d["unrealised"], 2)
    return {
        "mode": mode, "generated_at": now.isoformat(timespec="seconds"), "today": today.isoformat(),
        "market_open": market_open, "prices_as_of": prices_as_of, "priced_symbols": len(prices),
        "pools": pools, "overall": overall, "books": books, "pool_d": pool_d, "pool_e": pool_e,
        "activity_today": _activity_today(state_dir, books, d_pf, d_trades, today, pool_e, d_open),
        "schedule": schedule, "registry": registry, "agents": AGENTS, "desks": DESKS, "flows": FLOWS,
        "roadmap": roadmap_view(roadmap, registry_records) if roadmap else {"ready": [], "deferred": [], "weights": {}},
    }


def _activity_today(state_dir: str, books: list, d_pf: dict, d_trades: list, today: date,
                    pool_e: Optional[dict] = None, d_open: Optional[list] = None) -> list:
    """Every buy and sell that happened today across Pools A, B, C and D
    as one time-sorted list. Swing books fill at the open (09:30) so
    their entries/exits carry that clock time; Pool D carries its own
    tick timestamps."""
    today_iso = today.isoformat()
    rows = []
    for b in books:
        book_dir = os.path.join(state_dir, POOL_DIRS[b["pool"]], b["key"] if b["pool"] == "A" else "")
        pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
        unbooked_by_symbol = {d["symbol"]: d["unbooked"] for d in b.get("positions_detail", [])}
        for symbol, p in (pf.get("positions") or {}).items():
            if p.get("entry_date") == today_iso:
                bare = symbol.replace(".NS", "")
                rows.append({"time": "09:30", "action": "BUY", "symbol": bare,
                             "qty": int(p["quantity"]), "price": round(float(p["entry_price"]), 2),
                             "pool": POOL_LABELS[b["pool"]], "book": b["display_name"], "status": "Open",
                             "pnl": unbooked_by_symbol.get(bare), "note": "entry",
                             "cost": float(p["entry_price"]) * int(p["quantity"])})
        for t in _read_jsonl(os.path.join(book_dir, "trades.jsonl")):
            if t.get("exit_date") == today_iso:
                rows.append({"time": "09:30", "action": "SELL", "symbol": str(t.get("symbol", "")).replace(".NS", ""),
                             "qty": t.get("quantity"), "price": round(float(t.get("exit_price", 0) or 0), 2),
                             "pool": POOL_LABELS[b["pool"]], "book": b["display_name"], "status": "Closed",
                             "cost": float(t.get("entry_price", 0) or 0) * float(t.get("quantity", 0) or 0),
                             "pnl": round(float(t.get("pnl", 0) or 0), 2),
                             "note": (t.get("exit_reason") or t.get("reason") or "exit").replace("_", " ")})
    d_unbooked = {o["symbol"]: o["unbooked"] for o in (d_open or [])}
    for symbol, p in (d_pf.get("positions") or {}).items():
        ts = p.get("entry_timestamp", "")
        if ts.startswith(today_iso):
            rows.append({"time": ts[11:16], "action": "SELL" if p.get("direction") == "SELL" else "BUY",
                         "symbol": symbol, "qty": p.get("quantity"), "price": round(float(p["entry_price"]), 2),
                         "pool": "Pool D", "book": "Intraday", "status": "Open",
                         "pnl": d_unbooked.get(symbol), "note": "entry (open)",
                         "cost": float(p["entry_price"]) * float(p.get("quantity", 0) or 0)})
    for t in d_trades:
        if t.get("exit_date") != today_iso:
            continue
        opened, closed = t.get("entry_timestamp", ""), t.get("exit_timestamp", "")
        side_in = "SELL" if t.get("direction") == "SELL" else "BUY"
        # Pool D never holds overnight, so a trade closed today was also
        # opened today -- list the entry even if the record predates the
        # entry_timestamp field (blank time rather than a hidden fill).
        rows.append({"time": opened[11:16] if opened.startswith(today_iso) else "", "action": side_in,
                     "symbol": t.get("symbol"), "qty": t.get("quantity"),
                     "price": round(float(t.get("entry_price", 0) or 0), 2), "pool": "Pool D", "book": "Intraday",
                     "status": "Closed", "pnl": None, "note": "entry of a trade closed later today"})
        rows.append({"time": closed[11:16] if closed else "", "action": "BUY" if side_in == "SELL" else "SELL",
                     "symbol": t.get("symbol"), "qty": t.get("quantity"),
                     "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": "Pool D", "book": "Intraday",
                     "status": "Closed", "pnl": round(float(t.get("pnl", 0) or 0), 2),
                     "cost": float(t.get("entry_price", 0) or 0) * float(t.get("quantity", 0) or 0),
                     "note": str(t.get("reason", "")).replace("_", " ")})
    for r in rows:
        r["amount"] = round(float(r["price"] or 0) * int(r["qty"] or 0), 2)
        r["kind"] = "Intraday" if r["pool"] == "Pool D" else "Swing"
    # Pool E: the UTC daily close is 05:30 IST; prices in USDT, amounts in rupees.
    rate = float((pool_e or {}).get("usdinr") or 0)
    for b in (pool_e or {}).get("books", []):
        for p in b["open_positions"]:
            if p.get("entry_date") == today_iso:
                rows.append({"time": "05:30", "action": "BUY", "symbol": p["symbol"], "qty": p["quantity"],
                             "price": round(p["entry_price"], 2), "pool": "Pool E", "book": b["display_name"],
                             "status": "Open", "pnl": round(p["unbooked_post_tax"] * rate, 2),
                             "cost": p["entry_price"] * p["quantity"] * rate,
                             "note": "entry (USDT; P&L post-tax in Rs.)", "kind": "Crypto",
                             "amount": round(p["entry_price"] * p["quantity"] * rate, 2)})
        for t in b["recent_trades"]:
            if t.get("exit_date") == today_iso:
                qty = float(t.get("quantity", 0) or 0)
                rows.append({"time": "05:30", "action": "SELL", "symbol": t.get("symbol"), "qty": qty,
                             "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": "Pool E",
                             "book": b["display_name"], "status": "Closed",
                             "cost": float(t.get("entry_price", 0) or 0) * qty * rate,
                             "pnl": round(float(t.get("pnl", 0) or 0) * rate, 2),
                             "note": f"{str(t.get('exit_reason') or 'exit').replace('_', ' ')} (raw, USDT)",
                             "kind": "Crypto", "amount": round(float(t.get("exit_price", 0) or 0) * qty * rate, 2)})
    for r in rows:
        cost = float(r.pop("cost", 0) or 0)
        r["pct"] = round(float(r["pnl"]) / cost * 100, 2) if r.get("pnl") is not None and cost > 0 else None
    rows.sort(key=lambda r: (r["time"] or "99:99", r["symbol"] or ""))
    return rows
