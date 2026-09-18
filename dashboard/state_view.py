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

from deployment.base import is_crypto_record, is_pool_a_record
from reporting.pool_e import _ledger as _crypto_ledger
from swing_research.crypto_costs import INDIA_VDA_TAX_RATE, CryptoCostModel
from reporting.pool_summary import _book, _read_json, _read_jsonl, build_pool_summary  # noqa: F401

# Pool A1 (the legacy wind-down books) is deliberately absent from the
# dashboard, per explicit direction 2026-09-11 -- it stays in the daily
# Telegram summary only.
POOL_DIRS = {"A": "paper_trading", "B": "portfolio_b", "C": "portfolio_c", "F": "pool_f"}
POOL_LABELS = {"A": "Pool A", "B": "Pool B", "C": "Pool C", "D": "Pool D", "E": "Pool E", "F": "Pool F"}

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
    {"pool": "E", "name": "Crypto trends", "text": "Rule-based trend following on BTC, ETH, BNB, XRP and SOL, one 1,000 USDT book per strategy. All P&L shown is AFTER fees and 31.2% tax (open positions as if sold now)."},
    {"pool": "F", "name": "Pool A with partial profit booking", "text": "The same strategies as Pool A on fresh books, but at +5% half is sold and the stop on the rest moves to entry."},
    {"pool": "G", "name": "AI crypto judgment", "text": "The AI calls buy, sell or hold on the same five coins twice a day, with a fixed 18% stop; no backtest, judged live. All P&L shown is AFTER fees and 31.2% tax, same as Pool E."},
]


DESKS = [
    {"id": "research_lab", "name": "Intraday Research Lab (feeds Pool D)", "icon": "\U0001F52C",
     "blurb": "Dreams up intraday ideas and tests them to destruction on real 5-minute data."},
    {"id": "swing_research", "name": "Swing Research (feeds Pool A)", "icon": "\U0001F4DA",
     "blurb": "Takes strategies from the academic literature and proves them on years of NSE data."},
    {"id": "trading_desk", "name": "Trading Desk (Pools A, D and F)", "icon": "\U0001F4C8",
     "blurb": "Runs every approved strategy as a paper book, day after day."},
    {"id": "portfolio_team", "name": "Portfolio Team (Pools B and C)", "icon": "\U0001F9E0",
     "blurb": "AI analysts who debate each candidate the way a small fund's team would."},
    {"id": "crypto_desk", "name": "Crypto Desk (Pools E and G)", "icon": "\u20BF",
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
     "job": "Runs Pool E after the 00:00 UTC close", "status_from": "pool_e",
     "detail": "Same paper engine as Pool A on a 1,000 USDT book: month-end decisions from the strategy, 20% "
               "stops checked daily, fractional coins, fills at the close it just saw (crypto never closes)."},
    {"id": "crypto_judge", "avatar": {"type": "robot", "body": "#8A5A9E", "eye": "#F2C14E", "shape": "round"}, "name": "Crypto Judge", "icon": "\U0001F52E", "desk": "crypto_desk", "kind": "AI",
     "job": "Calls BUY/SELL/HOLD on the majors, twice a day", "status_from": "pool_g",
     "detail": "No backtest behind this one -- a live judgment call on BTC, ETH, BNB, XRP and SOL each run. A "
               "mechanical 18% stop protects every position regardless of what the model says; the model is "
               "told the tax cost of flipping a winning position and asked to avoid pointless churn."},
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
            {"id": "pool_g", "icon": "\U0001F52E", "label": "08:30 & 20:30", "text": "Crypto Judge calls BUY/SELL/HOLD on the majors, twice a day"},
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
    """The earliest date THIS BOOK has any recorded activity -- the
    smaller of its earliest closed trade's entry_date and its earliest
    still-open position's entry_date, read straight from its own
    portfolio.json. None if the book has never entered anything."""
    dates = [t.get("entry_date") for t in trades if t.get("entry_date")]
    pf = _read_json(os.path.join(book_dir, "portfolio.json")) or {}
    dates += [p.get("entry_date") for p in (pf.get("positions") or {}).values() if p.get("entry_date")]
    return min(dates) if dates else None


def _strategy_pool_breakdown(key: str, books: list, state_dir: str, d_trades: list, pool_d: dict,
                             pool_e: dict, pool_g: dict) -> list:
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
                    "pnl": round((pool_g["booked"]["post_tax"] + pool_g["unbooked"]["post_tax"]) * rate, 2),
                    "closed_trades": len(trades), "wins": _wins(trades), "started": _book_started(book_dir, trades)})
    if pool_e.get("exists"):
        eb = next((b for b in pool_e["books"] if b.get("key") == key), None)
        if eb:
            rate = pool_e.get("usdinr") or 0
            book_dir = os.path.join(state_dir, "pool_e", key)
            trades = _read_jsonl(os.path.join(book_dir, "trades.jsonl"))
            out.append({"pool": "Pool E", "capital": round(eb["capital"] * rate, 2),
                        "pnl": round((eb["booked"]["post_tax"] + eb["unbooked"]["post_tax"]) * rate, 2),
                        "closed_trades": len(trades), "wins": _wins(trades), "started": _book_started(book_dir, trades)})
    return out


