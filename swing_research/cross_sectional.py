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

import numpy as np
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


SHORT_TERM_REVERSAL_FORMATION_DAYS = 21  # 1 month -- Jegadeesh (1990)'s headline lag


def compute_short_term_reversal_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Jegadeesh (1990)'s 1-month formation-period return -- same single-
    period cumulative-return construction as compute_momentum_score(),
    just a much shorter (21 trading day) lookback. Kept as its own
    dedicated function (not a shared/parametrized helper) per this
    program's established precedent -- each strategy's cross-sectional
    signal is independently named and disclosed, never silently shared
    between strategies even when the underlying math is structurally
    similar. No .shift() needed before .rolling() -- no lookahead.
    """
    close = price_history["Close"]
    return close / close.shift(SHORT_TERM_REVERSAL_FORMATION_DAYS) - 1


def compute_short_term_reversal_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of reversal_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN, i.e. >=21 bars of history) formation
    return that day, of its own 1-month formation-period return. Same
    vectorized .rank(axis=1, pct=True) construction as every other
    cross-sectional signal in this module -- LOW percentile here means a
    WORSE recent return (this strategy's entry condition is percentile
    <=10, the bottom decile, the reverse polarity of every momentum
    strategy's >=90 threshold).
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_short_term_reversal_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = wide.rank(axis=1, pct=True) * 100
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


# Frazzini & Pedersen (2014) beta estimator: beta = rho x (sigma_i / sigma_m),
# shrunk 0.6/0.4 toward 1.0. BETA_LOOKBACK_DAYS is an APPROVED DEVIATION
# (2026-08-15) from the paper's own preferred 5-year (minimum 3-year)
# volatility window -- that window is structurally incompatible with this
# program's frozen 3-year recent-period check (acceptance_criteria.py,
# RECENT_PERIOD_YEARS=3, never modified): a 3-5 year warm-up would consume
# the entire recent-period slice with zero days left to trade. 1 year
# (matching the paper's own correlation window) is the shortest choice that
# still lets the frozen _feasible_window_count machinery produce a
# meaningful recent-period result. See
# swing_research/strategy_library/betting_against_beta.md and
# swing_research/published_research_analyst.py's BETTING_AGAINST_BETA record
# for the full disclosed reasoning.
BETA_LOOKBACK_DAYS = 252            # 1 year -- both sigma AND rho windows (approved deviation for sigma)
BETA_CORRELATION_RETURN_LAG_DAYS = 3  # overlapping 3-day log returns, paper-exact (approved 2026-08-15)
BETA_SHRINKAGE_WEIGHT = 0.6         # w in beta = w*beta_TS + (1-w)*1 -- the paper's own stated constant


def _overlapping_log_returns(close: pd.Series, lag_days: int) -> pd.Series:
    """log(Close_t) - log(Close_{t-lag_days}) computed for every day t --
    an OVERLAPPING series (each value shares lag_days-1 days with its
    neighbors), the paper's own deliberate construction to correct for
    non-synchronous/thin-trading understatement of correlation, not a
    naive/accidental overlap."""
    log_close = np.log(close)
    return log_close - log_close.shift(lag_days)


def compute_shrunk_beta_score(price_history: pd.DataFrame, market_close: pd.Series) -> pd.Series:
    """
    Frazzini & Pedersen (2014) beta, faithful to the paper's rho x
    (sigma_i/sigma_m) construction and 0.6/0.4 shrinkage, with the
    volatility/correlation lookback shortened to 1 year (BETA_LOOKBACK_DAYS
    -- see module-level comment above for why). No .shift() needed before
    .rolling()/.corr() -- consistent with every other score function in
    this module, the value at row i uses only data up to and including row
    i's own Close, no lookahead.

    market_close: the market index's (Nifty 50) own daily Close series,
    reindexed to price_history's own dates and forward-filled for any date
    the index itself is missing but the stock traded -- this is the first
    function in this module needing an EXTERNAL series alongside a single
    symbol's own price_history (every prior compute_*_score() function is
    self-contained).
    """
    stock_close = price_history["Close"]
    aligned_market = market_close.reindex(stock_close.index).ffill()

    stock_r3 = _overlapping_log_returns(stock_close, BETA_CORRELATION_RETURN_LAG_DAYS)
    market_r3 = _overlapping_log_returns(aligned_market, BETA_CORRELATION_RETURN_LAG_DAYS)
    rho = stock_r3.rolling(BETA_LOOKBACK_DAYS).corr(market_r3)

    stock_r1 = np.log(stock_close) - np.log(stock_close.shift(1))
    market_r1 = np.log(aligned_market) - np.log(aligned_market.shift(1))
    sigma_i = stock_r1.rolling(BETA_LOOKBACK_DAYS).std()
    sigma_m = market_r1.rolling(BETA_LOOKBACK_DAYS).std()

    beta_ts = rho * (sigma_i / sigma_m)
    return BETA_SHRINKAGE_WEIGHT * beta_ts + (1 - BETA_SHRINKAGE_WEIGHT) * 1.0


def compute_shrunk_beta_percentile_ranks(data: dict, market_close: pd.Series) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}. market_close: the
    market index's own daily Close series (see compute_shrunk_beta_score()).

    Returns {symbol: pd.Series of beta_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN) shrunk beta that day, of its own
    estimated systematic risk. LOW percentile means LOW beta -- this
    strategy's entry condition is percentile <=10 (bottom decile, lowest
    beta), the same polarity convention as
    compute_short_term_reversal_percentile_ranks()'s bottom-decile
    threshold. Same vectorized .rank(axis=1, pct=True) construction as
    every other cross-sectional signal in this module.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_shrunk_beta_score(df.sort_index(), market_close)

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


AMIHUD_ILLIQ_FORMATION_DAYS = 252  # ~1 year -- Amihud (2002)'s own preferred annual measurement
                                    # window, UNCHANGED (unlike Betting Against Beta's beta lookback,
                                    # this window fits comfortably within the frozen 3-year
                                    # recent-period check: 252 + MIN_TRADEABLE_DAYS_PER_WINDOW(60) = 312,
                                    # 756 // 312 = 2 feasible windows -- no shortening needed).
                                    # DISTINCT from execution_realism_engine.py's own trailing-ILLIQ
                                    # use for the EXECUTION COST estimate (20-day lookback, a
                                    # different purpose -- short-horizon liquidity AT THE MOMENT OF
                                    # A TRADE, not this strategy's own annual formation SIGNAL).


def compute_amihud_illiq_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of illiq_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN, i.e. >=252 bars of history) trailing
    252-day Amihud ILLIQ that day, of its own illiquidity. HIGH percentile
    means HIGH illiquidity (hard to trade) -- this strategy's entry
    condition is percentile >=90 (the top decile, MOST illiquid), the
    same polarity convention as every momentum strategy's >=90 threshold
    (compute_short_term_reversal_percentile_ranks() is the only strategy
    using the opposite, <=10, polarity).

    Reuses swing_research.execution_realism_engine.compute_trailing_illiq()
    -- the SAME Amihud (2002) ILLIQ formula (Close x Volume rupee-volume
    proxy, disclosed as the standard practice this platform already
    established) -- with THIS strategy's own 252-day formation window,
    not execution_realism_engine.py's own 20-day execution-cost-estimate
    window. One function, two callers, two different (disclosed, distinct-
    purpose) lookback lengths -- not a duplicated formula.
    """
    from swing_research.execution_realism_engine import compute_trailing_illiq

    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_trailing_illiq(df.sort_index(), lookback_days=AMIHUD_ILLIQ_FORMATION_DAYS)

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = wide.rank(axis=1, pct=True) * 100
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}
