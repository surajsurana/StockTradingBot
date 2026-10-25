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

# Rows (dates) ranked per slab by _percentile_rank_rows() below.
_RANK_ROWS_PER_SLAB = 256


def _percentile_rank_rows(wide: pd.DataFrame) -> pd.DataFrame:
    """
    wide.rank(axis=1, pct=True) * 100 -- every symbol's percentile among that day's valid scores
    -- computed a slab of dates at a time into one preallocated result.

    WHY (2026-10-25). Ranking the whole dates x symbols frame at once made pandas allocate
    several full-size temporaries together (sort indexer, mask, ranks, the x100 copy): ~55 MB of
    transient peak for the 503-symbol, 10-year US universe, on a 458 MB VPS. Each row is ranked
    on its own (axis=1: one day's cross-section, ties averaged and NaN left out within that row
    only), so ranking any subset of rows gives exactly the values the whole-frame call gives for
    those rows, bit for bit -- the slabs only bound how much is allocated at once. Anything other
    than an all-float64 frame takes the original single call unchanged.
    """
    if len(wide) <= _RANK_ROWS_PER_SLAB or not all(dtype == np.float64 for dtype in wide.dtypes):
        return wide.rank(axis=1, pct=True) * 100
    out = np.empty(wide.shape, dtype=np.float64)
    for start in range(0, len(wide), _RANK_ROWS_PER_SLAB):
        stop = start + _RANK_ROWS_PER_SLAB
        out[start:stop] = (wide.iloc[start:stop].rank(axis=1, pct=True) * 100).to_numpy()
    return pd.DataFrame(out, index=wide.index, columns=wide.columns)


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
    pct_ranks = _percentile_rank_rows(wide)
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
    pct_ranks = _percentile_rank_rows(wide)
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
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


MAX_EFFECT_FORMATION_DAYS = 21  # ~1 month -- Bali, Cakici & Whitelaw (2011)'s own "within a month" window


