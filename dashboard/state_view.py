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

from reporting.pool_summary import _book, _read_json, _read_jsonl, build_pool_summary  # noqa: F401

POOL_DIRS = {"A": "paper_trading", "A1": "paper_trading_legacy", "B": "portfolio_b", "C": "portfolio_c"}

# ----------------------------------------------------------------------------
# Static: the agent team and the pipelines. Kept as data so the page can
# draw them and highlight what ran today without hand-maintained HTML.
# ----------------------------------------------------------------------------
AGENTS = [
    {"id": "quant_researcher", "name": "Quant Researcher", "module": "research_lab/quant_researcher.py",
     "kind": "LLM", "program": "Intraday research lab",
     "role": "Proposes a batch of new intraday hypotheses, each with mechanism, rules and why it is distinct "
             "from everything already tried -- informed by the full experiment history and the established "
             "cross-experiment conclusions."},
    {"id": "research_director", "name": "Research Director", "module": "research_lab/research_director.py",
     "kind": "LLM + rules", "program": "Intraday research lab",
     "role": "Reviews the whole experiment history for cross-cutting lessons, hard-filters proposals against "
             "data the lab actually has, ranks the survivors, and runs the full experiment pipeline end to end."},
    {"id": "backtesting_engineer", "name": "Backtesting Engineer", "module": "research_lab/backtesting_engineer.py",
     "kind": "Mechanical", "program": "Intraday research lab",
     "role": "Simulates a strategy bar by bar on real 5-minute Kite data: stops before targets, mandatory "
             "square-off, no lookahead; computes every metric the Auditor judges."},
    {"id": "statistical_auditor", "name": "Statistical Auditor", "module": "research_lab/statistical_auditor.py",
     "kind": "Rules only", "program": "Both programs",
     "role": "The gate. PASS only if there are enough trades, enough walk-forward windows are positive, and "
             "the untouched out-of-sample holdout has positive expectancy. No LLM can override it."},
    {"id": "performance_analyst", "name": "Performance Analyst", "module": "research_lab/performance_analyst.py",
     "kind": "LLM", "program": "Both programs",
     "role": "Writes the plain-English narrative of why a verdict makes sense -- sector, regime and "
             "time-of-day breakdowns -- after the verdict is already decided."},
    {"id": "risk_manager_research", "name": "Risk Manager (research)", "module": "research_lab/risk_manager_research.py",
     "kind": "Rules only", "program": "Intraday research lab + Pool D",
     "role": "1% risk per trade, max 3 trades per symbol per day, 2% daily loss limit -- the same limits in "
             "the backtest and in Pool D's live ticks."},
    {"id": "knowledge_base", "name": "Knowledge Base", "module": "research_lab/knowledge_base.py",
     "kind": "Memory", "program": "Both programs",
     "role": "Every experiment's verdict and key reason, plus the established conclusions (e.g. the 15 bps "
             "minimum-edge rule) that future proposals are screened against."},
    {"id": "published_research_analyst", "name": "Published Research Analyst",
     "module": "swing_research/published_research_analyst.py", "kind": "Human-curated record",
     "program": "Swing research",
     "role": "For each literature-sourced swing strategy: the citation, the documented rules, the variant "
             "chosen, every scope reduction, and the estimated impact of each implementation assumption."},
    {"id": "swing_director", "name": "Swing Research Director", "module": "swing_research/research_director.py",
     "kind": "Mechanical + LLM narrative", "program": "Swing research",
     "role": "Runs a published strategy over the frozen Nifty 500 universe: continuous full-period run for "
             "the headline numbers, walk-forward windows for the Auditor, benchmarks, evidence-quality score."},
    {"id": "evidence_quality", "name": "Evidence Quality", "module": "swing_research/evidence_quality.py",
     "kind": "Rules only", "program": "Swing research",
     "role": "Outcome-blind 0-100 score of how much a result can be trusted: trade count, out-of-sample "
             "trade count, window count, data coverage."},
    {"id": "deployment_manager", "name": "Deployment Manager", "module": "deployment/deployment_manager.py",
     "kind": "Registry", "program": "Live",
     "role": "The strategy registry: permanent SW-IDs, research verdict, deployment status and its audit "
             "trail. Nothing runs live unless it is PAPER_TRADING here."},
    {"id": "paper_trading_engine", "name": "Paper Trading Engine", "module": "deployment/paper_trading_engine.py",
     "kind": "Mechanical", "program": "Pool A / A1",
     "role": "Once a day after the close: checks stops and targets, asks each strategy for exits and "
             "entries, queues entries for the next open, marks the book, writes the report."},
    {"id": "fundamental_agent", "name": "Fundamental Agent", "module": "fundamentals/fundamental_agent.py",
     "kind": "LLM", "program": "Portfolio B / C",
     "role": "Reads the company's fundamentals and returns a health assessment for a candidate."},
    {"id": "news_agent", "name": "News Agent", "module": "news/news_agent.py", "kind": "LLM", "program": "Portfolio B / C",
     "role": "Scans recent headlines for the candidate and returns sentiment and event risk."},
    {"id": "research_analyst", "name": "Research Analyst", "module": "research/", "kind": "LLM", "program": "Portfolio B / C",
     "role": "Combines the signal, fundamentals and news into a verdict on whether the setup is worth taking."},
    {"id": "portfolio_manager", "name": "Portfolio Manager", "module": "portfolio/", "kind": "LLM", "program": "Portfolio B / C",
     "role": "Decides which of today's approved candidates make the book and at what weight."},
    {"id": "risk_manager_live", "name": "Risk Manager (live)", "module": "risk/", "kind": "Rules", "program": "Portfolio B / C",
     "role": "Sizes every position against its stop and blocks anything that breaches the book's limits."},
    {"id": "pool_d_engine", "name": "Pool D Intraday Engine", "module": "pool_d/engine.py", "kind": "Mechanical",
     "program": "Pool D",
     "role": "Every 5 minutes during market hours: fetches today's bars for the Nifty 500, checks exits "
             "(catching stops hit on a missed poll), takes new signals on one shared Rs.1,00,000 book, "
             "squares off by 15:25."},
    {"id": "pool_summary", "name": "Daily Pool Summary", "module": "reporting/pool_summary.py", "kind": "Mechanical",
     "program": "Reporting",
     "role": "The one Telegram message a day: deployed, cash, unrealised and realised P&L per pool."},
]