def strategies_view(registry_records: list, pool_d_strategy: str, pool_f_keys: Optional[set] = None,
                    books: Optional[list] = None, pool_d: Optional[dict] = None,
                    pool_e: Optional[dict] = None, pool_g: Optional[dict] = None,
                    state_dir: str = "", d_trades: Optional[list] = None) -> list:
    """Every strategy the desk knows, with its pool, type, plain-language
    brief, capital allocated, total P&L to date, closed-trade count,
    reward:risk ratio, and the registry's verdict/status -- Pools B, C
    and D included even though they are not registry strategies."""
    books, pool_d, pool_e, pool_g, d_trades = books or [], pool_d or {}, pool_e or {}, pool_g or {}, d_trades or []
    rows = []
    for r in registry_records:
        status = str(getattr(r.deployment_status, "value", r.deployment_status)).split(".")[-1]
        crypto = is_crypto_record(r)
        fixed_pool = {"portfolio_b": "Pool B", "portfolio_c": "Pool C", "pool_d_vwap_fade": "Pool D"}.get(r.strategy_key)
        kind, brief = STRATEGY_BRIEFS.get(r.strategy_key, ("Crypto" if crypto else "Swing", ""))
        if fixed_pool:
            pool = fixed_pool if status == "PAPER_TRADING" else "-"
        else:
            pool = ("Pool E" if crypto else "Pool A") if status == "PAPER_TRADING" else "-"
        if pool == "Pool A" and r.strategy_key in (pool_f_keys or set()):
            pool = "Pool A, F"
        exp = getattr(r, "primary_experiment_id", "") or ""
        pools_breakdown = _strategy_pool_breakdown(r.strategy_key, books, state_dir, d_trades, pool_d, pool_e, pool_g)
        capital = round(sum(p["capital"] for p in pools_breakdown), 2) if pools_breakdown else None
        pnl = round(sum(p["pnl"] for p in pools_breakdown), 2) if pools_breakdown else None
        closed_trades = sum(p["closed_trades"] for p in pools_breakdown) if pools_breakdown else None
        wins = sum(p["wins"] for p in pools_breakdown) if pools_breakdown else None
        rows.append({"key": r.strategy_key, "sid": getattr(r, "strategy_id", ""), "name": r.display_name, "pool": pool,
                     "type": kind, "verdict": str(getattr(r.research_verdict, "value", r.research_verdict)).split(".")[-1],
                     "status": status, "experiment": exp, "brief": brief, "capital": capital, "pnl": pnl,
                     "started": _paper_trading_started(r), "closed_trades": closed_trades, "wins": wins,
                     "pools_breakdown": pools_breakdown,
                     "how": STRATEGY_HOW.get(r.strategy_key, {}), "research": _experiment_summary(exp),
                     # one tab per pool the strategy runs in; a twin pool carries only what it changes
                     "variants": ([{"pool": "Pool A", "diff": False},
                                   {"pool": "Pool F", "diff": True, "title": "What Pool F does differently",
                                    "brief": STRATEGY_BRIEFS["pool_f"][1], "how": STRATEGY_HOW["pool_f"]}]
                                  if pool == "Pool A, F" else [])})
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
                          crypto_prices: Optional[dict] = None, usdinr: Optional[float] = None,
                          prev_close: Optional[dict] = None, crypto_prev_close: Optional[dict] = None) -> dict:
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
    from reporting.pool_g import build_pool_g
    from data.fetch_crypto import DEFAULT_USDINR
    pool_g = build_pool_g(state_dir, crypto_prices, usdinr or DEFAULT_USDINR, today)
    g_rate = pool_g.get("usdinr") or 0
    overall = dict(summary["overall"])   # already carries Pool E's and Pool G's post-tax rupee figures (reporting/pool_summary.py)
    overall["capital"] = round(sum(p["capital"] for p in pools.values()) + pool_d["capital"]
                               + pool_e["inr"]["capital"] + pool_g.get("capital", 0) * g_rate, 2)
    overall["unrealised"] = round(overall["unrealised"] + pool_d["unrealised"], 2)
    return {
        "mode": mode, "generated_at": now.isoformat(timespec="seconds"), "today": today.isoformat(),
        "market_open": market_open, "prices_as_of": prices_as_of, "priced_symbols": len(prices),
        "quotes": {k: round(float(v), 2) for k, v in prices.items()},
        "pools": pools, "overall": overall, "books": books, "pool_d": pool_d, "pool_e": pool_e, "pool_g": pool_g,
        "ledger": _ledger(state_dir, books, d_pf, d_trades, today, pool_e, d_open,
                          prev_close=prev_close, crypto_prev_close=crypto_prev_close,
                          prices=prices, crypto_prices=crypto_prices, pool_g=pool_g),
        "schedule": schedule, "registry": registry, "agents": AGENTS, "desks": DESKS, "flows": FLOWS, "pools_info": POOLS_INFO,
        "strategies": strategies_view(registry_records, "VWAP Extension Exhaustion Fade",
                                      {b["key"] for b in summary["books"].get("F", [])},
                                      books=books, pool_d=pool_d, pool_e=pool_e, pool_g=pool_g,
                                      state_dir=state_dir, d_trades=d_trades),
        "roadmap": roadmap_view(roadmap, registry_records) if roadmap else {"ready": [], "deferred": [], "weights": {}},
    }


