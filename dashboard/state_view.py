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

import functools
import glob
import json
import os
from datetime import date, datetime, time as dtime
from typing import Callable, Optional

from deployment.base import is_crypto_record, is_pool_a_record
from reporting.pool_summary import _book, _read_json, _read_jsonl, build_pool_summary  # noqa: F401

# Pool A1 (the legacy wind-down books) is deliberately absent from the
# dashboard, per explicit direction 2026-09-11 -- it stays in the daily
# Telegram summary only.
POOL_DIRS = {"A": "paper_trading", "B": "portfolio_b", "C": "portfolio_c", "F": "pool_f"}
POOL_LABELS = {"A": "Pool A", "B": "Pool B", "C": "Pool C", "D": "Pool D", "E": "Pool E", "F": "Pool F", "E1": "Pool E1"}

# ----------------------------------------------------------------------------
# Static: the agent team and the pipelines. Kept as data so the page can
# draw them and highlight what ran today without hand-maintained HTML.
# ----------------------------------------------------------------------------
# One plain line per pool, shown on the Team tab.
POOLS_INFO = [
    {"pool": "A", "name": "Swing strategies", "text": "Each researched swing strategy trades its own ₹1,00,000 paper book on NSE stocks, holding for days to weeks."},
    {"pool": "B", "name": "AI watchlist", "text": "Stocks from your watchlist; the AI team debates each one and decides whether and how much to buy."},
    {"pool": "C", "name": "AI overlay", "text": "The AI team reviews the signals Pool A's strategies produce each day and only takes the ones it agrees with."},
    {"pool": "D", "name": "Intraday", "text": "One shared book that bets on stretched stocks snapping back within the day, everything closed by 15:25."},
    {"pool": "E", "name": "Crypto trends", "text": "Rule-based trend following on BTC, ETH, BNB, XRP and SOL, one 1,000 USDT book per strategy. Profit on Live day and Strategies is gross, before fees and tax, like every pool; the P&L tab shows fees, tax and net."},
    {"pool": "E1", "name": "Pool E with partial profit booking", "text": "The same crypto strategies as Pool E on fresh 1,000 USDT books, but at +5% half is sold and the stop on the rest moves to entry."},
    {"pool": "F", "name": "Pool A with partial profit booking", "text": "The same strategies as Pool A on fresh books, but at +5% half is sold and the stop on the rest moves to entry."},
    {"pool": "G", "name": "AI crypto judgment", "text": "The AI calls buy, sell or hold on the same five coins twice a day, with a fixed 18% stop; no backtest, judged live. Profit on Live day and Strategies is gross, before fees and tax; the P&L tab shows fees, tax and net."},
]


DESKS = [
    {"id": "research_feeder", "name": "Research Feeder (every lane)", "icon": "\U0001F9ED",
     "blurb": "Where every strategy starts, whatever pool it's headed for: one ranked queue across swing, "
              "intraday, medium, long-term and crypto, one candidate in research at a time, implemented and "
              "backtested for real, with new candidates found every month."},
    {"id": "research_lab", "name": "Intraday Research Lab (feeds Pool D)", "icon": "\U0001F52C",
     "blurb": "Dreams up intraday ideas and tests them to destruction on real 5-minute data -- still where an "
              "intraday candidate is actually built today, since the Feeder's cloud routine has no live "
              "broker session to backtest against."},
    {"id": "swing_research", "name": "Swing Research (feeds Pool A)", "icon": "\U0001F4DA",
     "blurb": "The backtest machinery every literature-sourced candidate runs through -- swing, medium or "
              "long-term -- whether a human is at the keyboard or the Feeder's Strategy Implementer is."},
    {"id": "trading_desk", "name": "Trading Desk (Pools A, D and F)", "icon": "\U0001F4C8",
     "blurb": "Runs every approved strategy as a paper book, day after day."},
    {"id": "portfolio_team", "name": "Portfolio Team (Pools B and C)", "icon": "\U0001F9E0",
     "blurb": "AI analysts who debate each candidate the way a small fund's team would."},
    {"id": "crypto_desk", "name": "Crypto Desk (Pools E, E1 and G)", "icon": "\u20BF",
     "blurb": "Tests published crypto rules on Binance history, judged only after fees and India's 31.2% tax, "
              "and runs the survivors as a 1,000 USDT paper book -- Pool E1 runs the same books again with "
              "partial profit booking, exactly Pool F's relationship to Pool A."},
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
     "job": "Runs the Pool A and Pool F books after the close", "status_from": "eod_a",
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
     "job": "Runs Pool E and Pool E1 after the 00:00 UTC close", "status_from": "pool_e",
     "detail": "Same paper engine as Pool A on a 1,000 USDT book: month-end decisions from the strategy, 20% "
               "stops checked daily, fractional coins, fills at the close it just saw (crypto never closes) -- "
               "Pool E1 runs the same books again on their own fresh capital, with partial profit booking on top."},
    {"id": "crypto_judge", "avatar": {"type": "robot", "body": "#8A5A9E", "eye": "#F2C14E", "shape": "round"}, "name": "Crypto Judge", "icon": "\U0001F52E", "desk": "crypto_desk", "kind": "AI",
     "job": "Calls BUY/SELL/HOLD on the majors, twice a day", "status_from": "pool_g",
     "detail": "No backtest behind this one -- a live judgment call on BTC, ETH, BNB, XRP and SOL each run. A "
               "mechanical 18% stop protects every position regardless of what the model says; the model is "
               "told the tax cost of flipping a winning position and asked to avoid pointless churn."},
    {"id": "pool_summary", "avatar": {"type": "robot", "body": "#7D6B8A", "eye": "#5FB7C0", "shape": "square"}, "name": "Bookkeeper", "icon": "\U0001F9FE", "desk": "reporting", "kind": "Mechanical",
     "job": "Sends the daily Telegram", "status_from": "summary",
     "detail": "Adds up deployed capital, cash, unrealised and realised P&L for every pool and sends the one "
               "message of the day at 16:05."},
    {"id": "head_of_research", "avatar": {"type": "robot", "body": "#3B7A57", "eye": "#F2C14E", "shape": "square"}, "name": "Head of Research", "icon": "\U0001F4CA", "desk": "research_feeder", "kind": "Rules only",
     "job": "Ranks candidates and keeps the queue", "status_from": "research_queue",
     "detail": "Scores every candidate 0-10 on evidence, data available, feasibility, diversification vs. "
               "what's already running, robustness, simplicity and research value, across every lane. Keeps "
               "exactly one candidate queued at a time -- free to swap it for a better-ranked one, or one "
               "picked by hand, right up until research actually starts; once it has, the queue is locked "
               "until that candidate is resolved."},
    {"id": "research_routine", "avatar": {"type": "robot", "body": "#8A5A9E", "eye": "#4CC383", "shape": "round"}, "name": "Strategy Implementer", "icon": "\U0001F9EA", "desk": "research_feeder", "kind": "AI",
     "job": "Implements and backtests whatever's queued, every Sunday", "status_from": None,
     "detail": "An unattended agent, not a local script: reads the queue, implements the candidate as real "
               "code following the pattern of whichever existing strategies are closest to it, runs the real "
               "backtest, and opens a pull request with the verdict -- pass or reject, whatever the numbers "
               "say. Never merges anything itself, never touches the registry or a live pool; promotion is "
               "always a separate, manual step. Swing, medium, long-term and crypto only -- intraday needs a "
               "live broker session it doesn't have."},
    {"id": "discovery_routine", "avatar": {"type": "robot", "body": "#B8860B", "eye": "#5FB7C0", "shape": "round"}, "name": "Discovery Scout", "icon": "\U0001F50D", "desk": "research_feeder", "kind": "AI",
     "job": "Searches for new candidates, once a month", "status_from": None,
     "detail": "Also unattended: reads what's already tracked, then searches for genuinely new strategies "
               "with real, verifiable sources -- a peer-reviewed paper or a well-known trading book, never an "
               "invented idea or a blog. Adds 1-5 a month, honestly scored, weaknesses included -- or none at "
               "all, some months, rather than pad the list. Opens a pull request; never implements anything."},
]

# Two stories told as strips of steps with icons; the page draws them.
FLOWS = {
    "daily": {
        "title": "A trading day",
        "steps": [
            {"id": "pool_e", "icon": "\u20BF", "label": "05:45", "text": "Crypto Trader marks Pool E after the UTC close (every day)"},
            {"id": "pool_e1", "icon": "\u20BF", "label": "05:50", "text": "Pool E1 -- Pool E's partial-booking twin -- marks its own book"},
            {"id": "pool_g", "icon": "\U0001F52E", "label": "08:30 & 20:30", "text": "Crypto Judge calls BUY/SELL/HOLD on the majors, twice a day"},
            {"id": "prep", "icon": "\U0001F305", "label": "09:00", "text": "Intraday Trader studies 90 days of history for 457 stocks"},
            {"id": "open", "icon": "\U0001F514", "label": "09:30", "text": "Yesterday's queued swing orders fill at the open"},
            {"id": "ticks", "icon": "\u26A1", "label": "09:15-15:30", "text": "Pool D checks every stock every 5 minutes"},
            {"id": "eod_a", "icon": "\U0001F4BC", "label": "15:35", "text": "Swing Trader closes what needs closing, queues new entries"},
            {"id": "eod_f", "icon": "\U0001F4BC", "label": "15:38", "text": "Pool F -- Pool A's partial-booking twin -- marks its own book"},
            {"id": "eod_a1", "icon": "\U0001F4BC", "label": "15:40", "text": "Pool A1 (legacy) marks its own book"},
            {"id": "eod_c", "icon": "\U0001F9E0", "label": "15:45", "text": "Portfolio B & C team debates today's candidates"},
            {"id": "eod_b", "icon": "\U0001F9E0", "label": "15:50", "text": "Portfolio B marks its own book"},
            {"id": "summary", "icon": "\U0001F4E8", "label": "16:05", "text": "Bookkeeper sends the one Telegram message"},
        ],
    },
    "research": {
        "title": "How a strategy earns its place",
        "steps": [
            {"id": "found", "icon": "\U0001F50D", "label": "Found", "text": "Discovery Scout adds real, sourced candidates every month -- a paper or a well-known book, never an invented idea"},
            {"id": "ranked", "icon": "\U0001F4CA", "label": "Ranked", "text": "Head of Research scores every candidate 0-10, across swing, intraday, medium, long-term and crypto alike"},
            {"id": "queued", "icon": "\U0001F5C2\ufe0f", "label": "Queued", "text": "One at a time -- free to swap for a better-ranked one, or a specific pick, until research actually starts"},
            {"id": "locked", "icon": "\U0001F512", "label": "Locked", "text": "Strategy Implementer claims it the instant it starts; nothing can bump it after that"},
            {"id": "backtest", "icon": "\u2699\ufe0f", "label": "Backtest", "text": "Years of real data, no peeking ahead"},
            {"id": "audit", "icon": "\u2696\ufe0f", "label": "Audit", "text": "Statistical Auditor: PASS or REJECT, rules only (crypto: after fees and tax)"},
            {"id": "pr", "icon": "\U0001F500", "label": "Pull request", "text": "The real verdict either way -- pass or reject -- never merged by the routine itself"},
            {"id": "promote", "icon": "\U0001F4CB", "label": "Register & trade", "text": "A human reviews and merges; only then an SW-ID, a paper book, and it runs live, watched every day"},
        ],
    },
}