# Two pipelines as node/edge graphs with grid positions (col, row) -- the page lays them out.
FLOWS = {
    "research": {
        "title": "How a strategy earns its place",
        "nodes": [
            {"id": "idea", "label": "Idea", "sub": "Literature search (swing) or Quant Researcher (intraday)", "col": 0, "row": 0},
            {"id": "record", "label": "Documented rules", "sub": "Published Research Analyst record / hypothesis", "col": 1, "row": 0},
            {"id": "build", "label": "Strategy code", "sub": "Rules as written, every assumption disclosed", "col": 2, "row": 0},
            {"id": "backtest", "label": "Backtest", "sub": "Real data, no lookahead, net of costs (intraday)", "col": 3, "row": 0},
            {"id": "walk", "label": "Walk-forward", "sub": "Sequential windows, last one held out", "col": 4, "row": 0},
            {"id": "audit", "label": "Statistical Auditor", "sub": "PASS / REJECT -- rules only", "col": 5, "row": 0},
            {"id": "narrative", "label": "Performance Analyst", "sub": "Explains the verdict", "col": 5, "row": 1},
            {"id": "kb", "label": "Knowledge Base", "sub": "Experiment record + conclusions", "col": 4, "row": 1},
            {"id": "promote", "label": "Promotion checklist", "sub": "Registry, catalog, VPS, first run", "col": 6, "row": 0},
            {"id": "poola", "label": "Pool A paper trading", "sub": "Rs.1,00,000 book per strategy", "col": 7, "row": 0},
        ],
        "edges": [["idea", "record"], ["record", "build"], ["build", "backtest"], ["backtest", "walk"],
                  ["walk", "audit"], ["audit", "narrative"], ["narrative", "kb"], ["kb", "idea"],
                  ["audit", "promote"], ["promote", "poola"]],
    },
    "daily": {
        "title": "A trading day (IST)",
        "nodes": [
            {"id": "prep", "label": "09:00 Pool D prepare", "sub": "90-day context for 457 names", "col": 0, "row": 0},
            {"id": "ticks", "label": "09:15-15:30 Pool D ticks", "sub": "Every 5 min: exits, entries, square-off 15:25", "col": 1, "row": 0},
            {"id": "open", "label": "09:30 Fill at open", "sub": "Yesterday's queued entries/exits: Pool A, A1, B, C", "col": 1, "row": 1},
            {"id": "eod_a", "label": "15:35 Pool A", "sub": "Stops, exits, new signals queued", "col": 2, "row": 0},
            {"id": "eod_a1", "label": "15:40 Pool A1", "sub": "Exits only (wind-down)", "col": 2, "row": 1},
            {"id": "eod_c", "label": "15:45 Portfolio C", "sub": "Anchor signals -> agent stack", "col": 3, "row": 0},
            {"id": "eod_b", "label": "15:50 Portfolio B", "sub": "Watchlist -> agent stack", "col": 3, "row": 1},
            {"id": "summary", "label": "16:05 Telegram", "sub": "The one message of the day", "col": 4, "row": 0},
        ],
        "edges": [["prep", "ticks"], ["ticks", "eod_a"], ["open", "eod_a"], ["eod_a", "eod_a1"], ["eod_a", "eod_c"],
                  ["eod_a1", "eod_b"], ["eod_c", "summary"], ["eod_b", "summary"]],
    },
}

# Cron jobs as the page's schedule strip. (hour, minute) in IST; "every5" spans a window.
SCHEDULE = [
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


def build_dashboard_state(state_dir: str, logs_dir: str, registry_records: list, prices: dict,
                          prices_as_of: Optional[str], now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    today = now.date()
    active = {r.strategy_key: r.display_name for r in registry_records
              if str(getattr(r.deployment_status, "value", r.deployment_status)).endswith("PAPER_TRADING")}
    summary = build_pool_summary(state_dir, active, lambda symbols: {s: prices[s] for s in symbols if s in prices},
                                 today=today)

    books = []
    for pool, dirname in POOL_DIRS.items():
        if pool in ("A", "A1"):
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
    pool_d = {
        **summary["pool_d"],
        "starting_capital": d_pf.get("starting_capital"),
        "open_positions": [{"symbol": s, **{k: p.get(k) for k in ("direction", "entry_price", "stop_loss", "target",
                                                                    "quantity", "entry_timestamp")}}
                           for s, p in (d_pf.get("positions") or {}).items()],
        "todays_trades": [t for t in d_trades if t.get("exit_date") == today.isoformat()],
        "recent_trades": list(reversed(d_trades[-15:])),
        "symbols_with_context": len(d_pf.get("context_by_symbol") or {}),
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
    return {
        "generated_at": now.isoformat(timespec="seconds"), "today": today.isoformat(), "market_open": market_open,
        "prices_as_of": prices_as_of, "priced_symbols": len(prices),
        "pools": summary["pools"], "overall": summary["overall"], "books": books, "pool_d": pool_d,
        "schedule": schedule, "registry": registry, "agents": AGENTS, "flows": FLOWS,
    }