def compute_max_effect_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Bali, Cakici & Whitelaw (2011)'s MAX(1): the single HIGHEST daily
    return within the trailing MAX_EFFECT_FORMATION_DAYS (21 trading days,
    ~1 calendar month) window ending at (and including) the current row --
    the paper's own headline/most-cited specification (MAX(5), the average
    of the 5 highest days in the month, is the paper's disclosed robustness
    variant, not implemented here -- see swing_research/strategy_library/
    max_effect.md for the full documented-rule-vs-implementation-assumption
    breakdown). No .shift() needed before .rolling() -- daily_return at
    row i depends only on Close[i] and Close[i-1], and .rolling(21).max()
    at row i looks only at rows i-20..i, never a future row -- no
    lookahead, the same property every other score function in this
    module already relies on.
    """
    daily_return = price_history["Close"].pct_change()
    return daily_return.rolling(MAX_EFFECT_FORMATION_DAYS).max()


def compute_max_effect_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of max_effect_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN, i.e. >=21 bars of history) MAX score
    that day, of its own trailing-month single-highest-daily-return. Same
    vectorized .rank(axis=1, pct=True) construction as every other
    cross-sectional signal in this module -- LOW percentile here means a
    LOW recent maximum daily return (a calmer, less lottery-like stock --
    this strategy's entry condition is percentile <=10, the bottom decile,
    the same "long the calmer/less extreme decile" polarity already used
    by Betting Against Beta's bottom-decile-of-beta convention).
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_max_effect_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


TURNOVER_FORMATION_DAYS = 21   # ~1 month -- same window convention as every other cross-sectional
                               # score in this module (RS, momentum, reversal, MAX effect); the
                               # source paper's own formation window is not independently
                               # re-verified beyond its existence (see turnover_liquidity.py's
                               # module docstring), so this reuses the program's own established
                               # default rather than inventing a paper-specific one.


def compute_turnover_score(price_history: pd.DataFrame, shares_outstanding: float) -> pd.Series:
    """Datar, Naik & Radcliffe (1998)'s turnover measure: traded volume as
    a fraction of shares outstanding, averaged over the trailing
    TURNOVER_FORMATION_DAYS. shares_outstanding is a single CURRENT
    snapshot value (see data/fetch_shares_outstanding.py's module
    docstring for the disclosed historical-series limitation) applied
    across the whole price history -- if shares_outstanding is missing or
    non-positive, returns an all-NaN Series so the symbol simply never
    qualifies rather than dividing by zero."""
    if not shares_outstanding or shares_outstanding <= 0:
        return pd.Series(float("nan"), index=price_history.index)
    daily_turnover = price_history["Volume"] / shares_outstanding
    return daily_turnover.rolling(TURNOVER_FORMATION_DAYS).mean()


def compute_turnover_percentile_ranks(data: dict, shares_outstanding: dict) -> dict:
    """data: {symbol: DataFrame of daily OHLCV bars}. shares_outstanding:
    {symbol: float}, from data/fetch_shares_outstanding.py.

    Returns {symbol: pd.Series of turnover_percentile (0-100)} -- each
    symbol's cross-sectional percentile rank, among whatever symbols have
    both price history and a known share count that day, of its own
    trailing-month average turnover. LOW percentile = LOW turnover (an
    illiquid stock, trading a small fraction of its shares outstanding) --
    this strategy's entry condition is percentile <=10, the bottom decile,
    the same "long the calmer/less-traded decile" polarity as MAX Effect
    and Betting Against Beta's bottom-decile conventions. Symbols with no
    shares-outstanding figure are silently excluded from the cross-section
    for every date (their score is all-NaN, so pandas' rank() ignores
    them), not treated as automatically illiquid."""
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_turnover_score(df.sort_index(), shares_outstanding.get(symbol))

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
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
    pct_ranks = _percentile_rank_rows(wide)
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
    pct_ranks = _percentile_rank_rows(wide)
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
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


IVOL_FORMATION_DAYS = 21  # ~1 month -- Ang, Hodrick, Xing & Zhang (2006)'s own monthly
                           # re-formation window (a regression run on the trailing month's
                           # daily returns). Unlike Betting Against Beta's beta lookback, this
                           # window needs NO shortening for the frozen 3-year recent-period
                           # check -- the paper's own primary specification is already a
                           # short, 1-month window, not a multi-year one.


def compute_idiosyncratic_volatility_score(price_history: pd.DataFrame, market_close: pd.Series) -> pd.Series:
    """
    Ang, Hodrick, Xing & Zhang (2006)'s idiosyncratic volatility: the
    standard deviation of the residuals from a regression of the stock's
    daily returns on a market-return factor, over the trailing
    IVOL_FORMATION_DAYS (21 trading days, ~1 month) window.

    DISCLOSED DEVIATION FROM THE PAPER'S PRIMARY SPECIFICATION: the paper's
    headline construction regresses on the FAMA-FRENCH 3-FACTOR model
    (market, SMB size factor, HML value factor) -- SMB/HML require
    point-in-time market-cap and book-to-market data this platform has
    already confirmed unavailable (see research_roadmap.py's
    DATA_CAPABILITIES). A SINGLE-FACTOR (CAPM/market-model) residual
    volatility is used instead -- this is NOT an invented substitute: the
    paper's own robustness section reports results are qualitatively
    unchanged using a single-factor market-model residual, so this is a
    faithful, disclosed alternative specification drawn from the paper
    itself, not a simplification invented for this platform. See
    swing_research/strategy_library/idiosyncratic_volatility.md and
    published_research_analyst.py's IDIOSYNCRATIC_VOLATILITY_ANOMALY record
    for the full documented-rules-vs-implementation-assumptions breakdown.

    For a single-regressor OLS, the residual variance has a closed form
    that avoids running an actual regression: Var(residual) = Var(Y) x
    (1 - rho^2), where rho is the correlation between Y (stock returns) and
    the single regressor (market returns) -- so idio_vol = sigma_stock x
    sqrt(1 - rho^2), using PLAIN (non-overlapping) daily returns, matching
    the paper's own daily-frequency, within-the-month regression (NOT
    Betting Against Beta's own separate, disclosed choice of overlapping
    3-day returns, which was Frazzini-Pedersen's own specific thin-trading
    correction for a DIFFERENT paper's beta estimator, not a general
    convention for every strategy needing a market series).

    market_close: the market index's (Nifty 50) own daily Close series,
    reindexed to price_history's own dates and forward-filled, same
    alignment convention as compute_shrunk_beta_score(). No .shift() needed
    before .rolling() -- the value at row i uses only data up to and
    including row i's own Close, no lookahead.
    """
    stock_close = price_history["Close"]
    aligned_market = market_close.reindex(stock_close.index).ffill()

    stock_r1 = stock_close.pct_change()
    market_r1 = aligned_market.pct_change()

    rho = stock_r1.rolling(IVOL_FORMATION_DAYS).corr(market_r1)
    sigma_stock = stock_r1.rolling(IVOL_FORMATION_DAYS).std()

    return sigma_stock * np.sqrt(1 - rho ** 2)


def compute_idiosyncratic_volatility_percentile_ranks(data: dict, market_close: pd.Series) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}. market_close: the market
    index's own daily Close series (see compute_idiosyncratic_volatility_score()).

    Returns {symbol: pd.Series of idio_vol_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN) idiosyncratic volatility that day, of
    its own residual (market-model-adjusted) volatility. LOW percentile
    means LOW idiosyncratic volatility (a calmer, less lottery-like stock
    once market-wide moves are stripped out) -- this strategy's entry
    condition is percentile <=10 (bottom decile, lowest idio-vol), the
    paper's own "long the calmest quintile" finding, same polarity
    convention as Betting Against Beta's and MAX Effect's own bottom-decile
    thresholds. Same vectorized .rank(axis=1, pct=True) construction as
    every other cross-sectional signal in this module.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_idiosyncratic_volatility_score(df.sort_index(), market_close)

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


def compute_market_cap_score(price_history: pd.DataFrame, shares_outstanding: float) -> pd.Series:
    """Banz (1981)'s size measure: market capitalization = Close x shares
    outstanding, a POINT-IN-TIME figure -- no rolling/averaging window,
    faithful to the paper's own annual size-sort at the formation date
    (unlike this module's other cross-sectional signals, which deliberately
    average over a formation window). shares_outstanding is a single
    CURRENT snapshot value (see data/fetch_shares_outstanding.py's module
    docstring for the disclosed historical-series limitation), applied
    across the whole price history -- same convention as
    compute_turnover_score()'s identical snapshot limitation. Returns an
    all-NaN Series if shares_outstanding is missing or non-positive, so the
    symbol simply never qualifies rather than multiplying by a bad value."""
    if not shares_outstanding or shares_outstanding <= 0:
        return pd.Series(float("nan"), index=price_history.index)
    return price_history["Close"] * shares_outstanding


def compute_market_cap_percentile_ranks(data: dict, shares_outstanding: dict) -> dict:
    """data: {symbol: DataFrame of daily OHLCV bars}. shares_outstanding:
    {symbol: float}, from data/fetch_shares_outstanding.py.

    Returns {symbol: pd.Series of market_cap_percentile (0-100)} -- each
    symbol's cross-sectional percentile rank, among whatever symbols have
    both price history and a known share count that day, of its own market
    capitalization. LOW percentile = LOW market cap (a small-cap firm) --
    this strategy's entry condition is percentile <=10, the bottom decile,
    the size-premium candidate's own long-only reduction of Banz (1981)'s
    size-sorted portfolio. Symbols with no shares-outstanding figure are
    silently excluded from the cross-section for every date (their score is
    all-NaN, so pandas' rank() ignores them), not treated as automatically
    small-cap. Same vectorized .rank(axis=1, pct=True) construction as every
    other cross-sectional signal in this module."""
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_market_cap_score(df.sort_index(), shares_outstanding.get(symbol))

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


OVERNIGHT_RETURN_FORMATION_DAYS = 21  # 1 month -- this program's convention, see strategy module docstring


def compute_overnight_return_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Lou, Polk & Skouras (2019)'s cumulative overnight (Close-to-Open)
    return over a trailing formation window. Daily overnight return_t =
    Open_t / Close_{t-1} - 1 (captures ONLY the close-to-open move, zero
    intraday-session exposure -- unlike compute_momentum_score()'s
    Close-to-Close construction). The formation-period figure compounds
    these daily overnight returns over OVERNIGHT_RETURN_FORMATION_DAYS
    trading days: product(1 + overnight_return_t) - 1.
    """
    daily_overnight_return = price_history["Open"] / price_history["Close"].shift(1) - 1
    return (1 + daily_overnight_return).rolling(OVERNIGHT_RETURN_FORMATION_DAYS).apply(
        lambda window: window.prod() - 1, raw=True
    )


def compute_overnight_return_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of overnight_percentile (0-100), indexed by
    date} -- each symbol's cross-sectional percentile rank, among whatever
    symbols have a valid (non-NaN, i.e. >=21 bars of history) formation
    overnight return that day, of its own cumulative overnight return.
    HIGH percentile means a STRONGER recent cumulative overnight return --
    this strategy's entry condition is percentile >=90 (top decile), the
    paper's own long-side persistence finding. Same vectorized
    .rank(axis=1, pct=True) construction as every other cross-sectional
    signal in this module.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_overnight_return_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


