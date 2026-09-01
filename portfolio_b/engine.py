"""
Portfolio B's fixed watchlist and synthetic-signal builder.

Unlike Portfolio C (portfolio_c/engine.py), there is no anchor strategy
proposing an entry -- Research Analyst/Portfolio Manager/Risk Manager
still need a concrete strategies.base.Signal (entry_price, stop_loss) to
form a verdict and size against, so this module builds one directly from
today's close and a fixed protective stop, NOT from any systematic entry
rule. This is the one genuinely different piece from Portfolio C; every
other stage (Fundamental Agent, News Agent, Research Analyst, Portfolio
Manager, Risk Manager, next-day-open fills) is identical in shape.
"""

from portfolio_b.state import load_watchlist
from strategies.base import Signal as AgentSignal

# Hand-picked, fixed universe -- confirmed 2026-09-01, per explicit
# direction. Resolved to real yfinance tickers and verified fetchable:
# RVNL (Rail Vikas Nigam), Vedanta (VEDL -- covers its aluminium business,
# no separate symbol), Exide Industries, Honasa Consumer, Himadri
# Speciality Chemical (ticker is HSCL, NOT "HIMADRI" -- verified), Eternal
# (the renamed Zomato -- "ZOMATO.NS" no longer resolves), Motilal Oswal
# Nasdaq 100 ETF (the "Nasdaq 100 Motilal" the user meant), and Gold/
# Silver via NSE-listed ETFs (GOLDBEES/SILVERBEES) rather than real MCX
# commodities -- this platform has no commodity data source or
# futures-style paper-trading mechanics, and ETFs need none of that,
# trading exactly like any other NSE equity.
#
# This is only the SEED list, used to create watchlist.json the first
# time this bot ever runs (see portfolio_b/state.py's load_watchlist()).
# After that, deployment/state/portfolio_b/watchlist.json is the single
# source of truth -- edited live via Telegram /addstock and /removestock
# commands (portfolio_b/telegram_bot.py), never by changing this
# constant. Changing this list in code after first run has NO effect on
# an already-running deployment.
DEFAULT_WATCHLIST = [
    "RVNL.NS", "VEDL.NS", "EXIDEIND.NS", "HONASA.NS", "HSCL.NS",
    "ETERNAL.NS", "MON100.NS", "GOLDBEES.NS", "SILVERBEES.NS",
]

# Same disclosed convention every swing_research strategy without a
# strategy-specific stop uses (see e.g.
# swing_research/strategies/max_effect.py's STOP_LOSS_PCT) -- reused
# here rather than inventing a new number, since Portfolio B has no
# strategy of its own to derive one from either.
PROTECTIVE_STOP_PCT = 0.08

# Same display-only convention as portfolio_c/engine.py's
# DISPLAY_ONLY_TARGET_R_MULTIPLE -- nothing in this pipeline sizes or
# exits off `target`; Research Analyst's prompt only displays it.
DISPLAY_ONLY_TARGET_R_MULTIPLE = 2.0


def get_watchlist() -> list:
    """The LIVE watchlist -- reads deployment/state/portfolio_b/watchlist.json
    (seeding it from DEFAULT_WATCHLIST on first ever call). Call this
    fresh each time you need the watchlist rather than caching it -- a
    Telegram /addstock or /removestock command can change it between
    calls, and every consumer (run_portfolio_b.py, the daily cycle)
    should always see the CURRENT list, never a stale one."""
    return load_watchlist(DEFAULT_WATCHLIST)


def build_watchlist_signal(symbol: str, price_history) -> AgentSignal:
    """
    A SYNTHETIC signal, not a strategy's entry proposal: entry_price is
    simply today's close, stop_loss is a fixed 8% below it (long-only,
    matching every swing_research strategy's own long-only convention),
    target is a 2R display-only figure. confidence is a neutral 1.0 --
    Portfolio Manager's actual sizing multiplier comes from Research
    Analyst's own verdict confidence (candidate.research_assessment.
    confidence), never from this field, so it has no functional effect
    here (same as Turtle System 2 / Turn-of-the-Month's neutral default
    in swing_research/candidate_ranking.py, for the same reason: no
    natural ranking signal exists to put here instead).

    price_history: DataFrame with a Close column, most recent row last.
    """
    entry_price = float(price_history["Close"].iloc[-1])
    stop_loss = entry_price * (1 - PROTECTIVE_STOP_PCT)
    target = entry_price + DISPLAY_ONLY_TARGET_R_MULTIPLE * (entry_price - stop_loss)

    return AgentSignal(
        symbol=symbol, direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
        target=target, confidence=1.0, strategy_name="portfolio_b_watchlist",
        reason="Fixed watchlist candidate -- no systematic entry signal; evaluated purely on "
               "recent price action, fundamentals, and news.",
    )