# Plain-language briefs for the Strategies tab (one per registry key, plus
# the three books that are not registry strategies). type: Swing / Intraday /
# Crypto / AI.
STRATEGY_BRIEFS = {
    "turtle_system2": ("Swing", "Buys a stock when it breaks above its highest price of the last 55 days and rides the trend, adding on the way up; sells when it drops below its 20-day low. The classic 1980s trend-following system."),
    "minervini_trend_template_filter": ("Swing", "Only buys stocks in a strong uptrend: price above its rising 50, 150 and 200-day averages, well off its 52-week low, near its 52-week high, and stronger than most of the market."),
    "fifty_two_week_high_momentum": ("Swing", "Buys stocks trading close to their 52-week high, on the idea that people are slow to push a stock to a new high, so it keeps drifting up. Retired after failing its tests."),
    "ma_crossover": ("Swing", "Buys when a short moving average crosses above a long one and sells on the reverse cross. A basic trend rule, kept as a benchmark."),
    "mean_reversion": ("Swing", "Buys stocks that have fallen well below their recent average and sells when they bounce back to it. Kept as a benchmark."),
    "cross_sectional_momentum": ("Swing", "Every month, buys the stocks that rose the most over the last six months and holds them for a month. Winners tend to keep winning for a while."),
    "pead": ("Swing", "After a company reports better-than-expected results, buys it and holds for about two months, because prices keep drifting up for weeks after good news."),
    "short_term_reversal": ("Swing", "Buys the stocks that fell the most over the last month and holds them a month. Short sharp drops tend to bounce back."),
    "betting_against_beta": ("Swing", "Buys the calmest, least market-sensitive stocks. The idea is that investors overpay for exciting stocks and underpay for boring ones."),
    "amihud_illiquidity": ("Swing", "Buys stocks that are hard to trade in size, because investors demand extra return for that inconvenience."),
    "turnover_liquidity": ("Swing", "Buys stocks where only a small fraction of shares actually change hands each month, a second way of measuring the same 'hard to trade' idea as Amihud. Rejected: lost money on data it hadn't seen before."),
    "max_effect": ("Swing", "Avoids lottery-like stocks: buys the ones with the smallest single-day jumps over the last month. Gamblers overpay for big-jump stocks, leaving the calm ones cheap."),
    "idiosyncratic_volatility": ("Swing", "Buys stocks whose own ups and downs, apart from the market's, are the smallest."),
    "turn_of_month": ("Swing", "Buys a few days before the end of each month and sells a few days into the next, when salary and fund money flows into the market."),
    "ma_pullback": ("Swing", "Waits for a stock in an uptrend to dip back to its moving average, then buys the dip."),
    "volume_backed_breakout": ("Swing", "Buys a stock breaking out to a new high on much higher-than-usual volume, a sign that big buyers are behind the move."),
    "overnight_return_anomaly": ("Swing", "Buys at the close and sells at the next open, in stocks that have been gaining overnight. Most of some stocks' gains happen while the market is shut."),
    "high_volume_return_premium": ("Swing", "Buys stocks that just had an unusually busy trading day or week and holds a month. A burst of attention brings in new buyers over the following weeks."),
    "earnings_announcement_premium": ("Swing", "Buys stocks a month before their scheduled results and holds through the announcement, because attention and buying build up around results day."),
    "crypto_xs_momentum": ("Crypto", "Each week, buys the coins that rose most over the last three weeks. Rejected: after fees and India's crypto tax it lost money."),
    "crypto_trend_timing": ("Crypto", "At each month-end, holds a coin only if its price is above its 10-month average, otherwise stays in cash. Aims to skip the deep crypto crashes."),
    "crypto_trend_timing_weekly": ("Crypto", "The same rule as Crypto Trend Timing, checked every week instead of every month, so it trades more often. Built for a more active crypto book."),
    "crypto_trend_timing_daily": ("Crypto", "The same rule as Crypto Trend Timing, checked every single day -- the fastest cadence tested. Higher return than the monthly and weekly versions after tax, but a rougher ride: only about 1 in 5 trades wins, and it only comes out ahead because the winners run far."),
    "crypto_tsmom": ("Crypto", "At each month-end, holds a coin only if it is higher than a year ago, otherwise cash. A slower cousin of the moving-average rule."),
    "downside_beta": ("Swing", "Buys the stocks that fall the hardest when the market falls, because investors demand a premium to hold them. Passed its test but earned less than the index, so not promoted."),
    "crypto_vol_managed": ("Crypto", "Always holds the big coins but holds less after a volatile month and more after a calm one. Rejected: monthly re-sizing triggers India's tax on every profitable month."),
    "nifty_low_volatility_30": ("Swing", "Buys the calmest stocks by one-year price swings and holds six months, the way NSE's Low Volatility 30 index does. Rejected: stopped working in 2025-26."),
    "portfolio_b": ("AI", "Pool B: your watchlist. The AI team (fundamentals, news, research analyst, portfolio manager, risk manager) debates each stock you add and decides whether and how much to buy."),
    "portfolio_c": ("AI", "Pool C: the AI team reviews the signals Pool A's strategies produce each day and picks the ones it agrees with, sized by the risk manager."),
    "pool_f": ("Swing", "Pool F: the same strategies as Pool A on their own books, with one addition -- when a position is up 5% at any point in the day, half is sold there and the stop on the rest is raised to the entry price. Runs side by side with Pool A so the two can be compared."),
    "pool_e1": ("Crypto", "Pool E1: the same crypto strategies as Pool E on their own 1,000 USDT books, with one addition -- when a position is up 5% at any point in the day, half is sold there and the stop on the rest is raised to the entry price. Runs side by side with Pool E so the two can be compared."),
    "portfolio_g": ("Crypto", "Pool G: an AI judgment call on Bitcoin, Ethereum, BNB, XRP and Solana, twice a day. No backtest -- it is judged on its live paper record. A mechanical stop protects every position; the model decides entries and exits itself, and is told to avoid flipping a winning position just to bank a small taxable gain."),
    "pool_d_vwap_fade": ("Intraday", "Pool D: when a stock stretches unusually far from its day's average price and then stalls, bets on it snapping back; everything is squared off by 15:25. A known-reject rule kept running to test the intraday machinery."),
}