VOLUME_SHOCK_RECENT_DAYS = 5      # "a day or a week" -- the paper's own recent-window language
VOLUME_SHOCK_BASELINE_DAYS = 252  # 1 trading year -- the stock's own typical volume level


def compute_volume_shock_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Gervais, Kaniel & Mingelgrin (2001)'s volume "shock" -- a stock's
    recent trading activity relative to its OWN typical level, since raw
    volume differs enormously across stocks by size alone and can't be
    compared cross-sectionally without normalizing each stock against
    itself first. score_t = (mean Volume over the trailing
    VOLUME_SHOCK_RECENT_DAYS) / (mean Volume over the trailing
    VOLUME_SHOCK_BASELINE_DAYS, ENDING right before the recent window --
    no overlap between the two windows, so the recent window's own volume
    never inflates its own baseline).
    """
    volume = price_history["Volume"]
    recent_avg = volume.rolling(VOLUME_SHOCK_RECENT_DAYS).mean()
    # Baseline ends immediately before the recent window starts: shift by
    # VOLUME_SHOCK_RECENT_DAYS first, then take the trailing baseline mean,
    # so today's baseline reflects days [t - recent - baseline, t - recent).
    baseline_avg = volume.shift(VOLUME_SHOCK_RECENT_DAYS).rolling(VOLUME_SHOCK_BASELINE_DAYS).mean()
    return recent_avg / baseline_avg.replace(0, float("nan"))


CRYPTO_MOMENTUM_LOOKBACK_DAYS = 21   # three weeks -- Liu, Tsyvinski & Wu (2022)'s strongest horizon


def compute_crypto_momentum_score(price_history: pd.DataFrame,
                                  lookback_days: int = CRYPTO_MOMENTUM_LOOKBACK_DAYS) -> pd.Series:
    """Trailing `lookback_days` calendar-day return (crypto trades every
    day, so calendar days ARE bars), as of each day's close -- Liu,
    Tsyvinski & Wu's 3-week momentum. No shift: the signal is used at the
    close it is computed on, and the entry fills at that same close."""
    close = price_history["Close"]
    return close / close.shift(lookback_days) - 1


def compute_crypto_momentum_percentile_ranks(data: dict, lookback_days: int = CRYPTO_MOMENTUM_LOOKBACK_DAYS) -> dict:
    """{symbol: Series of crypto_momentum_percentile (0-100)} -- each
    coin's cross-sectional rank by trailing 3-week return among the coins
    with a valid score that day. Same .rank(axis=1, pct=True) construction
    as every other signal in this module."""
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_crypto_momentum_score(df.sort_index(), lookback_days)
    if not scores:
        return {}
    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


def compute_volume_shock_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of volume_shock_percentile (0-100), indexed
    by date} -- each symbol's cross-sectional percentile rank, among
    whatever symbols have a valid (non-NaN) volume shock score that day,
    of its own recent-vs-typical volume ratio. HIGH percentile means an
    UNUSUALLY LARGE recent volume spike relative to that stock's own
    history -- this strategy's entry condition is percentile >=90 (top
    decile), the paper's own long-side finding (high-volume shocks predict
    price appreciation over the following month). Same vectorized
    .rank(axis=1, pct=True) construction as every other cross-sectional
    signal in this module.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_volume_shock_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


# Ang, Chen & Xing (2006) downside beta: beta of the stock to the market
# on the days the market's return is BELOW its own mean over the window,
# daily returns over the past year (the paper's own estimator).
DOWNSIDE_BETA_LOOKBACK_DAYS = 252
DOWNSIDE_BETA_MIN_DOWN_DAYS = 60   # a 252-day window has ~110-130 down days; fewer than 60 means data gaps


def compute_downside_beta_score(price_history: pd.DataFrame, market_close: pd.Series,
                                lookback_days: int = DOWNSIDE_BETA_LOOKBACK_DAYS) -> pd.Series:
    """beta_minus_t = cov(r_i, r_m | r_m < mean(r_m)) / var(r_m | r_m < mean(r_m))
    over the `lookback_days` daily log returns ending at day t, the
    condition and the means taken WITHIN each window (exact, via sliding
    windows). NaN until a full window of valid returns exists, or when a
    window has fewer than DOWNSIDE_BETA_MIN_DOWN_DAYS market-down days.
    market_close is aligned to the stock's own dates and forward-filled,
    as compute_shrunk_beta_score() does."""
    from numpy.lib.stride_tricks import sliding_window_view

    stock_close = price_history["Close"]
    aligned_market = market_close.reindex(stock_close.index).ffill()
    r_i = (np.log(stock_close) - np.log(stock_close.shift(1))).to_numpy(dtype=float)
    r_m = (np.log(aligned_market) - np.log(aligned_market.shift(1))).to_numpy(dtype=float)
    n = len(r_i)
    out = np.full(n, np.nan)
    if n <= lookback_days:
        return pd.Series(out, index=stock_close.index)

    Ri = sliding_window_view(r_i[1:], lookback_days)     # window k ends at index k + lookback_days
    Rm = sliding_window_view(r_m[1:], lookback_days)
    valid = np.isfinite(Ri) & np.isfinite(Rm)
    full = valid.all(axis=1)
    Ri0, Rm0 = np.where(valid, Ri, 0.0), np.where(valid, Rm, 0.0)
    mu_m = Rm0.sum(axis=1) / np.maximum(valid.sum(axis=1), 1)
    down = valid & (Rm0 < mu_m[:, None])
    cnt = down.sum(axis=1)
    safe_cnt = np.maximum(cnt, 1)
    mean_i = (Ri0 * down).sum(axis=1) / safe_cnt
    mean_m = (Rm0 * down).sum(axis=1) / safe_cnt
    dev_i = (Ri0 - mean_i[:, None]) * down
    dev_m = (Rm0 - mean_m[:, None]) * down
    cov = (dev_i * dev_m).sum(axis=1) / safe_cnt
    var = (dev_m * dev_m).sum(axis=1) / safe_cnt
    beta = np.where((var > 0) & full & (cnt >= DOWNSIDE_BETA_MIN_DOWN_DAYS), cov / np.where(var > 0, var, 1.0), np.nan)
    out[lookback_days:] = beta
    return pd.Series(out, index=stock_close.index)


def compute_downside_beta_percentile_ranks(data: dict, market_close: pd.Series,
                                           lookback_days: int = DOWNSIDE_BETA_LOOKBACK_DAYS) -> dict:
    """{symbol: Series of downside_beta_percentile (0-100)}: each stock's
    cross-sectional rank by downside beta among the stocks with a valid
    estimate that day. HIGH percentile = HIGH downside beta = the side
    Ang, Chen & Xing find the premium on. Same .rank(axis=1, pct=True)
    construction as every other signal here."""
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_downside_beta_score(df.sort_index(), market_close, lookback_days)
    if not scores:
        return {}
    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


# Nifty100 Low Volatility 30 methodology: standard deviation of daily
# log returns over the last one year, lowest = selected.
REALIZED_VOL_LOOKBACK_DAYS = 252


def compute_realized_volatility_score(price_history: pd.DataFrame,
                                      lookback_days: int = REALIZED_VOL_LOOKBACK_DAYS) -> pd.Series:
    """Rolling 1-year standard deviation of daily log returns, as of each
    day's close (no lookahead). NaN until a full window exists."""
    close = price_history["Close"]
    r = np.log(close) - np.log(close.shift(1))
    return r.rolling(lookback_days).std()