def _closed_crypto_post_tax(t: dict) -> float:
    """A closed crypto trade's profit AFTER fees and India's 31.2% tax, in USDT -- the same
    per-trade arithmetic reporting/pool_e.py uses for the book totals, so the Live day rows add
    up to the totals shown everywhere else."""
    qty = float(t.get("quantity", 0) or 0)
    led = _crypto_ledger(float(t.get("pnl", 0) or 0), float(t.get("entry_price", 0) or 0) * qty,
                         float(t.get("exit_price", 0) or 0) * qty, CryptoCostModel(), INDIA_VDA_TAX_RATE)
    return led["post_tax"]


def _days_between(start_iso, end_iso) -> Optional[int]:
    try:
        return (date.fromisoformat(end_iso) - date.fromisoformat(start_iso)).days
    except (TypeError, ValueError):
        return None


def _ledger(state_dir: str, books: list, d_pf: dict, d_trades: list, today: date,
            pool_e: Optional[dict] = None, d_open: Optional[list] = None,
            prev_close: Optional[dict] = None, crypto_prev_close: Optional[dict] = None,
            prices: Optional[dict] = None, crypto_prices: Optional[dict] = None,
            pool_g: Optional[dict] = None) -> list:
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
                         "cost": float(t.get("entry_price", 0) or 0) * float(t.get("quantity", 0) or 0),
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
                     "cost": float(t.get("entry_price", 0) or 0) * float(t.get("quantity", 0) or 0),
                     "note": str(t.get("reason", "")).replace("_", " ")})
    for r in rows:
        r["amount"] = round(float(r["price"] or 0) * int(r["qty"] or 0), 2)
        r["kind"] = "Intraday" if r["pool"] == "Pool D" else "Swing"
    # Pool E: the UTC daily close is 05:30 IST; prices in USDT, amounts and P&L in rupees (post-tax on open rows).
    rate = float((pool_e or {}).get("usdinr") or 0)
    for b in (pool_e or {}).get("books", []):
        for p in b["open_positions"]:
            entered_today = p.get("entry_date") == today_iso
            move = day_move(p["symbol"], p["entry_price"], p["quantity"], entered_today,
                            crypto_prices.get(p["symbol"]), crypto_prev_close.get(p["symbol"]))
            rows.append({"date": p.get("entry_date"), "time": "05:30" if entered_today else "", "action": "BUY",
                         "symbol": p["symbol"], "symbol_key": p["symbol"], "book_key": b["key"],
                         "qty": p["quantity"], "price": round(p["entry_price"], 2), "pool": "Pool E",
                         "book": b["display_name"], "status": "Open", "fill_today": entered_today,
                         "pnl": round(p["unbooked_post_tax"] * rate, 2),
                         "pnl_today": round(move * rate, 2) if move is not None else None,
                         "bought_on": p.get("entry_date"), "held_days": _days_between(p.get("entry_date"), today_iso),
                         "cost": p["entry_price"] * p["quantity"] * rate,
                         "note": "price in USDT; P&L post-tax in Rs.", "kind": "Crypto",
                         "amount": round(p["entry_price"] * p["quantity"] * rate, 2)})
        for t in _read_jsonl(os.path.join(state_dir, "pool_e", b["key"], "trades.jsonl")):
            qty = float(t.get("quantity", 0) or 0)
            rows.append({"date": t.get("exit_date"), "time": "05:30", "action": "SELL", "symbol": t.get("symbol"),
                         "qty": qty, "symbol_key": t.get("symbol"), "book_key": b["key"],
                         "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": "Pool E",
                         "book": b["display_name"], "status": "Closed", "fill_today": t.get("exit_date") == today_iso,
                         "bought_on": t.get("entry_date"), "held_days": _days_between(t.get("entry_date"), t.get("exit_date")),
                         "cost": float(t.get("entry_price", 0) or 0) * qty * rate,
                         "pnl": round(_closed_crypto_post_tax(t) * rate, 2),
                         "note": f"{str(t.get('exit_reason') or 'exit').replace('_', ' ')} (after fees and tax)",
                         "kind": "Crypto", "amount": round(float(t.get("exit_price", 0) or 0) * qty * rate, 2)})
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
                     "pnl": round(p["unbooked_post_tax"] * g_rate, 2),
                     "pnl_today": round(move * g_rate, 2) if move is not None else None,
                     "bought_on": p.get("entry_date"), "held_days": _days_between(p.get("entry_date"), today_iso),
                     "cost": p["entry_price"] * p["quantity"] * g_rate,
                     "note": p.get("reasoning", "") or "price in USDT; P&L post-tax in Rs.", "kind": "Crypto",
                     "amount": round(p["entry_price"] * p["quantity"] * g_rate, 2)})
    for t in _read_jsonl(os.path.join(state_dir, "pool_g", "trades.jsonl")):
        qty = float(t.get("quantity", 0) or 0)
        rows.append({"date": t.get("exit_date"), "time": "", "action": "SELL", "symbol": t.get("symbol"),
                     "qty": qty, "symbol_key": t.get("symbol"), "book_key": None,
                     "price": round(float(t.get("exit_price", 0) or 0), 2), "pool": "Pool G",
                     "book": "AI judgment", "status": "Closed", "fill_today": t.get("exit_date") == today_iso,
                     "bought_on": t.get("entry_date"), "held_days": _days_between(t.get("entry_date"), t.get("exit_date")),
                     "cost": float(t.get("entry_price", 0) or 0) * qty * g_rate,
                     "pnl": round(_closed_crypto_post_tax(t) * g_rate, 2),
                     "note": f"{str(t.get('reason', '')) or t.get('exit_reason', '')} (after fees and tax)",
                     "kind": "Crypto", "amount": round(float(t.get("exit_price", 0) or 0) * qty * g_rate, 2)})
    for r in rows:
        cost = float(r.pop("cost", 0) or 0)
        r["pct"] = round(float(r["pnl"]) / cost * 100, 2) if r.get("pnl") is not None and cost > 0 else None
        r.setdefault("pnl_today", None)
    rows.sort(key=lambda r: (r["date"] or "", r["time"] or "", r["symbol"] or ""), reverse=True)   # newest first; unstamped last within a day
    return rows