# How each strategy actually trades, in plain words: what it buys, when it
# sells, how it is sized, and where the rule comes from.
STRATEGY_HOW = {
    "turtle_system2": {"entry": "Buy when today's price is the highest of the last 55 trading days; add up to four times as it keeps rising.", "exit": "Sell when the price falls to its lowest of the last 20 days, or at the stop.", "risk": "Position size set so a normal day's move risks about 1% of the book; stop two average daily ranges below entry.", "source": "Dennis and Eckhardt's Turtle rules (1983), as published by Curtis Faith."},
    "minervini_trend_template_filter": {"entry": "Buy only stocks meeting all eight trend checks (rising 50/150/200-day averages in the right order, at least 30% above the 52-week low, within 25% of the 52-week high, stronger than 70% of the market), on the day they first qualify.", "exit": "Sell when the stock stops meeting the template, or at the 8% stop.", "risk": "1% of the book risked per position against an 8% stop; at most 10 positions.", "source": "Mark Minervini, Trade Like a Stock Market Wizard (2013)."},
    "fifty_two_week_high_momentum": {"entry": "Buy the stocks closest to their 52-week high (top decile), on the day they enter it.", "exit": "Hold about six months, or the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "George and Hwang (2004), Journal of Finance."},
    "ma_crossover": {"entry": "Buy when the 20-day average crosses above the 50-day average.", "exit": "Sell on the reverse cross or at the stop.", "risk": "Production risk settings: 1% per trade.", "source": "Classic technical rule; kept as a benchmark."},
    "mean_reversion": {"entry": "Buy when the price drops two standard deviations below its 20-day average.", "exit": "Sell when it returns to the average, or at the stop.", "risk": "Production risk settings: 1% per trade.", "source": "Classic technical rule; kept as a benchmark."},
    "cross_sectional_momentum": {"entry": "Rank every stock by its return over the past six months; buy the top decile on the day a stock enters it.", "exit": "Sell after one month (single vintage), or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions ranked by momentum.", "source": "Jegadeesh and Titman (1993), Journal of Finance."},
    "pead": {"entry": "Buy on the day after a company reports results that beat expectations by a wide margin.", "exit": "Sell about 60 trading days later, or at the stop.", "risk": "1% risk per position, 8% stop.", "source": "Bernard and Thomas (1989); forward-evidence experiment, no historical backtest."},
    "short_term_reversal": {"entry": "Rank every stock by its return over the past month; buy the bottom decile on the day a stock enters it.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "Jegadeesh (1990), Journal of Finance."},
    "betting_against_beta": {"entry": "Rank stocks by how much they move with the market (beta); buy the least sensitive decile.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop.", "source": "Frazzini and Pedersen (2014), Journal of Financial Economics."},
    "amihud_illiquidity": {"entry": "Rank stocks by how much price moves per rupee traded; buy the most illiquid decile.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop; realistic fill costs modelled.", "source": "Amihud (2002), Journal of Financial Markets."},
    "turnover_liquidity": {"entry": "Rank stocks by their trailing 1-month average turnover (shares traded / shares outstanding); buy the bottom decile (least traded), on the day a stock enters it.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop; shares outstanding is today's count applied across history, disclosed limitation.", "source": "Datar, Naik and Radcliffe (1998), Journal of Financial Markets."},
    "max_effect": {"entry": "Rank stocks by their single biggest daily gain over the past month; buy the decile with the smallest, on the day a stock enters it.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "Bali, Cakici and Whitelaw (2011), Journal of Financial Economics."},
    "idiosyncratic_volatility": {"entry": "Rank stocks by the volatility left after removing the market's moves; buy the calmest decile.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop.", "source": "Ang, Hodrick, Xing and Zhang (2006), Journal of Finance."},
    "turn_of_month": {"entry": "Buy at the close of the last trading day of the month (the strongest stocks by recent return).", "exit": "Sell at the close of the third trading day of the new month.", "risk": "1% risk per position, 8% stop.", "source": "Lakonishok and Smidt (1988); Ariel (1987)."},
    "ma_pullback": {"entry": "In a stock above its 50-day average, buy when the price dips to touch the average and closes back above it.", "exit": "Sell on a close below the average, or at the 8% stop.", "risk": "1% risk per position, 8% stop.", "source": "Ported from the earlier production strategy; informally backtested."},
    "volume_backed_breakout": {"entry": "Buy when a stock closes at a new 20-day high on at least twice its normal volume.", "exit": "Sell on a close below the 10-day low, or at the 8% stop.", "risk": "1% risk per position, 8% stop.", "source": "Ported from the earlier production strategy; informally backtested."},
    "overnight_return_anomaly": {"entry": "Rank stocks by their overnight (close-to-open) gains over the past month; buy the top decile at the close.", "exit": "Sell at the next morning's open.", "risk": "1% risk per position; fills at close and open, not at the same close.", "source": "Lou, Polk and Skouras (2019), Journal of Financial Economics."},
    "high_volume_return_premium": {"entry": "Rank stocks by last week's volume against their usual volume; buy the top decile on the day a stock enters it.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "Gervais, Kaniel and Mingelgrin (2001), Journal of Finance."},
    "earnings_announcement_premium": {"entry": "On the last trading day of the month, buy stocks that reported results in the same month last year (expected announcers), ranked by how much of their volume clusters around results.", "exit": "Sell at the last trading day of the following month, or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "Frazzini and Lamont (2007); Barber et al. (2013)."},
    "crypto_xs_momentum": {"entry": "Each Monday, rank 40 large coins by their 3-week return; buy the top fifth.", "exit": "Sell the following Monday, or at the 20% stop.", "risk": "2.5% risk per coin against a 20% stop; fees 0.30% a side; India's 31.2% tax on each profitable trade.", "source": "Liu, Tsyvinski and Wu (2022), Journal of Finance."},
    "crypto_trend_timing": {"entry": "At each month-end, buy a coin (BTC, ETH, BNB, XRP, SOL) whose close is above the average of its last 10 month-end closes.", "exit": "Sell at a month-end when the close is below that average, or at the 20% stop.", "risk": "Equal 20% sleeve per coin; fees and 31.2% tax applied before the verdict.", "source": "Faber (2007), Journal of Wealth Management."},
    "crypto_trend_timing_weekly": {"entry": "Every week (Sunday, UTC), buy a coin whose close is above its 300-day average.", "exit": "Sell on a week-end close below that average, or at the 20% stop.", "risk": "Equal 20% sleeve per coin; fees and 31.2% tax applied before the verdict.", "source": "Faber (2007) -- this program's own weekly-cadence variant of Crypto Trend Timing, 2026-09-17."},
    "crypto_trend_timing_daily": {"entry": "Every day, buy a coin whose close is above its 300-day average.", "exit": "Sell on a daily close below that average, or at the 20% stop.", "risk": "Equal 20% sleeve per coin; fees and 31.2% tax applied before the verdict. Win rate only about 18% on the tested period -- most exits are small losses, and the edge comes from the few winners running far.", "source": "Faber (2007) -- this program's own daily-cadence variant of Crypto Trend Timing, 2026-09-17, completing the monthly/weekly/daily set."},
    "crypto_tsmom": {"entry": "At each month-end, buy a coin that is higher than it was 12 months ago.", "exit": "Sell at a month-end when it is lower than 12 months ago, or at the 20% stop.", "risk": "Equal 20% sleeve per coin; fees and 31.2% tax applied before the verdict.", "source": "Moskowitz, Ooi and Pedersen (2012), Journal of Financial Economics."},
    "downside_beta": {"entry": "Rank stocks by how hard they fall on the market's down days over the past year; buy the top fifth on the day a stock enters it.", "exit": "Sell after one month, or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "Ang, Chen and Xing (2006), Review of Financial Studies."},
    "crypto_vol_managed": {"entry": "Always long the five majors; at each month-end, size each sleeve by min(1, (60% / last month's volatility)^2).", "exit": "Re-size at month-end when the weight moves more than 10%; 20% stop.", "risk": "Monthly re-sizing books a taxable gain every profitable month.", "source": "Moreira and Muir (2017), Journal of Finance."},
    "nifty_low_volatility_30": {"entry": "Rank stocks by one-year daily volatility; buy the calmest decile on the day a stock enters it.", "exit": "Sell after six months, or at the 8% stop.", "risk": "1% risk per position, 8% stop, 10 positions.", "source": "NSE Nifty100 Low Volatility 30 methodology; Baker, Bradley and Wurgler (2011)."},
    "portfolio_b": {"entry": "You add a stock to the watchlist on Telegram; the Fundamentals, News and Research analysts each grade it; the Portfolio Manager decides whether to buy and how much; the Risk Manager sizes it against its stop.", "exit": "The team reviews holdings daily and sells on an unfavourable verdict, or at the stop.", "risk": "Risk Manager: 1% risk per position, book-level limits.", "source": "This program's own AI team; no published paper."},
    "portfolio_c": {"entry": "Each day the AI team looks at every entry signal Pool A's strategies produced and buys the ones it agrees with.", "exit": "Follows the originating strategy's exit, the team's verdict, or the stop.", "risk": "Risk Manager sizing, 1% per position.", "source": "This program's own AI team; no published paper."},
    "pool_f": {"entry": "Exactly as the Pool A strategy it mirrors: same signals, same universe, same data, same fills.", "exit": "The moment a position is up 5% during the day, half is sold at that level and the stop on the rest moves to the entry price; the rest then exits on the strategy's own rule or at the stop.", "risk": "Same 1% risk sizing and 8% initial stop as Pool A; the raised stop applies from the next day.", "source": "This program's own experiment (2026-09-16); the 5% / half / stop-to-entry numbers are a disclosed a-priori choice."},
    "pool_e1": {"entry": "Exactly as the Pool E strategy it mirrors: same coins, same signal, same fill timing.", "exit": "The moment a position's day High is up 5%, half is sold at that level and the stop on the rest moves to the entry price; the rest then exits on the strategy's own rule or at the stop.", "risk": "Same sizing as Pool E, on its own fresh 1,000 USDT book.", "source": "This program's own experiment (2026-09-22), the same disclosed a-priori 5% / half / stop-to-entry numbers as Pool F."},
    "portfolio_g": {"entry": "The model is shown price, 1/7/30-day change and distance from the 300-day average for each coin, twice a day, and calls BUY/SELL/HOLD/AVOID with a one-line reason each time.", "exit": "The model can say SELL any time its view changes; independently, a mechanical 18% stop (set at entry, never moved by the model) closes a position immediately if it is touched, before the model is even consulted.", "risk": "At most 25% of the 1,000 USDT book per coin; no minimum holding period, but the model is told each realised gain costs 31.2% tax with no relief for losses, so it is instructed against flipping a winner just to bank it.", "source": "This program's own live experiment (2026-09-17) -- no published paper; a real-time test of whether an LLM's judgment beats the researched trend rules."},
    "pool_d_vwap_fade": {"entry": "During the day, when a stock has stretched unusually far from its volume-weighted average price and stalls, sell (or buy) it expecting a snap back.", "exit": "Target at the average price, tight stop, or the 15:25 square-off.", "risk": "1% risk per trade on one shared Rs.1,00,000 book, at most 25% of the book per name, 3 trades a day per stock, 2% daily loss limit.", "source": "Proposed by the Quant Researcher; rejected in EXP-008 and kept as a framework test."},
}


def _experiment_summary(exp_id: str) -> dict:
    """The headline numbers of a strategy's primary experiment, if its
    folder is on disk (swing experiments first, then the intraday lab)."""
    if not exp_id:
        return {}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub in ("swing_research", "research_lab"):
        path = os.path.join(here, sub, "experiments", exp_id, "metrics.json")
        if os.path.exists(path):
            m = _read_json(path) or {}
            out = {k: m.get(k) for k in ("total_trades", "cagr", "sharpe_ratio", "max_drawdown_pct", "win_rate",
                                         "avg_holding_period_days", "total_pnl")}
            eq = m.get("evidence_quality") or {}
            out["evidence"] = f"{eq.get('label', '')} {eq.get('score', '')}".strip()
            if "pre_tax" in m:
                pre = m["pre_tax"].get("full_period", {})
                out["pre_tax"] = {k: pre.get(k) for k in ("cagr", "total_pnl", "max_drawdown_pct")}
                out["book_currency"] = m.get("book_currency", "USDT")
            return out
    return {}


def _paper_trading_started(r) -> Optional[str]:
    """The date this strategy MOST RECENTLY moved into PAPER_TRADING, from
    the registry's own deployment_status_history (see
    deployment.deployment_manager.set_deployment_status()) -- None if it
    has never been through that transition (never deployed, or a
    hand-edited registry entry that skipped the normal call)."""
    history = getattr(r, "deployment_status_history", None) or []
    stamps = [h.get("timestamp") for h in history if h.get("to_status") == "PAPER_TRADING" and h.get("timestamp")]
    return date.fromtimestamp(max(stamps)).isoformat() if stamps else None


def _wins(trades: list) -> int:
    """Closed trades that made money."""
    return sum(1 for t in trades if float(t.get("pnl", 0) or 0) > 0)


def _book_started(book_dir: str, trades: list) -> Optional[str]:
    """The day THIS BOOK began running, i.e. began looking for trades: the first line of its own daily_equity.jsonl
    (written on every run, trade or no trade). A strategy can go days without finding its first trade, so the
    earliest closed trade / open position is only the fallback for a book that has no equity log. None if neither
    exists."""
    dates = [t.get("entry_date") for t in trades if t.get("entry_date")]
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    dates += [p.get("entry_date") for p in (pf.get("positions") or {}).values() if p.get("entry_date")]
    try:
        with open(os.path.join(book_dir, "daily_equity.jsonl"), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    dates.append(json.loads(line).get("date"))
                    break
    except (OSError, ValueError):
        pass
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def _strategy_pool_breakdown(key: str, books: list, state_dir: str, d_trades: list, pool_d: dict,
                             pool_e: dict, pool_g: dict, pool_e1: Optional[dict] = None) -> list:
    """One entry per pool this strategy actually runs in -- {"pool":
    "Pool A", "capital", "pnl", "closed_trades", "started"}, all in
    rupees. Usually a single entry; a strategy with both a Pool A and a
    Pool F book (the same strategy on two books) gets two, which the
    Strategies tab shows combined (summed) by default and can expand to
    show separately. Every book's trades.jsonl is read in full here (not
    the 10-15-row "recent trades" list shown elsewhere), since a
    closed-trade count needs every trade, not just the latest few.
    "started" is THIS BOOK's own earliest activity date, not the
    registry's strategy-level PAPER_TRADING date (which only reflects
    whichever pool the strategy was FIRST deployed to -- a twin book
    added later, e.g. Pool F built well after Pool A, would otherwise
    wrongly show Pool A's own start date)."""
    out = []
    for b in books:
        if b.get("key") != key:
            continue
        book_dir = os.path.join(state_dir, POOL_DIRS[b["pool"]], key) if b["pool"] in ("A", "F") \
            else os.path.join(state_dir, POOL_DIRS[b["pool"]])
        trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
        out.append({"pool": POOL_LABELS.get(b["pool"], "Pool " + b["pool"]),
                    "capital": round(b.get("capital", 0) or 0, 2),
                    "pnl": round((b.get("realised", 0) or 0) + (b.get("unrealised", 0) or 0), 2),
                    "closed_trades": len(trades), "wins": _wins(trades), "started": _book_started(book_dir, trades)})
    if key == "pool_d_vwap_fade" and pool_d.get("capital") is not None:
        book_dir = os.path.join(state_dir, "pool_d")
        out.append({"pool": "Pool D", "capital": round(pool_d["capital"], 2),
                    "pnl": round((pool_d.get("realised", 0) or 0) + (pool_d.get("unrealised", 0) or 0), 2),
                    "closed_trades": len(d_trades), "wins": _wins(d_trades), "started": _book_started(book_dir, d_trades)})
    if key == "portfolio_g" and pool_g.get("exists"):
        rate = pool_g.get("usdinr") or 0
        book_dir = os.path.join(state_dir, "pool_g")
        trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
        out.append({"pool": "Pool G", "capital": round(pool_g["capital"] * rate, 2),
                    "pnl": round((pool_g["booked"]["raw"] + pool_g["unbooked"]["raw"]) * rate, 2),
                    "closed_trades": len(trades), "wins": _wins(trades), "started": _book_started(book_dir, trades)})
    if pool_e.get("exists"):
        eb = next((b for b in pool_e["books"] if b.get("key") == key), None)
        if eb:
            rate = pool_e.get("usdinr") or 0
            book_dir = os.path.join(state_dir, "pool_e", key)
            trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
            out.append({"pool": "Pool E", "capital": round(eb["capital"] * rate, 2),
                        "pnl": round((eb["booked"]["raw"] + eb["unbooked"]["raw"]) * rate, 2),
                        "closed_trades": len(trades), "wins": _wins(trades), "started": _book_started(book_dir, trades)})
    if (pool_e1 or {}).get("exists"):
        eb = next((b for b in pool_e1["books"] if b.get("key") == key), None)
        if eb:
            rate = pool_e1.get("usdinr") or 0
            book_dir = os.path.join(state_dir, "pool_e1", key)
            trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
            out.append({"pool": "Pool E1", "capital": round(eb["capital"] * rate, 2),
                        "pnl": round((eb["booked"]["raw"] + eb["unbooked"]["raw"]) * rate, 2),
                        "closed_trades": len(trades), "wins": _wins(trades), "started": _book_started(book_dir, trades)})
    return out


def statement_lines(books: list, pool_d: dict, pool_e: dict, pool_g: dict, registry_records: list,
                    state_dir: str = "", d_trades: Optional[list] = None, pool_e1: Optional[dict] = None) -> list:
    """One line per book (strategy x pool) for the P&L tab, all in rupees. realised / unrealised are
    GROSS (the dashboard's other tabs show the same numbers); each line also carries `charges`
    (brokerage, STT, exchange, SEBI, stamp duty, DP -- everything except GST), `gst`, `tax` and a
    `tds` memo, plus `detail` (charges by component) for the hover text.

    Stock pools (A, B, C, F, D) use reporting/equity_costs.py, worked out from each book's own
    trades and open positions; crypto (E, G) uses the fee and 31.2% tax arithmetic in
    reporting/pool_e.py. Open positions are treated as if sold today throughout.
    capital + realised + unrealised == cash + deployed + unrealised by construction (capital is
    defined as cash + deployed - realised), so the balance sheet always balances."""
    from reporting.equity_costs import book_costs
    sid_of = {r.strategy_key: getattr(r, "strategy_id", "") for r in registry_records}
    d_trades = d_trades or []
    out = []

    def equity_line(pool, key, sid, name, kind, capital, cash, deployed, realised, unrealised, trades, open_positions, intraday):
        c = book_costs(trades, open_positions, intraday, realised + unrealised)
        return {"pool": pool, "key": key, "sid": sid, "name": name, "type": kind, "capital": capital, "cash": cash,
                "deployed": deployed, "realised": realised, "unrealised": unrealised, "charges": c["charges"], "gst": c["gst"],
                "tax": c["tax"], "tds": None, "tax_rate": c["tax_rate"], "detail": c["detail"], "taxable": True}

    for b in books:
        pool_letter = b["pool"]
        book_dir = os.path.join(state_dir, POOL_DIRS[pool_letter], b["key"]) if pool_letter in ("A", "F") \
            else os.path.join(state_dir, POOL_DIRS[pool_letter])
        trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl")) if state_dir else []
        opens = [{"entry_price": p["entry"], "price": p["price"], "quantity": p["qty"]} for p in b.get("positions_detail", [])]
        out.append(equity_line(POOL_LABELS.get(pool_letter, "Pool " + pool_letter), b["key"], b.get("sid", "") or sid_of.get(b["key"], ""),
                               b["display_name"], "AI" if pool_letter in ("B", "C") else "Swing",
                               b.get("capital", 0) or 0, b.get("cash", 0) or 0, b.get("deployed", 0) or 0,
                               b.get("realised", 0) or 0, b.get("unrealised", 0) or 0, trades, opens, False))
    if pool_d.get("capital") is not None:
        opens = [{"entry_price": p.get("entry_price"), "price": p.get("price"), "quantity": p.get("quantity"), "direction": p.get("direction")}
                 for p in pool_d.get("open_positions", [])]
        out.append(equity_line("Pool D", "pool_d_vwap_fade", sid_of.get("pool_d_vwap_fade", ""), "Intraday (VWAP fade)", "Intraday",
                               pool_d["capital"], pool_d.get("cash", 0) or 0, pool_d.get("deployed", 0) or 0,
                               pool_d.get("realised", 0) or 0, pool_d.get("unrealised", 0) or 0, d_trades, opens, True))

    def crypto_line(pool, key, sid, name, capital, cash, deployed, booked, unbooked, rate):
        fees = (booked["fees"] + unbooked["fees"]) * rate
        return {"pool": pool, "key": key, "sid": sid, "name": name, "type": "Crypto",
                "capital": capital * rate, "cash": cash * rate, "deployed": deployed * rate,
                "realised": booked["raw"] * rate, "unrealised": unbooked["raw"] * rate,
                # the 0.30% exchange fee is treated as GST-inclusive, so GST is shown as zero
                "charges": fees, "gst": 0.0, "tax": (booked["tax"] + unbooked["tax"]) * rate,
                "tds": (booked["tds"] + unbooked["tds"]) * rate, "tax_rate": 0.312, "detail": {"crypto_fee": fees}, "taxable": True}
    if pool_e.get("exists"):
        rate = pool_e.get("usdinr") or 0
        for eb in pool_e["books"]:
            out.append(crypto_line("Pool E", eb["key"], eb.get("sid", "") or sid_of.get(eb["key"], ""), eb["display_name"],
                                   eb["capital"], eb["cash"], eb["deployed"], eb["booked"], eb["unbooked"], rate))
    if (pool_e1 or {}).get("exists"):
        rate = pool_e1.get("usdinr") or 0
        for eb in pool_e1["books"]:
            out.append(crypto_line("Pool E1", eb["key"], eb.get("sid", "") or sid_of.get(eb["key"], ""), eb["display_name"],
                                   eb["capital"], eb["cash"], eb["deployed"], eb["booked"], eb["unbooked"], rate))
    if pool_g.get("exists"):
        out.append(crypto_line("Pool G", "portfolio_g", sid_of.get("portfolio_g", ""), "AI judgment", pool_g["capital"], pool_g["cash"],
                               pool_g["deployed"], pool_g["booked"], pool_g["unbooked"], pool_g.get("usdinr") or 0))
    for l in out:
        for k in ("capital", "cash", "deployed", "realised", "unrealised", "charges", "gst", "tax", "tds"):
            if l[k] is not None:
                l[k] = round(l[k], 2)
        l["detail"] = {k: round(v, 2) for k, v in l["detail"].items()}
    return out


def portfolio_view(snap: Optional[dict], prices: dict, prev_close: Optional[dict] = None, session_today: bool = True) -> dict:
    """Your real Groww holdings for the My Portfolio tab. Groww supplies quantity and average cost;
    the latest price comes from the same feed as the paper pools (symbol + ".NS"), so a holding it
    cannot price is shown at cost with no P&L rather than guessed. `today` is the move since the
    last close before today, and is 0 on a day with no trading session (weekend, or before the open)."""
    snap = snap or {}
    prev_close = prev_close or {}
    rows = []
    for h in snap.get("holdings", []):
        key = f"{h['symbol']}.NS"
        qty, avg = float(h["quantity"]), float(h["avg_price"])
        price = prices.get(key)
        prev = prev_close.get(key)
        invested = qty * avg
        value = qty * float(price) if price is not None else None
        rows.append({"symbol": h["symbol"], "quantity": qty, "avg_price": round(avg, 2), "invested": round(invested, 2),
                     "price": round(float(price), 2) if price is not None else None,
                     "value": round(value, 2) if value is not None else None,
                     "pnl": round(value - invested, 2) if value is not None else None,
                     "pct": round((value / invested - 1) * 100, 2) if value is not None and invested else None,
                     "today": (round((float(price) - float(prev)) * qty, 2) if session_today else 0.0) if price is not None and prev is not None else None})
    priced = [r for r in rows if r["value"] is not None]
    invested_priced = sum(r["invested"] for r in priced)
    totals = {"holdings": len(rows), "unpriced": len(rows) - len(priced),
              "invested": round(sum(r["invested"] for r in rows), 2),
              "value": round(sum(r["value"] for r in priced) + sum(r["invested"] for r in rows if r["value"] is None), 2),
              "pnl": round(sum(r["pnl"] for r in priced), 2),
              "pct": round(sum(r["pnl"] for r in priced) / invested_priced * 100, 2) if invested_priced else None,
              "today": round(sum(r["today"] or 0 for r in rows), 2)}
    return {"status": snap.get("status", "not_connected"), "message": snap.get("message", ""), "fetched_at": snap.get("fetched_at"),
            "holdings": rows, "totals": totals}


# How the real Groww holdings are grouped for the Reports tab's "where the profit comes from".
LONG_TERM_GROUPS = {
    "GOLDBEES": "Gold and silver", "SILVERBEES": "Gold and silver",
    "NIFTYBEES": "India index ETFs", "MID150BEES": "India index ETFs", "HDFCSML250": "India index ETFs", "ITBEES": "India index ETFs",
    "MON100": "US tech (Nasdaq-100)", "GROWWDEFNC": "Defence ETF",
}
SPIN_OFF_SHARES = {"VAML", "VOGL", "VEDPOWER", "VISL"}   # demerger shares: Groww's cost for them is an allocation, not a purchase


# Broad segments for the industry pie. Index funds are their own slice (they already hold many industries);
# sector funds join the sector they track. Each NSE industry maps to one broad segment.
FUND_SEGMENT = {
    "GOLDBEES": "Gold and silver", "SILVERBEES": "Gold and silver",
    "NIFTYBEES": "India index funds", "MID150BEES": "India index funds", "HDFCSML250": "India index funds",
    "ITBEES": "Technology (incl. US tech fund)", "MON100": "Technology (incl. US tech fund)",
    "GROWWDEFNC": "Industrials and infrastructure",
}
INDUSTRY_SEGMENT = {
    "Financial Services": "Financial services",
    "Automobile and Auto Components": "Automobiles",
    "Consumer Services": "Consumer", "Fast Moving Consumer Goods": "Consumer", "Consumer Durables": "Consumer",
    "Textiles": "Consumer", "Media Entertainment & Publication": "Consumer",
    "Capital Goods": "Industrials and infrastructure", "Construction": "Industrials and infrastructure",
    "Construction Materials": "Industrials and infrastructure", "Services": "Industrials and infrastructure",
    "Metals & Mining": "Metals and chemicals", "Chemicals": "Metals and chemicals",
    "Information Technology": "Technology (incl. US tech fund)",
}
FALLBACK_SEGMENT = "Realty, energy and other"
# Companies missing from the Nifty 500 list (demerged or newly listed): NSE industry by what the business does.
INDUSTRY_OVERRIDES = {
    "TMPV": "Automobile and Auto Components", "TMCV": "Automobile and Auto Components",
    "VAML": "Metals & Mining", "VISL": "Metals & Mining", "VOGL": "Oil, Gas & Consumable Fuels", "VEDPOWER": "Power",
    "UNIECOM": "Information Technology",
}


@functools.lru_cache(maxsize=1)
def _industry_map() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "nifty500_constituents.csv")
    out = {}
    try:
        import csv
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[r["Symbol"]] = r["Industry"]
    except OSError:
        pass
    return out


def _segment_of(symbol: str) -> str:
    industry = INDUSTRY_OVERRIDES.get(symbol) or _industry_map().get(symbol)
    return INDUSTRY_SEGMENT.get(industry, FALLBACK_SEGMENT)


def industry_view(mine: Optional[dict]) -> Optional[dict]:
    """Where the money in the real Groww account sits, in a handful of broad segments. A holding that
    cannot be priced counts at cost."""
    if not mine or not mine.get("holdings"):
        return None
    inds = _industry_map()
    slices: dict = {}
    fund_value = 0.0
    for h in mine["holdings"]:
        value = h["value"] if h.get("value") is not None else h["invested"]
        is_fund = h["symbol"] in FUND_SEGMENT
        if is_fund:
            name, fund_value = FUND_SEGMENT[h["symbol"]], fund_value + value
        else:
            industry = INDUSTRY_OVERRIDES.get(h["symbol"]) or inds.get(h["symbol"])
            name = INDUSTRY_SEGMENT.get(industry, FALLBACK_SEGMENT)
        sl = slices.setdefault(name, {"name": name, "value": 0.0, "invested": 0.0, "holdings": []})
        sl["value"] += value
        sl["invested"] += h["invested"]
        sl["holdings"].append({"symbol": h["symbol"], "value": round(value, 2), "fund": is_fund})
    total = sum(sl["value"] for sl in slices.values())
    rows = []
    for sl in sorted(slices.values(), key=lambda x: -x["value"]):
        sl["holdings"].sort(key=lambda x: -x["value"])
        rows.append({**sl, "value": round(sl["value"], 2), "invested": round(sl["invested"], 2),
                     "weight": round(sl["value"] / total * 100, 1) if total else None})
    return {"total": round(total, 2), "slices": rows,
            "funds_pct": round(fund_value / total * 100, 1) if total else None,
            "stocks_pct": round((total - fund_value) / total * 100, 1) if total else None}


# Demerged companies are reported with their parent: the parent's price fell when the new shares were handed out.
FAMILY = {"TMCV": "TMPV", "VAML": "VEDL", "VOGL": "VEDL", "VEDPOWER": "VEDL", "VISL": "VEDL", "NETWORK18": "TV18BRDCST"}
FAMILY_NAME = {"TMPV": "Tata Motors (with demerged TMCV)", "VEDL": "Vedanta (with demerged units)", "TV18BRDCST": "TV18 Broadcast / Network18 (merged)"}
COMPANY_NAMES = {
    "VAML": "Vedanta Aluminium Metal", "VOGL": "Vedanta Oil & Gas", "VEDPOWER": "Vedanta Power", "VISL": "Vedanta Iron & Steel",
    "TMCV": "Tata Motors (commercial vehicles)", "TMPV": "Tata Motors Passenger Vehicles", "VEDL": "Vedanta",
    "NIFTYBEES": "Nifty 50 ETF (Nippon)", "MID150BEES": "Midcap 150 ETF (Nippon)", "SILVERBEES": "Silver ETF (Nippon)", "GOLDBEES": "Gold ETF (Nippon)",
    "HDFCSML250": "Smallcap 250 ETF (HDFC)", "ITBEES": "IT ETF (Nippon)", "MON100": "Nasdaq-100 ETF (Motilal Oswal)", "GROWWDEFNC": "Defence ETF (Groww)",
    "BANKBEES": "Bank ETF (Nippon)", "JUNIORBEES": "Nifty Next 50 ETF (Nippon)", "PSUBNKBEES": "PSU Bank ETF (Nippon)", "PHARMABEES": "Pharma ETF (Nippon)",
    "CPSEETF": "CPSE ETF", "SETFNIFBK": "Nifty Bank ETF (SBI)",
    "ITC": "ITC", "NTPC": "NTPC", "NTPCGREEN": "NTPC Green Energy", "DLF": "DLF", "TCS": "TCS", "INFY": "Infosys", "LICHSGFIN": "LIC Housing Finance",
    "TVSMOTOR": "TVS Motor", "GROWW": "Groww (Billionbrains Garage)", "IRFC": "Indian Railway Finance Corp", "IRCTC": "IRCTC", "RVNL": "Rail Vikas Nigam",
    "HSCL": "Himadri Speciality Chemical", "UNIECOM": "Unicommerce eSolutions", "TV18BRDCST": "TV18 Broadcast", "NETWORK18": "Network18 Media", "PAYTM": "Paytm (One 97)",
    "NYKAA": "Nykaa (FSN E-Commerce)", "DMART": "DMart (Avenue Supermarts)", "BAJAJHFL": "Bajaj Housing Finance", "OLAELEC": "Ola Electric", "HIGHENE$": "High Energy Batteries",
    "ELECON": "Elecon Engineering", "FINOPB": "Fino Payments Bank", "GOKEX": "Gokaldas Exports", "GPIL": "Godawari Power & Ispat", "ZEEL": "Zee Entertainment",
    "RAMASTEEL": "Rama Steel Tubes", "HDFCLIFE": "HDFC Life", "JIOFIN": "Jio Financial Services", "JINDALSTEL": "Jindal Steel", "TATAPOWER": "Tata Power",
    "TATACONSUM": "Tata Consumer", "TORNTPOWER": "Torrent Power", "WAAREEENER": "Waaree Energies", "OBEROIRLTY": "Oberoi Realty", "EXIDEIND": "Exide Industries",
    "OLECTRA": "Olectra Greentech", "ETERNAL": "Eternal (Zomato)", "HONASA": "Honasa Consumer", "WIPRO": "Wipro", "LUPIN": "Lupin", "SUZLON": "Suzlon Energy",
    "COCHINSHIP": "Cochin Shipyard", "CASTROLIND": "Castrol India", "BHARTIARTL": "Bharti Airtel", "RELIANCE": "Reliance Industries", "TITAN": "Titan",
    "LT": "Larsen & Toubro", "NHPC": "NHPC", "IDEA": "Vodafone Idea", "RPOWER": "Reliance Power",
}


def _company_name(rep: dict, symbol: str) -> str:
    return COMPANY_NAMES.get(symbol) or (rep.get("names") or {}).get(symbol, symbol).title()


def dividends_view(rep: dict, today: date) -> dict:
    """Every dividend received, date by date, the best payers, and a check against Groww's own yearly totals."""
    rows = sorted(rep.get("dividends", []), key=lambda r: (r["ex"], r["symbol"]), reverse=True)
    by_family: dict = {}
    cost: dict = {}
    for sym, flows in (rep.get("company_flows") or {}).items():
        fam = FAMILY.get(sym, sym)
        cost[fam] = cost.get(fam, 0.0) + sum(-a for _, a in flows if a < 0)
    fy_est: dict = {}
    out_rows = []
    for r in rows:
        d = date.fromisoformat(r["ex"])
        fy = d.year if d.month >= 4 else d.year - 1
        fy_key = f"FY{str(fy)[2:]}-{str(fy + 1)[2:]}"
        fy_est[fy_key] = fy_est.get(fy_key, 0.0) + r["gross"]
        fam = FAMILY.get(r["symbol"], r["symbol"])
        f = by_family.setdefault(fam, {"key": fam, "name": FAMILY_NAME.get(fam) or _company_name(rep, fam), "total": 0.0, "payouts": 0, "last": r["ex"]})
        f["total"] += r["gross"]
        f["payouts"] += 1
        out_rows.append({"date": r["ex"], "fy": fy_key, "symbol": r["symbol"], "name": _company_name(rep, r["symbol"]),
                         "dps": r["dps"], "qty": r["qty"], "amount": r["gross"], "source": r.get("source", "estimated")})
    companies = sorted(by_family.values(), key=lambda x: -x["total"])
    for c in companies:
        c["total"] = round(c["total"], 2)
        c["cost"] = round(cost.get(c["key"], 0.0), 2)
        c["pct_of_cost"] = round(c["total"] / c["cost"] * 100, 2) if c["cost"] else None
    groww = {f["fy"]: f["dividends"] for f in rep.get("fy", [])}
    check = [{"fy": k, "rebuilt": round(v, 2), "groww": groww.get(k)} for k, v in sorted(fy_est.items())]
    cur_fy = today.year if today.month >= 4 else today.year - 1
    year_ago = date(today.year - 1, today.month, min(today.day, 28)).isoformat()
    return {"rows": out_rows, "companies": companies, "check": check,
            "total": round(sum(r["gross"] for r in rows), 2),
            "this_fy": round(fy_est.get(f"FY{str(cur_fy)[2:]}-{str(cur_fy + 1)[2:]}", 0.0), 2),
            "last_12m": round(sum(r["gross"] for r in rows if r["ex"] >= year_ago), 2),
            "first": min((r["ex"] for r in rows), default=None),
            "groww_count": sum(1 for r in rows if r.get("source") == "groww"),
            "groww_total": round(sum(r["gross"] for r in rows if r.get("source") == "groww"), 2),
            "estimated_total": round(sum(r["gross"] for r in rows if r.get("source") == "estimated"), 2),
            "due_total": round(sum(r["gross"] for r in rows if r.get("source") == "due"), 2)}


def company_returns_view(rep: dict, mine: dict, today: date) -> list:
    """Return per company, including ones already sold: price gain, dividends, total, and the compounded
    yearly return (only where the money has been in for a year or more, otherwise annualising misleads)."""
    from reporting.groww_reports import xirr
    live: dict = {}
    for h in mine["holdings"]:
        live[FAMILY.get(h["symbol"], h["symbol"])] = live.get(FAMILY.get(h["symbol"], h["symbol"]), 0.0) + (h["value"] if h.get("value") is not None else h["invested"])
    flows: dict = {}
    for sym, fl in (rep.get("company_flows") or {}).items():
        flows.setdefault(FAMILY.get(sym, sym), []).extend((date.fromisoformat(d), a) for d, a in fl)
    net_qty: dict = {}
    for sym, q in (rep.get("net_qty") or {}).items():
        net_qty[FAMILY.get(sym, sym)] = net_qty.get(FAMILY.get(sym, sym), 0.0) + q
    divs: dict = {}
    for r in rep.get("dividends", []):
        divs.setdefault(FAMILY.get(r["symbol"], r["symbol"]), []).append((date.fromisoformat(r["ex"]), r["gross"]))
    out = []
    for fam, fl in flows.items():
        bought = sum(-a for _, a in fl if a < 0)
        if bought <= 0:
            continue
        sold = sum(a for _, a in fl if a > 0)
        value = live.get(fam, 0.0)
        dv = sum(a for _, a in divs.get(fam, []))
        price_gain = value + sold - bought
        first = min(d for d, _ in fl)
        last = today if value > 0 else max(d for d, _ in fl)
        cash = list(fl) + divs.get(fam, []) + ([(today, value)] if value > 0 else [])
        yrs = (last - first).days / 365.0
        x = xirr(cash) if yrs >= 1 else None
        # shares the orders say you still own that are no longer in the account (merger, delisting): the outcome is unknown
        unknown = value == 0 and net_qty.get(fam, 0.0) > 0.5
        out.append({"key": fam, "name": FAMILY_NAME.get(fam) or _company_name(rep, fam), "held": value > 0, "unknown": unknown,
                    "bought": round(bought, 2), "sold": round(sold, 2), "value": round(value, 2), "dividends": round(dv, 2),
                    "profit": round(price_gain + dv, 2),
                    "price_pct": round(price_gain / bought * 100, 1), "div_pct": round(dv / bought * 100, 1),
                    "total_pct": round((price_gain + dv) / bought * 100, 1),
                    "annual_pct": round(x * 100, 1) if x is not None else None, "years": round(yrs, 1)})
    return sorted(out, key=lambda r: (r["unknown"], -r["profit"]))


def reports_view(rep: Optional[dict], mine: dict, cash: Optional[float], today: date) -> Optional[dict]:
    """The Reports tab: returns on the money put into the real Groww account, what drives the profit,
    and what it costs. `rep` is the file built by reporting/groww_reports.py from Groww's downloaded
    reports; today's value comes from the live holdings in `mine` (portfolio_view). Money added since the
    reports were downloaded is inferred from the change in holdings cost and dated today."""
    from reporting.groww_reports import modified_dietz, xirr
    if not rep or not mine or not mine.get("holdings"):
        return None
    tot = mine["totals"]
    value, invested_now = float(tot["value"]), float(tot["invested"])
    added_since = round(invested_now - float(rep["net_invested"]), 2)
    if abs(added_since) < 1:
        added_since = 0.0
    flows = [(date.fromisoformat(d), a) for d, a in rep["flows"]]
    own_flows = flows + ([(today, -added_since)] if added_since else []) + [(today, value)]
    bench_now = next((h["price"] for h in mine["holdings"] if h["symbol"] == rep["benchmark"] and h.get("price")), None) or rep.get("benchmark_price_at_report")
    units = float(rep["bench_units_total"]) + (added_since / bench_now if bench_now else 0.0)
    bench_value = units * bench_now if bench_now else None
    bench_flows = [(d, a) for d, a in flows] + ([(today, -added_since)] if added_since else []) + ([(today, bench_value)] if bench_value else [])
    net_in = float(rep["net_invested"]) + added_since

    def _round(x, n=2):
        return None if x is None else round(x, n)
    own_x, bench_x = xirr(own_flows), (xirr(bench_flows) if bench_value else None)

    years = []
    for y in rep["yearly"]:
        cur = y["end_own"] is None
        end_own = value if cur else y["end_own"]
        end_bench = bench_value if cur else y["end_bench"]
        net = y["net_added"] + (added_since if cur else 0.0)
        own_ret = modified_dietz(y["start_own"], end_own, net, y["weighted_net"] + (0.0 if not cur else 0.0))
        bench_ret = modified_dietz(y["start_bench"], end_bench, net, y["weighted_net"]) if end_bench is not None else None
        years.append({"year": y["year"], "label": f'{y["year"]}{" (to date)" if cur else ""}', "start": y["start_own"], "net_added": round(net, 2),
                      "buys": y["buys"], "sells": y["sells"], "n_buys": y["n_buys"], "n_sells": y["n_sells"],
                      "end": _round(end_own), "gain": _round(end_own - y["start_own"] - net),
                      "return_pct": _round(own_ret * 100, 1) if own_ret is not None else None,
                      "bench_return_pct": _round(bench_ret * 100, 1) if bench_ret is not None else None})
    # Month table: what went in and out of the Groww account (from its fund statement, which only starts in the
    # month of ledger["first_date"]) beside the running total invested in stocks (from the orders).
    ledger = rep.get("ledger") or {}
    led = {m["month"]: m for m in ledger.get("months", [])}
    led_start = (ledger.get("first_date") or "9999-99")[:7]
    ordered = {m["month"]: m for m in rep["monthly"]}
    monthly, cum = [], 0.0
    for month in sorted(set(ordered) | set(led)):
        if month in ordered:
            cum += ordered[month]["buys"] - ordered[month]["sells"]
        known = month >= led_start
        dep, wd = (led.get(month, {}).get("deposited", 0.0), led.get(month, {}).get("withdrawn", 0.0)) if known else (None, None)
        monthly.append({"month": month, "deposited": dep, "withdrawn": wd, "net": round(dep - wd, 2) if known else None, "cum": round(cum, 2)})

    groups: dict = {}
    rows = []
    for h in mine["holdings"]:
        if h.get("value") is None:
            continue
        g = LONG_TERM_GROUPS.get(h["symbol"], "Individual stocks")
        s = groups.setdefault(g, {"group": g, "invested": 0.0, "value": 0.0, "count": 0})
        s["invested"] += h["invested"]; s["value"] += h["value"]; s["count"] += 1
        rows.append({"symbol": h["symbol"], "group": g, "invested": h["invested"], "value": h["value"], "pnl": h["pnl"], "pct": h["pct"],
                     "spin_off": h["symbol"] in SPIN_OFF_SHARES})
    total_pnl = sum(r["pnl"] for r in rows)
    group_rows = []
    for s in sorted(groups.values(), key=lambda s: -(s["value"] - s["invested"])):
        pnl = s["value"] - s["invested"]
        group_rows.append({**s, "invested": round(s["invested"], 2), "value": round(s["value"], 2), "pnl": round(pnl, 2),
                           "pct": round((s["value"] / s["invested"] - 1) * 100, 1) if s["invested"] else None,
                           "weight": round(s["value"] / value * 100, 1) if value else None,
                           "share_of_profit": round(pnl / total_pnl * 100, 1) if total_pnl else None})
    rows.sort(key=lambda r: -r["pnl"])
    fy = rep["fy"]
    return {
        "dividends": dividends_view(rep, today), "companies": company_returns_view(rep, mine, today),
        "as_of_reports": rep["as_of"], "benchmark": rep["benchmark"], "cash": cash,
        "headline": {"invested": round(net_in, 2), "value": round(value, 2), "gain": round(value - net_in, 2),
                     "gain_pct": round((value / net_in - 1) * 100, 2) if net_in else None,
                     "xirr_pct": _round(own_x * 100, 1) if own_x is not None else None,
                     "bench_value": _round(bench_value), "bench_gain": _round(bench_value - net_in) if bench_value else None,
                     "bench_xirr_pct": _round(bench_x * 100, 1) if bench_x is not None else None,
                     "added_since_reports": added_since, "first_trade": rep["flows"][0][0] if rep["flows"] else None},
        "years": years, "monthly": monthly, "groups": group_rows,
        "winners": [r for r in rows if r["pnl"] > 0][:8], "losers": sorted([r for r in rows if r["pnl"] < 0], key=lambda r: r["pnl"])[:8],
        "fy": fy, "ledger": ledger,
        "totals": {"charges": round(sum(f["charges"] for f in fy), 2), "dividends": round(sum(f["dividends"] for f in fy), 2),
                   "realised": round(sum(f["intraday"] + f["short_term"] + f["long_term"] for f in fy), 2)},
    }


def strategies_view(registry_records: list, pool_d_strategy: str, pool_f_keys: Optional[set] = None,
                    books: Optional[list] = None, pool_d: Optional[dict] = None,
                    pool_e: Optional[dict] = None, pool_g: Optional[dict] = None,
                    state_dir: str = "", d_trades: Optional[list] = None,
                    pool_e1: Optional[dict] = None) -> list:
    """Every strategy the desk knows, with its pool, type, plain-language
    brief, capital allocated, total P&L to date, closed-trade count,
    reward:risk ratio, and the registry's verdict/status -- Pools B, C
    and D included even though they are not registry strategies."""
    books, pool_d, pool_e, pool_g, d_trades = books or [], pool_d or {}, pool_e or {}, pool_g or {}, d_trades or []
    pool_e1 = pool_e1 or {}
    e1_keys = {b["key"] for b in pool_e1.get("books", [])}
    rows = []
    for r in registry_records:
        status = str(getattr(r.deployment_status, "value", r.deployment_status)).split(".")[-1]
        crypto = is_crypto_record(r)
        fixed_pool = {"portfolio_b": "Pool B", "portfolio_c": "Pool C", "pool_d_vwap_fade": "Pool D", "portfolio_g": "Pool G"}.get(r.strategy_key)
        kind, brief = STRATEGY_BRIEFS.get(r.strategy_key, ("Crypto" if crypto else "Swing", ""))
        if fixed_pool:
            pool = fixed_pool if status == "PAPER_TRADING" else "-"
        else:
            pool = ("Pool E" if crypto else "Pool A") if status == "PAPER_TRADING" else "-"
        if pool == "Pool A" and r.strategy_key in (pool_f_keys or set()):
            pool = "Pool A, F"
        if pool == "Pool E" and r.strategy_key in e1_keys:
            pool = "Pool E, E1"
        exp = getattr(r, "primary_experiment_id", "") or ""
        pools_breakdown = _strategy_pool_breakdown(r.strategy_key, books, state_dir, d_trades, pool_d, pool_e, pool_g, pool_e1)
        capital = round(sum(p["capital"] for p in pools_breakdown), 2) if pools_breakdown else None
        pnl = round(sum(p["pnl"] for p in pools_breakdown), 2) if pools_breakdown else None
        closed_trades = sum(p["closed_trades"] for p in pools_breakdown) if pools_breakdown else None
        # the strategy "started" when its first book began running (the registry date is when it was approved, which can be weeks earlier)
        book_starts = [p["started"] for p in pools_breakdown if p.get("started")]
        started = min(book_starts) if book_starts else _paper_trading_started(r)
        wins = sum(p["wins"] for p in pools_breakdown) if pools_breakdown else None
        rows.append({"key": r.strategy_key, "sid": getattr(r, "strategy_id", ""), "name": r.display_name, "pool": pool,
                     "type": kind, "verdict": str(getattr(r.research_verdict, "value", r.research_verdict)).split(".")[-1],
                     "status": status, "experiment": exp, "brief": brief, "capital": capital, "pnl": pnl,
                     "started": started, "closed_trades": closed_trades, "wins": wins,
                     "pools_breakdown": pools_breakdown,
                     "how": STRATEGY_HOW.get(r.strategy_key, {}), "research": _experiment_summary(exp),
                     # one tab per pool the strategy runs in; a twin pool carries only what it changes
                     "variants": ([{"pool": "Pool A", "diff": False},
                                   {"pool": "Pool F", "diff": True, "title": "What Pool F does differently",
                                    "brief": STRATEGY_BRIEFS["pool_f"][1], "how": STRATEGY_HOW["pool_f"]}]
                                  if pool == "Pool A, F" else
                                  [{"pool": "Pool E", "diff": False},
                                   {"pool": "Pool E1", "diff": True, "title": "What Pool E1 does differently",
                                    "brief": STRATEGY_BRIEFS["pool_e1"][1], "how": STRATEGY_HOW["pool_e1"]}]
                                  if pool == "Pool E, E1" else [])})
    keys = {r["key"] for r in rows}
    # Before 2026-09-16 these three were not registry entries; keep the synthetic rows only if they are still missing.
    if "portfolio_b" not in keys:
        rows.append({"key": "portfolio_b", "sid": "B", "name": "Portfolio B (AI watchlist book)", "pool": "Pool B", "type": "AI",
                     "verdict": "-", "status": "PAPER_TRADING", "experiment": "", "brief": STRATEGY_BRIEFS["portfolio_b"][1],
                     "capital": None, "pnl": None, "started": None, "closed_trades": None, "wins": None, "pools_breakdown": [], "how": STRATEGY_HOW["portfolio_b"], "research": {}})
    if "portfolio_c" not in keys:
        rows.append({"key": "portfolio_c", "sid": "C", "name": "Portfolio C (AI overlay on Pool A)", "pool": "Pool C", "type": "AI",
                     "verdict": "-", "status": "PAPER_TRADING", "experiment": "", "brief": STRATEGY_BRIEFS["portfolio_c"][1],
                     "capital": None, "pnl": None, "started": None, "closed_trades": None, "wins": None, "pools_breakdown": [], "how": STRATEGY_HOW["portfolio_c"], "research": {}})
    if "portfolio_g" not in keys:
        rows.append({"key": "portfolio_g", "sid": "G", "name": "Portfolio G (AI judgment book, crypto)", "pool": "Pool G", "type": "Crypto",
                     "verdict": "-", "status": "PAPER_TRADING", "experiment": "", "brief": STRATEGY_BRIEFS["portfolio_g"][1],
                     "capital": None, "pnl": None, "started": None, "closed_trades": None, "wins": None, "pools_breakdown": [], "how": STRATEGY_HOW["portfolio_g"], "research": {}})
    if "pool_d_vwap_fade" not in keys:
        rows.append({"key": "pool_d_vwap_fade", "sid": "D", "name": pool_d_strategy, "pool": "Pool D", "type": "Intraday",
                     "verdict": "REJECT", "status": "PAPER_TRADING", "experiment": "EXP-008", "brief": STRATEGY_BRIEFS["pool_d_vwap_fade"][1],
                     "capital": None, "pnl": None, "started": None, "closed_trades": None, "wins": None, "pools_breakdown": [], "how": STRATEGY_HOW["pool_d_vwap_fade"], "research": _experiment_summary("EXP-008")})
    return rows


# Cron jobs as the page's schedule strip. (hour, minute) in IST; "every5" spans a window.
SCHEDULE = [
    {"id": "pool_e", "label": "Pool E crypto (after the 00:00 UTC close)", "at": "05:45", "log": "pool_e.log"},
    {"id": "pool_e1", "label": "Pool E1 crypto (partial booking twin)", "at": "05:50", "log": "pool_e1.log"},
    {"id": "pool_g", "label": "Pool G crypto AI judgment", "at": "08:30 & 20:30", "log": "pool_g.log"},
    {"id": "prep", "label": "Pool D prepare", "at": "09:00", "log": "pool_d.log"},
    {"id": "ticks", "label": "Pool D ticks", "at": "09:15-15:30 every 5 min", "log": "pool_d.log"},
    {"id": "open", "label": "Fill-at-open passes (A, A1, B, C)", "at": "09:30-09:32", "log": "paper_trading_open.log"},
    {"id": "eod_a", "label": "Pool A end-of-day", "at": "15:35", "log": "paper_trading.log"},
    {"id": "eod_f", "label": "Pool F end-of-day (partial booking twin)", "at": "15:38", "log": "pool_f.log"},
    {"id": "eod_a1", "label": "Pool A1 end-of-day", "at": "15:40", "log": "pool_a1.log"},
    {"id": "eod_c", "label": "Portfolio C", "at": "15:45", "log": "portfolio_c.log"},
    {"id": "eod_b", "label": "Portfolio B", "at": "15:50", "log": "portfolio_b.log"},
    {"id": "summary", "label": "Telegram daily summary", "at": "16:05", "log": "daily_pool_summary.log"},
    {"id": "research_queue", "label": "Research queue: rank and advance", "at": "every 6h", "log": "research_queue.log"},
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


def roadmap_view(roadmap: dict, registry_records: list, queue: Optional[dict] = None) -> dict:
    """The Head-of-Research roadmap (swing_research/research_roadmap.py)
    reduced to what the Strategies tab shows: ranked candidates that
    could be researched now, and the ones waiting on data. Candidates
    whose key is already in the registry are dropped -- the roadmap's
    own candidate list lags promotions.

    `queue` is research_queue.load()'s output (2026-09-22): one strategy
    researched at a time, a new one picked up automatically once a week or
    started early from the dashboard. Each row gets a `queue_status` --
    "current" (being researched right now, no Start button), a completed
    history row's outcome (e.g. "researched" with its experiment id, also
    no button), or None (still eligible to start)."""
    taken = {r.strategy_key for r in registry_records}
    queue = queue or {"current": None, "history": []}
    current = queue.get("current") or {}
    current_key = current.get("key")
    resolved = {h["key"]: h for h in queue.get("history", []) if h.get("resolved")}

    def queue_status(key):
        if key == current_key:
            return {"state": "current", "in_progress": bool(current.get("in_progress")), "mode": current.get("mode", "backtest")}
        if key in resolved:
            h = resolved[key]
            return {"state": "resolved", "outcome": h["outcome"], "experiment_id": h.get("experiment_id")}
        return None

    def row(s, mode, rank=None):
        c = s.candidate
        return {"rank": rank, "key": c.key, "name": c.name, "family": c.factor_family, "year": c.year,
                "authors": c.authors, "holding": c.typical_holding_period,
                "holding_days_min": c.holding_days_min, "holding_days_max": c.holding_days_max, "direction": c.direction,
                "horizon_lane": c.horizon_lane, "market": c.market, "mode": mode,
                "score": s.total_score, "axes": {k: round(v, 1) for k, v in s.axis_scores.items()},
                "feasibility": s.feasibility_classification,
                "blockers": list(s.feasibility_reasons)[:2], "strengths": c.known_strengths,
                "weaknesses": c.known_weaknesses, "queue": queue_status(c.key)}

    from swing_research.research_roadmap import DEFERRED_BY_DIRECTION
    # paper_direct_eligible (2026-09-22): a blocked candidate that scores as well as the worst
    # backtestable one -- genuinely eligible for the SAME queue, just mode="paper_direct" instead of
    # "backtest" (see research_queue.py). Shown in "ready", not "deferred", and excluded from
    # deferred/by_direction below so nothing appears in two tables at once.
    paper_direct_keys = {s.candidate.key for s in roadmap.get("paper_direct_eligible", [])}
    ready_pool = ([(s, "backtest") for s in roadmap["researchable_now"] if s.candidate.key not in taken]
                  + [(s, "paper_direct") for s in roadmap.get("paper_direct_eligible", []) if s.candidate.key not in taken])
    ready_pool.sort(key=lambda pair: -pair[0].total_score)
    deferred = [s for s in roadmap["deferred_pending_data"]
                if s.candidate.key not in taken and s.candidate.key not in paper_direct_keys]
    by_direction = [s for s in roadmap.get("deferred_by_direction", []) if s.candidate.key not in taken]
    rows = [row(s, "backtest") for s in deferred]
    for s in by_direction:
        r = row(s, "backtest")
        r["blockers"] = [DEFERRED_BY_DIRECTION[s.candidate.key]]
        rows.append(r)
    return {"ready": [row(s, mode, i + 1) for i, (s, mode) in enumerate(ready_pool)], "deferred": rows,
            "weights": roadmap.get("weights", {})}


def agents_view(queue: Optional[dict] = None) -> list:
    """AGENTS, with the Strategy Implementer's status overridden to "working" while the research routine
    (Phase 2, a scheduled cloud agent) actually has something in_progress -- the only agent on the whole
    team tab whose status reflects something outside this VPS's own logs, since that's the only signal
    the routine ever sends back (research_queue.mark_in_progress, called over the internet)."""
    current = (queue or {}).get("current") or {}
    if not current.get("in_progress"):
        return AGENTS
    return [{**a, "live_status": "working"} if a["id"] == "research_routine" else a for a in AGENTS]


def build_dashboard_state(state_dir: str, logs_dir: str, registry_records: list, prices: dict,
                          prices_as_of: Optional[str], now: Optional[datetime] = None,
                          roadmap: Optional[dict] = None, mode: str = "paper",
                          crypto_prices: Optional[dict] = None, usdinr: Optional[float] = None,
                          prev_close: Optional[dict] = None, crypto_prev_close: Optional[dict] = None,
                          groww: Optional[dict] = None, reports: Optional[dict] = None,
                          advice_params: Optional[dict] = None, advice_done: Optional[list] = None,
                          advice_results: Optional[dict] = None, advice_extra: Optional[dict] = None,
                          research_queue: Optional[dict] = None) -> dict:
    now = now or datetime.now()
    today = now.date()
    active = {r.strategy_key: r.display_name for r in registry_records
              if str(getattr(r.deployment_status, "value", r.deployment_status)).endswith("PAPER_TRADING")
              and is_pool_a_record(r)}
    summary = build_pool_summary(state_dir, active, lambda symbols: {s: prices[s] for s in symbols if s in prices},
                                 today=today, crypto_prices=crypto_prices, usdinr=usdinr)

    books = []
    for pool, dirname in POOL_DIRS.items():
        if pool in ("A", "F"):
            for b in summary["books"][pool]:
                book_dir = os.path.join(state_dir, dirname, b["key"])
                rec = next((r for r in registry_records if r.strategy_key == b["key"]), None)
                books.append({**b, "pool": pool, "sid": getattr(rec, "strategy_id", "") if rec else "",
                              "positions_detail": _positions_detail(book_dir, prices),
                              "recent_trades": _recent_trades(book_dir)})
        else:
            b = summary["books"][pool][0]
            book_dir = os.path.join(state_dir, dirname)
            rec = next((r for r in registry_records if r.strategy_key == b["key"]), None)
            books.append({**b, "pool": pool, "sid": getattr(rec, "strategy_id", "") if rec else "",
                          "positions_detail": _positions_detail(book_dir, prices),
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
    my_portfolio = portfolio_view(groww, prices, prev_close, session_today=now.weekday() < 5 and now.time() >= dtime(9, 15))
    reports_out = reports_view(reports, my_portfolio, (groww or {}).get("cash"), now.date())
    from advice.view import build_advice
    advice_out = build_advice(my_portfolio, reports_out, now.date(), advice_params, lambda s: FAMILY.get(s, s), _segment_of, state_dir,
                             lambda fam: FAMILY_NAME.get(fam) or _company_name(reports or {}, fam),
                             cash=(groww or {}).get("cash"), done=advice_done, results=advice_results, raw=reports,
                             screener=(advice_extra or {}).get("screener"), log=(advice_extra or {}).get("log")) if reports is not None else None
    pools = {k: _with_capital(dict(v)) for k, v in summary["pools"].items() if k != "A1"}
    pool_d = _with_capital(pool_d)
    for b in books:
        _with_capital(b)
    pool_e = summary["pool_e"]
    pool_e1 = summary["pool_e1"]   # Pool E's partial-booking twin, 2026-09-22 -- exactly Pool F's relationship to Pool A
    for r in registry_records:
        if is_crypto_record(r):
            for b in pool_e["books"]:
                if b["key"] == r.strategy_key:
                    b["sid"] = getattr(r, "strategy_id", "")
            for b in pool_e1["books"]:
                if b["key"] == r.strategy_key:
                    b["sid"] = getattr(r, "strategy_id", "")
    from reporting.pool_g import build_pool_g
    from data.fetch_crypto import DEFAULT_USDINR
    pool_g = build_pool_g(state_dir, crypto_prices, usdinr or DEFAULT_USDINR, today)
    g_rate = pool_g.get("usdinr") or 0
    overall = dict(summary["overall"])   # built by reporting/pool_summary.py (post-tax for crypto -- the Telegram basis; the dashboard tabs show gross)
    overall["capital"] = round(sum(p["capital"] for p in pools.values()) + pool_d["capital"]
                               + pool_e["inr"]["capital"] + pool_e1["inr"]["capital"] + pool_g.get("capital", 0) * g_rate, 2)
    overall["unrealised"] = round(overall["unrealised"] + pool_d["unrealised"], 2)
    lifecycles = {}
    state = {
        "mode": mode, "generated_at": now.isoformat(timespec="seconds"), "today": today.isoformat(),
        "market_open": market_open, "prices_as_of": prices_as_of, "priced_symbols": len(prices),
        "quotes": {k: round(float(v), 2) for k, v in prices.items()},
        "pools": pools, "overall": overall, "books": books, "pool_d": pool_d, "pool_e": pool_e, "pool_e1": pool_e1, "pool_g": pool_g,
        "ledger": _ledger(state_dir, books, d_pf, d_trades, today, pool_e, d_open,
                          prev_close=prev_close, crypto_prev_close=crypto_prev_close,
                          prices=prices, crypto_prices=crypto_prices, pool_g=pool_g, lifecycles=lifecycles, pool_e1=pool_e1),
        "lifecycles": lifecycles,
        "schedule": schedule, "registry": registry, "agents": agents_view(research_queue), "desks": DESKS, "flows": FLOWS, "pools_info": POOLS_INFO, "my_portfolio": my_portfolio, "reports": reports_out, "industries": industry_view(my_portfolio), "advice": advice_out, "statement": statement_lines(books, pool_d, pool_e, pool_g, registry_records, state_dir, d_trades, pool_e1=pool_e1),
        "strategies": strategies_view(registry_records, "VWAP Extension Exhaustion Fade",
                                      {b["key"] for b in summary["books"].get("F", [])},
                                      books=books, pool_d=pool_d, pool_e=pool_e, pool_g=pool_g,
                                      state_dir=state_dir, d_trades=d_trades, pool_e1=pool_e1),
        "roadmap": roadmap_view(roadmap, registry_records, research_queue) if roadmap else {"ready": [], "deferred": [], "weights": {}},
    }
    if mode == "live" and reports is not None:    # your real Groww portfolio is Pool H, shown only in Live mode
        from reporting.pool_h import add_pool_h
        add_pool_h(state, my_portfolio, reports, (groww or {}).get("cash"), now.date(), (advice_out or {}).get("tax"))
    return state


def _days_between(start_iso, end_iso) -> Optional[int]:
    try:
        return (date.fromisoformat(end_iso) - date.fromisoformat(start_iso)).days
    except (TypeError, ValueError):
        return None


def _attach_lifecycles(rows: list) -> dict:
    """Group ledger rows into one lifecycle per purchase: the entry, every sell leg (a partial
    profit booking is its own leg), and whatever is still held. Each row gets a `life` id and a
    `partial` flag (part sold while the rest is still open); the full story for the popup goes in
    the returned {life_id: {...}} map so it is sent once, not once per row."""
    groups = {}
    for r in rows:
        entry_price = float(r["price"] if r["status"] == "Open" else r.get("entry_price") or 0)
        key = (r["pool"], r.get("book"), r.get("symbol_key") or r.get("symbol"), r.get("bought_on"), round(entry_price, 4))
        groups.setdefault(key, []).append((r, entry_price))
    lifecycles = {}
    for n, (key, members) in enumerate(groups.items()):
        life_id = f"L{n}"
        opens = [r for r, _ in members if r["status"] == "Open"]
        sells = sorted((r for r, _ in members if r["status"] == "Closed"), key=lambda r: (r["date"] or "", r["time"] or ""))
        first, entry_price = members[0]
        entry_action = first["action"] if first["status"] == "Open" else ("SELL" if first["action"] == "BUY" else "BUY")
        qty_left = sum(float(r["qty"] or 0) for r in opens)
        qty_sold = sum(float(r["qty"] or 0) for r in sells)
        qty_bought = qty_left + qty_sold
        unit_cost = (float(first.get("cost") or 0) / float(first["qty"])) if float(first["qty"] or 0) else 0.0
        realised = round(sum(float(r["pnl"] or 0) for r in sells), 2)
        unrealised = round(sum(float(r["pnl"] or 0) for r in opens), 2)
        open_cost = sum(float(r.get("cost") or 0) for r in opens)
        partial = bool(opens) and bool(sells)
        for r, _ in members:
            r["life"], r["partial"] = life_id, partial
        lifecycles[life_id] = {
            "symbol": first["symbol"], "book": first.get("book"), "pool": first["pool"], "kind": first.get("kind"),
            "short": entry_action != "BUY",
            "entry": {"date": first.get("bought_on"), "qty": qty_bought, "price": entry_price, "value": round(qty_bought * unit_cost, 2)},
            "sells": [{"date": r["date"], "time": r.get("time") or "", "qty": r["qty"], "price": r["price"], "value": r["amount"],
                       "pnl": r["pnl"], "reason": r.get("note") or ""} for r in sells],
            "open": ({"qty": qty_left, "cost": round(open_cost, 2), "pnl": unrealised, "value_now": round(open_cost + unrealised, 2)} if opens else None),
            "qty_bought": qty_bought, "qty_sold": qty_sold, "qty_left": qty_left,
            "realised": realised, "unrealised": unrealised, "total": round(realised + unrealised, 2),
        }
    return lifecycles


def _ledger(state_dir: str, books: list, d_pf: dict, d_trades: list, today: date,
            pool_e: Optional[dict] = None, d_open: Optional[list] = None,
            prev_close: Optional[dict] = None, crypto_prev_close: Optional[dict] = None,
            prices: Optional[dict] = None, crypto_prices: Optional[dict] = None,
            pool_g: Optional[dict] = None, lifecycles: Optional[dict] = None,
            pool_e1: Optional[dict] = None) -> list:
    """One list for the Live day tab: EVERY open position (whenever it was
    bought, with its current P&L) plus EVERY closed trade on record, across
    Pools A, B, C, D and E. Each row carries `date` (the exit date for a
    closed trade, the entry date for an open one) so the page can filter
    by date range; fill_today marks rows bought or sold today.

    pnl_today on an open row = (latest price - reference) x quantity,
    where the reference is yesterday's close for a position held from
    before today and the entry price for one bought today -- the day's
    move on that position. Summed with the pool's booked-today figure it
    gives "today's P&L including open positions" without any baseline
    capture at the open. Missing prices contribute 0."""
    today_iso = today.isoformat()
    prev_close, crypto_prev_close = prev_close or {}, crypto_prev_close or {}
    prices, crypto_prices = prices or {}, crypto_prices or {}
    rows = []

    def day_move(symbol_key: str, entry_price: float, qty: float, entered_today: bool, price: Optional[float],
                 prev: Optional[float]) -> Optional[float]:
        if price is None:
            return None
        ref = entry_price if entered_today else prev
        return round((float(price) - float(ref)) * qty, 2) if ref else None

    for b in books:
        book_dir = os.path.join(state_dir, POOL_DIRS[b["pool"]], b["key"] if b["pool"] in ("A", "F") else "")
        pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
        unbooked_by_symbol = {d["symbol"]: d["unbooked"] for d in b.get("positions_detail", [])}
        for symbol, p in (pf.get("positions") or {}).items():
            bare = symbol.replace(".NS", "")
            entered_today = p.get("entry_date") == today_iso
            qty, entry = int(p["quantity"]), float(p["entry_price"])
            rows.append({"date": p.get("entry_date"), "time": "09:30" if entered_today else "", "action": "BUY",
                         "symbol": bare, "symbol_key": symbol, "book_key": b["key"] if b["pool"] in ("A", "F") else None,
                         "qty": qty, "price": round(entry, 2),
                         "pool": POOL_LABELS[b["pool"]], "book": b["display_name"], "status": "Open",
                         "pnl": unbooked_by_symbol.get(bare), "note": "", "fill_today": entered_today,
                         "pnl_today": day_move(symbol, entry, qty, entered_today, prices.get(symbol), prev_close.get(symbol)),
                         "bought_on": p.get("entry_date"), "held_days": _days_between(p.get("entry_date"), today_iso),
                         "cost": entry * qty})
        for t in _read_jsonl(os.path.join(book_dir, "trades.jsonl")):
            rows.append({"date": t.get("exit_date"), "time": "09:30", "action": "SELL",
                         "symbol": str(t.get("symbol", "")).replace(".NS", ""),
                         "symbol_key": t.get("symbol"), "book_key": b["key"] if b["pool"] in ("A", "F") else None,
                         "qty": t.get("quantity"), "price": round(float(t.get("exit_price", 0) or 0), 2),
                         "pool": POOL_LABELS[b["pool"]], "book": b["display_name"], "status": "Closed",
                         "fill_today": t.get("exit_date") == today_iso, "bought_on": t.get("entry_date"),
                         "held_days": _days_between(t.get("entry_date"), t.get("exit_date")),
                         "entry_price": float(t.get("entry_price", 0) or 0), "cost": float(t.get("entry_price", 0) or 0) * float(t.get("quantity", 0) or 0),
                         "pnl": round(float(t.get("pnl", 0) or 0), 2),
                         "note": (t.get("exit_reason") or t.get("reason") or "exit").replace("_", " ")})
    d_unbooked = {o["symbol"]: o["unbooked"] for o in (d_open or [])}
    for symbol, p in (d_pf.get("positions") or {}).items():
        ts = p.get("entry_timestamp", "")
        rows.append({"date": ts[:10] or today_iso, "time": ts[11:16],
                     "action": "SHORT" if p.get("direction") == "SELL" else "BUY",
                     "direction": p.get("direction", "BUY"),
                     "symbol": symbol, "symbol_key": symbol, "book_key": None,
                     "qty": p.get("quantity"), "price": round(float(p["entry_price"]), 2),
                     "pool": "Pool D", "book": "Intraday", "status": "Open", "fill_today": ts.startswith(today_iso),
                     "pnl": d_unbooked.get(symbol), "pnl_today": d_unbooked.get(symbol), "note": "",
                     "bought_on": ts[:10] or today_iso, "held_days": 0,
                     "cost": float(p["entry_price"]) * float(p.get("quantity", 0) or 0)})
    for t in d_trades:
        opened, closed = t.get("entry_timestamp", ""), t.get("exit_timestamp", "")
        side_in = "SELL" if t.get("direction") == "SELL" else "BUY"
        exit_date = t.get("exit_date") or closed[:10]
        # One row per closed intraday trade: the exit, carrying the P&L (the entry leg is implied).
        rows.append({"date": exit_date, "time": closed[11:16] if closed else "",
                     "action": "BUY" if side_in == "SELL" else "SELL",
                     "symbol": t.get("symbol"), "qty": t.get("quantity"),
                     "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": "Pool D", "book": "Intraday",
                     "status": "Closed", "fill_today": exit_date == today_iso, "pnl": round(float(t.get("pnl", 0) or 0), 2),
                     "bought_on": opened[:10] or exit_date, "held_days": 0,
                     "entry_price": float(t.get("entry_price", 0) or 0), "cost": float(t.get("entry_price", 0) or 0) * float(t.get("quantity", 0) or 0),
                     "note": str(t.get("reason", "")).replace("_", " ")})
    for r in rows:
        r["amount"] = round(float(r["price"] or 0) * int(r["qty"] or 0), 2)
        r["kind"] = "Intraday" if r["pool"] == "Pool D" else "Swing"
    # Pool E (and its partial-booking twin, Pool E1, 2026-09-22): the UTC daily close is 05:30 IST;
    # prices in USDT, amounts and P&L in rupees (gross, before fees and tax).
    def crypto_rows(pool_dict, pool_label, dirname):
        rate = float((pool_dict or {}).get("usdinr") or 0)
        for b in (pool_dict or {}).get("books", []):
            for p in b["open_positions"]:
                entered_today = p.get("entry_date") == today_iso
                move = day_move(p["symbol"], p["entry_price"], p["quantity"], entered_today,
                                crypto_prices.get(p["symbol"]), crypto_prev_close.get(p["symbol"]))
                rows.append({"date": p.get("entry_date"), "time": "05:30" if entered_today else "", "action": "BUY",
                             "symbol": p["symbol"], "symbol_key": p["symbol"], "book_key": b["key"],
                             "qty": p["quantity"], "price": round(p["entry_price"], 2), "pool": pool_label,
                             "book": b["display_name"], "status": "Open", "fill_today": entered_today,
                             "pnl": round(p["unbooked_raw"] * rate, 2),
                             "pnl_today": round(move * rate, 2) if move is not None else None,
                             "bought_on": p.get("entry_date"), "held_days": _days_between(p.get("entry_date"), today_iso),
                             "cost": p["entry_price"] * p["quantity"] * rate,
                             "note": "price in USDT; P&L in Rs., before fees and tax", "kind": "Crypto",
                             "amount": round(p["entry_price"] * p["quantity"] * rate, 2)})
            for t in _read_jsonl(os.path.join(state_dir, dirname, b["key"], "trades.jsonl")):
                qty = float(t.get("quantity", 0) or 0)
                rows.append({"date": t.get("exit_date"), "time": "05:30", "action": "SELL", "symbol": t.get("symbol"),
                             "qty": qty, "symbol_key": t.get("symbol"), "book_key": b["key"],
                             "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": pool_label,
                             "book": b["display_name"], "status": "Closed", "fill_today": t.get("exit_date") == today_iso,
                             "bought_on": t.get("entry_date"), "held_days": _days_between(t.get("entry_date"), t.get("exit_date")),
                             "entry_price": float(t.get("entry_price", 0) or 0), "cost": float(t.get("entry_price", 0) or 0) * qty * rate,
                             "pnl": round(float(t.get("pnl", 0) or 0) * rate, 2),
                             "note": f"{str(t.get('exit_reason') or 'exit').replace('_', ' ')} (price in USDT)",
                             "kind": "Crypto", "amount": round(float(t.get("exit_price", 0) or 0) * qty * rate, 2)})
    crypto_rows(pool_e, "Pool E", "pool_e")
    crypto_rows(pool_e1, "Pool E1", "pool_e1")
    # Pool G: a single shared book, twice-daily live-price fills, prices/P&L in USDT converted to rupees.
    g_rate = float((pool_g or {}).get("usdinr") or 0)
    for p in (pool_g or {}).get("open_positions", []):
        entered_today = p.get("entry_date") == today_iso
        move = day_move(p["symbol"], p["entry_price"], p["quantity"], entered_today,
                        crypto_prices.get(p["symbol"]), crypto_prev_close.get(p["symbol"]))
        rows.append({"date": p.get("entry_date"), "time": "", "action": "BUY",
                     "symbol": p["symbol"], "symbol_key": p["symbol"], "book_key": None,
                     "qty": p["quantity"], "price": round(p["entry_price"], 2), "pool": "Pool G",
                     "book": "AI judgment", "status": "Open", "fill_today": entered_today,
                     "pnl": round(p["unbooked_raw"] * g_rate, 2),
                     "pnl_today": round(move * g_rate, 2) if move is not None else None,
                     "bought_on": p.get("entry_date"), "held_days": _days_between(p.get("entry_date"), today_iso),
                     "cost": p["entry_price"] * p["quantity"] * g_rate,
                     "note": p.get("reasoning", "") or "price in USDT; P&L in Rs., before fees and tax", "kind": "Crypto",
                     "amount": round(p["entry_price"] * p["quantity"] * g_rate, 2)})
    for t in _read_jsonl(os.path.join(state_dir, "pool_g", "trades.jsonl")):
        qty = float(t.get("quantity", 0) or 0)
        rows.append({"date": t.get("exit_date"), "time": "", "action": "SELL", "symbol": t.get("symbol"),
                     "qty": qty, "symbol_key": t.get("symbol"), "book_key": None,
                     "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": "Pool G",
                     "book": "AI judgment", "status": "Closed", "fill_today": t.get("exit_date") == today_iso,
                     "bought_on": t.get("entry_date"), "held_days": _days_between(t.get("entry_date"), t.get("exit_date")),
                     "entry_price": float(t.get("entry_price", 0) or 0), "cost": float(t.get("entry_price", 0) or 0) * qty * g_rate,
                     "pnl": round(float(t.get("pnl", 0) or 0) * g_rate, 2),
                     "note": f"{str(t.get('reason', '')) or t.get('exit_reason', '')} (price in USDT)",
                     "kind": "Crypto", "amount": round(float(t.get("exit_price", 0) or 0) * qty * g_rate, 2)})
    life = _attach_lifecycles(rows)
    if lifecycles is not None:
        lifecycles.update(life)
    for r in rows:
        cost = float(r.pop("cost", 0) or 0)
        r["pct"] = round(float(r["pnl"]) / cost * 100, 2) if r.get("pnl") is not None and cost > 0 else None
        r.setdefault("pnl_today", None)
        # Live day tape's P&L column toggles between total and today's -- pct_today is today's
        # move as a % of cost, the same way pct is the total move as a % of cost.
        r["pct_today"] = round(float(r["pnl_today"]) / cost * 100, 2) if r.get("pnl_today") is not None and cost > 0 else None
    rows.sort(key=lambda r: (r["date"] or "", r["time"] or "", r["symbol"] or ""), reverse=True)   # newest first; unstamped last within a day
    return rows