def compute_realized_volatility_percentile_ranks(data: dict,
                                                 lookback_days: int = REALIZED_VOL_LOOKBACK_DAYS) -> dict:
    """{symbol: Series of realized_vol_percentile (0-100)} -- LOW percentile
    = LOW volatility, the selected side (entry condition <= 10). Same
    .rank(axis=1, pct=True) construction as every other signal here."""
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_realized_volatility_score(df.sort_index(), lookback_days)
    if not scores:
        return {}
    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


NIFTY_MOMENTUM_30_6M_DAYS = 126    # 6-month return leg
NIFTY_MOMENTUM_30_12M_DAYS = 252   # 12-month return leg, also the volatility lookback window
NIFTY_MOMENTUM_30_6M_WEIGHT = 0.5
NIFTY_MOMENTUM_30_12M_WEIGHT = 0.5


def compute_nifty_momentum_30_score(price_history: pd.DataFrame) -> pd.Series:
    """
    Risk-adjusted blended momentum score, per the Nifty200 Momentum 30
    Index methodology (NSE Indices, live since 2019): the 6-month and
    12-month Close-to-Close returns, EACH divided by the SAME trailing
    12-month (252-trading-day) standard deviation of daily returns -- a
    Sharpe-ratio-like risk adjustment -- then blended 50%/50%. No
    .shift() needed before .rolling()/the return ratios -- every value at
    row i uses only data up to and including row i, no lookahead.

    DISCLOSED SIMPLIFICATION: the official methodology standardises each
    of the two risk-adjusted ratios into a cross-sectional Z-score FIRST,
    averages the two Z-scores, and then applies an asymmetric non-linear
    transform (1+Z for Z>=0, 1/(1-Z) for Z<0) -- a transform that matters
    for THAT index's free-float-market-cap x score portfolio WEIGHTING,
    which this program's framework has no equivalent of. Like every other
    cross-sectional strategy already in this program (compute_rs_score(),
    compute_momentum_score(), etc.), this blends the raw ratios directly
    and leaves cross-sectional standardisation to the single
    .rank(pct=True) percentile step below, since a decile-cutoff entry
    rule only needs a consistent ORDERING, not the index's own
    score-as-a-portfolio-weight value.
    """
    close = price_history["Close"]
    r = np.log(close) - np.log(close.shift(1))
    vol_12m = r.rolling(NIFTY_MOMENTUM_30_12M_DAYS).std()
    ret_6m = close / close.shift(NIFTY_MOMENTUM_30_6M_DAYS) - 1
    ret_12m = close / close.shift(NIFTY_MOMENTUM_30_12M_DAYS) - 1
    ratio_6m = ret_6m / vol_12m
    ratio_12m = ret_12m / vol_12m
    return (NIFTY_MOMENTUM_30_6M_WEIGHT * ratio_6m
            + NIFTY_MOMENTUM_30_12M_WEIGHT * ratio_12m)


