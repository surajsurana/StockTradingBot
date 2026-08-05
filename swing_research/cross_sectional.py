"""
Cross-sectional relative-strength (RS) percentile ranking -- the one
computation Minervini's Trend Template criterion 8 ("RS ranking >= 70th
percentile vs. the universe") needs that no single symbol's own price
history can answer alone. Reusable as-is for the Cross-Sectional Momentum
strategy later in the roadmap, which needs the identical kind of
whole-universe percentile rank.

IBD's own RS Rating formula is proprietary and not publicly documented in
exact form -- compute_rs_score() below is a transparent, commonly-cited
OPEN substitute (a recency-weighted blend of trailing 3/6/9/12-month
returns), disclosed as an approximation, not a claim of replicating IBD's
real algorithm. See swing_research/strategy_library/ for the documented-
rules-vs-assumptions distinction this feeds into.

Vectorized, not a per-day Python loop: builds one wide DataFrame (dates x
symbols) of RS scores and calls pandas' .rank(axis=1, pct=True) ONCE to get
every symbol's cross-sectional percentile for every day simultaneously --
the same lesson learned from the earlier intraday research_lab/market_state.py
cross-sectional work, where per-day loops over hundreds of symbols were the
actual performance bottleneck.
"""

import pandas as pd

# Recency-weighted blend, matching the commonly-cited open approximation of
# IBD's RS Rating: heaviest weight on the most recent quarter.
_RS_WEIGHTS = {"r3": 0.4, "r6": 0.2, "r9": 0.2, "r12": 0.2}
_TRADING_DAYS_PER_MONTH = 21


def compute_rs_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Recency-weighted composite of trailing 3/6/9/12-month returns, using
    Close-to-Close percent change over each lookback (`.shift()` before the
    ratio, so a given row's score never uses data from after that row's
    own date -- no lookahead).
    """
    close = price_history["Close"]
    r3 = close / close.shift(3 * _TRADING_DAYS_PER_MONTH) - 1
    r6 = close / close.shift(6 * _TRADING_DAYS_PER_MONTH) - 1
    r9 = close / close.shift(9 * _TRADING_DAYS_PER_MONTH) - 1
    r12 = close / close.shift(12 * _TRADING_DAYS_PER_MONTH) - 1
    return (_RS_WEIGHTS["r3"] * r3 + _RS_WEIGHTS["r6"] * r6
            + _RS_WEIGHTS["r9"] * r9 + _RS_WEIGHTS["r12"] * r12)


def compute_rs_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of rs_percentile (0-100), indexed by date} --
    each symbol's percentile rank AMONG WHATEVER SYMBOLS HAVE VALID DATA
    that day (pandas' rank(pct=True) naturally excludes NaN rows/symbols
    from that day's ranking rather than penalizing a symbol for another
    symbol's missing data).
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_rs_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)  # outer-joins on the union of all symbols' date indices
    pct_ranks = wide.rank(axis=1, pct=True) * 100
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


MOMENTUM_FORMATION_DAYS = 126  # J=6 months, ~21 trading days/month -- Jegadeesh & Titman
                                # (1993)'s own most-cited J=6/K=6 specification


def compute_momentum_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Jegadeesh & Titman (1993)'s formation-period return: cumulative Close-
    to-Close return over the prior MOMENTUM_FORMATION_DAYS (126) trading
    days, a SINGLE period -- NOT compute_rs_score()'s multi-horizon
    3/6/9/12-month blend (that function is Minervini's own disclosed
    open-approximation of IBD's proprietary RS Rating, a different
    strategy's different undocumented criterion). This is a faithful,
    direct restatement of the paper's own single-J-month formation return,
    for the Cross-Sectional Momentum strategy specifically. No .shift()
    needed before .rolling() -- the return at row i uses only Close
    prices up to and including row i, no lookahead.
    """
    close = price_history["Close"]
    return close / close.shift(MOMENTUM_FORMATION_DAYS) - 1


def compute_momentum_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of momentum_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN, i.e. >=126 bars of history) formation
    return that day, of its own J=6-month formation-period return. Same
    vectorized .rank(axis=1, pct=True) construction as
    compute_rs_percentile_ranks()/compute_52w_high_nearness_percentile_ranks()
    -- one call ranks every symbol for every day simultaneously.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_momentum_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = wide.rank(axis=1, pct=True) * 100
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


LOOKBACK_52W = 252  # trading days -- same convention as
                     # strategies/minervini_trend_template_filter.py's own LOOKBACK_52W


def compute_52w_high_nearness_score(price_history: pd.DataFrame) -> pd.Series:
    """
    George & Hwang (2004)'s nearness ratio: Close / rolling-252-day-high of
    Close. No .shift() needed before .rolling() here -- the ratio at row i
    uses the high INCLUDING today's own close (today's close cannot exceed
    today's own contribution to the rolling high by construction), which
    matches the paper's own "price relative to its 52-week high as of the
    formation date" definition exactly; the cross-sectional percentile
    rank derived from this (compute_52w_high_nearness_percentile_ranks())
    is what actually drives the strategy's entry decision.
    """
    close = price_history["Close"]
    high_52w = close.rolling(LOOKBACK_52W).max()
    return close / high_52w


def compute_52w_high_nearness_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of nearness_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN, i.e. >=252 bars of history) nearness
    ratio that day, of how close its price is to its own 52-week high.
    Same vectorized .rank(axis=1, pct=True) construction as
    compute_rs_percentile_ranks() -- one call ranks every symbol for every
    day simultaneously, not a per-day Python loop.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_52w_high_nearness_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = wide.rank(axis=1, pct=True) * 100
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}