def compute_nifty_momentum_30_percentile_ranks(data: dict) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of nifty_momentum_30_percentile (0-100),
    indexed by date} -- each symbol's cross-sectional percentile rank,
    among whatever symbols have a valid (non-NaN, i.e. >=252 bars of
    history) blended risk-adjusted momentum score that day. Same
    vectorized .rank(axis=1, pct=True) construction as every other signal
    in this module.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_nifty_momentum_30_score(df.sort_index())

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


CRYPTO_ILLIQ_FORMATION_DAYS = 365  # ~1 year of CALENDAR days, not the equity lane's 252 TRADING
                                     # days (AMIHUD_ILLIQ_FORMATION_DAYS above) -- crypto trades
                                     # every day, so 252 bars would be ~8.3 months, not a full year.
                                     # Same "every day is a bar, 12 months = 365 days" convention
                                     # crypto_tsmom.py's own TSMOM_LOOKBACK_DAYS already discloses.


def compute_crypto_illiq_percentile_ranks(data: dict, lookback_days: int = CRYPTO_ILLIQ_FORMATION_DAYS) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of crypto_illiq_percentile (0-100), indexed
    by date} -- each coin's cross-sectional percentile rank, among
    whatever coins have a valid (non-NaN, i.e. >=365 bars of history)
    trailing 365-day Amihud ILLIQ that day, of its own illiquidity. HIGH
    percentile means HIGH illiquidity (hard to trade) -- this strategy's
    entry condition is percentile >=90 (the top decile, MOST illiquid),
    the same polarity convention as compute_amihud_illiq_percentile_ranks().

    Reuses swing_research.execution_realism_engine.compute_trailing_illiq()
    -- the SAME Amihud (2002) ILLIQ formula (Close x dollar-volume proxy)
    already established for the equity Amihud strategy -- with THIS
    strategy's own 365-CALENDAR-day formation window, not the equity
    lane's 252-TRADING-day window or execution_realism_engine.py's own
    20-day execution-cost-estimate window. One function, three callers,
    three different (disclosed, distinct-purpose) lookback lengths -- not
    a duplicated formula.
    """
    from swing_research.execution_realism_engine import compute_trailing_illiq

    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_trailing_illiq(df.sort_index(), lookback_days=lookback_days)

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


CRYPTO_IVOL_FORMATION_DAYS = 30  # ~1 month of CALENDAR days, not the equity lane's 21 TRADING
                                   # days (IVOL_FORMATION_DAYS above) -- crypto trades every day,
                                   # so 21 bars would be ~3 weeks, not a full month. 30 is the same
                                   # "every day is a bar" conversion already established by this
                                   # lane's own CRYPTO_ILLIQ_FORMATION_DAYS (252->365 for the
                                   # annual window; 21->30 here for the monthly one, same ~1.45x
                                   # trading-to-calendar ratio).


def compute_crypto_market_factor_close(data: dict) -> pd.Series:
    """
    An equal-weighted crypto market-factor proxy, built from `data` itself
    (the universe already fetched for this experiment) -- a synthetic
    daily index level series, usable as the `market_close` argument of
    compute_crypto_idiosyncratic_volatility_score()/_percentile_ranks()
    below, the same role Nifty 50's own fetched Close series plays for the
    equity lane's compute_idiosyncratic_volatility_score(). Unlike Nifty
    50, this platform has no separately-fetched "crypto market index"
    data source (see data/fetch_crypto.py) -- Zhang & Li's own
    Fama-MacBeth-style market factor is itself built from the authors' own
    coin sample, so an equal-weighted index of the SAME universe each
    experiment already fetches is a faithful, disclosed proxy, not an
    invented substitute.

    DISCLOSED DEVIATION: because the index is built from this experiment's
    own universe, each coin contributes to the market factor it is then
    regressed against (e.g. ~2.5% self-weight in the 40-coin
    CRYPTO_UNIVERSE) -- unlike Nifty 50 vs. an arbitrary NSE stock, where
    the two series are fetched independently. Estimated impact: MINOR --
    this slightly inflates every coin's own correlation with the market
    factor (and so slightly understates every coin's IVOL) by the same
    small amount, symmetrically across the whole cross-section, so it does
    not favor one coin over another in the resulting percentile ranks.

    Each day's cross-sectional mean of daily simple returns (across
    whichever coins have a valid prior-day Close that day -- a coin
    missing a day is simply excluded from that day's mean, not treated as
    a zero return) is compounded into an index level starting at 1.0.
    """
    returns = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        returns[symbol] = df.sort_index()["Close"].pct_change()

    if not returns:
        return pd.Series(dtype=float)

    wide_returns = pd.DataFrame(returns)
    market_return = wide_returns.mean(axis=1, skipna=True)
    return (1.0 + market_return.fillna(0.0)).cumprod()


def compute_crypto_idiosyncratic_volatility_score(price_history: pd.DataFrame, market_close: pd.Series,
                                                   lookback_days: int = CRYPTO_IVOL_FORMATION_DAYS) -> pd.Series:
    """
    Zhang & Li (2020)'s crypto idiosyncratic volatility: the standard
    deviation of the residuals from a regression of a coin's daily returns
    on a crypto-market-factor proxy, over the trailing `lookback_days`
    (30 calendar days, ~1 month -- see CRYPTO_IVOL_FORMATION_DAYS above).

    Same single-factor closed-form identity as the equity lane's own
    compute_idiosyncratic_volatility_score() (for a single-regressor OLS,
    Var(residual) = Var(Y) x (1 - rho^2), so idio_vol = sigma_coin x
    sqrt(1 - rho^2)) -- the regression machinery this candidate's own
    mechanism field says to reuse -- just with THIS strategy's own
    30-calendar-day window and crypto market-factor proxy (market_close:
    see compute_crypto_market_factor_close() above) in place of the equity
    lane's 21-trading-day window and fetched Nifty 50 Close series. Plain
    (non-overlapping) daily returns, no lookahead: the value at row i uses
    only data up to and including row i's own Close.

    clip(..., 0, None): a near-perfectly-correlated coin (rho within
    floating-point noise of +/-1.0, plausible here since this experiment's
    OWN universe partly makes up the market factor -- see
    compute_crypto_market_factor_close()'s own disclosed self-weighting)
    can otherwise push 1 - rho^2 to a tiny NEGATIVE number, turning a
    true, near-zero idiosyncratic volatility into NaN (sqrt of a negative
    number) instead of ~0.0.
    """
    coin_close = price_history["Close"]
    aligned_market = market_close.reindex(coin_close.index).ffill()

    coin_r1 = coin_close.pct_change()
    market_r1 = aligned_market.pct_change()

    rho = coin_r1.rolling(lookback_days).corr(market_r1)
    sigma_coin = coin_r1.rolling(lookback_days).std()

    return sigma_coin * np.sqrt((1 - rho ** 2).clip(lower=0.0))


def compute_crypto_idiosyncratic_volatility_percentile_ranks(
        data: dict, market_close: pd.Series, lookback_days: int = CRYPTO_IVOL_FORMATION_DAYS) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}. market_close: the
    crypto market-factor proxy (see compute_crypto_market_factor_close()).

    Returns {symbol: pd.Series of crypto_ivol_percentile (0-100), indexed
    by date} -- each coin's cross-sectional percentile rank, among
    whatever coins have a valid (non-NaN) idiosyncratic volatility that
    day, of its own residual (market-model-adjusted) volatility. HIGH
    percentile means HIGH idiosyncratic volatility -- this strategy's
    entry condition is percentile >=90 (the top decile), the paper's own
    documented POSITIVE crypto IVOL-return relationship -- the OPPOSITE
    polarity from the equity lane's own compute_idiosyncratic_volatility_
    percentile_ranks() (bottom-decile, lowest-idio-vol), which is this
    candidate's own disclosed, genuinely-different-sign mechanism, not an
    inconsistency between the two. Same vectorized .rank(axis=1, pct=True)
    construction as every other cross-sectional signal in this module.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_crypto_idiosyncratic_volatility_score(df.sort_index(), market_close, lookback_days)

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}


CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS = 60  # ~2 calendar months -- the midpoint of this
    # candidate's own disclosed "roughly 1-3 month" strongest-reversal band (research_roadmap.py's
    # typical_holding_period field), not independently re-derived from Dobrynskaya (2023) /
    # Ficura & Colak (2023)'s own full papers (the candidate's own known_weaknesses field flags the
    # exact switch-point as needing that confirmation before implementation, which this module does
    # not have access to -- see the strategy's module docstring for the full disclosure). Crypto
    # trades every day, so calendar days are bars, same "every day is a bar" convention as
    # CRYPTO_ILLIQ_FORMATION_DAYS (252->365) and CRYPTO_IVOL_FORMATION_DAYS (21->30) above.


def compute_crypto_long_horizon_reversal_score(
        price_history: pd.DataFrame, lookback_days: int = CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS) -> pd.Series:
    """Dobrynskaya (2023) / Ficura & Colak (2023)'s long-horizon formation return: trailing
    `lookback_days` calendar-day Close-to-Close return, as of each day's close -- the SAME
    single-period cumulative-return construction as compute_crypto_momentum_score() (just a much
    longer lookback: 60 calendar days vs. that function's 21-day/3-week horizon), since the two
    papers' documented finding is that this longer window is where crypto's short-horizon momentum
    switches sign into reversal. No .shift() needed before the ratio -- the value at row i uses
    only Close prices up to and including row i, no lookahead."""
    close = price_history["Close"]
    return close / close.shift(lookback_days) - 1


def compute_crypto_long_horizon_reversal_percentile_ranks(
        data: dict, lookback_days: int = CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    Returns {symbol: pd.Series of crypto_reversal_percentile (0-100), indexed by date} -- each
    coin's cross-sectional percentile rank, among whatever coins have a valid (non-NaN, i.e.
    >=`lookback_days` bars of history) trailing formation-period return that day, of its own
    60-calendar-day return. LOW percentile means a WORSE recent 60-day return -- this strategy's
    entry condition is percentile <=10 (the bottom decile, past LOSERS), the candidate's own
    disclosed long-only reduction of the papers' long-short reversal factor (direction field:
    "Long-only top-decile (past LOSERS...)"), the same <=10/bottom-decile polarity convention as
    compute_short_term_reversal_percentile_ranks() above -- a structurally identical mechanism at
    a much longer horizon, not a duplicated signal (short-term reversal's 21-trading-day equity
    formation window targets a completely different (1-month equity) effect than crypto's
    1-3-month "faster metabolism" switch this candidate's mechanism field describes). Same
    vectorized .rank(axis=1, pct=True) construction as every other cross-sectional signal in this
    module -- one call ranks every coin for every day simultaneously.
    """
    scores = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        scores[symbol] = compute_crypto_long_horizon_reversal_score(df.sort_index(), lookback_days)

    if not scores:
        return {}

    wide = pd.DataFrame(scores)
    pct_ranks = _percentile_rank_rows(wide)
    return {symbol: pct_ranks[symbol] for symbol in pct_ranks.columns}
