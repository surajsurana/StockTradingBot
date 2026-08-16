"""
Wraps swing_research.backtesting_engine.simulate_portfolio()'s OUTPUT
(never its internals -- backtesting_engine.py is never imported for
modification, only its Trade dataclass shape is relied on) to add three
optional, individually-toggleable execution-realism adjustments, per
swing_research/execution_realism_framework_proposal.md (2026-08-15,
approved) and swing_research/execution_realism_study.md (2026-08-05,
original recommendation):

    1. Volume-relative position-sizing cap
    2. Illiquidity-linked slippage/impact cost (derived from the same
       Amihud ILLIQ construction used for the Amihud Illiquidity Premium
       roadmap candidate)
    3. Next-day-open fill timing

Every option defaults to OFF -- apply_execution_realism() called with no
options returns trades economically identical to the input (same prices,
same quantities, same PnL).

METHODOLOGY CAVEAT (disclosed, not hidden): all three adjustments are
applied as a POST-PROCESS on the trade list simulate_portfolio() already
produced under its OWN (uncapped, zero-cost, same-day-close) sequential
equity path -- NOT a full re-simulation where the cap/cost/timing feed
back into which later trades even get taken or how they're sized. This is
the SAME style of approximation execution_realism_study.md itself already
used (substituting alternate fill prices into an existing trade list
rather than re-deriving a fully sequential alternate simulation). It is
EXACT for quantity rescaling (PnL is linear in quantity here -- entry/exit
prices don't themselves depend on quantity in this engine) but does NOT
capture how a capped/costed early trade would have changed the REALIZED
EQUITY available to size later trades in a true sequential re-simulation.
Disclosed limitation: a fully sequential re-implementation would require
a new day-loop (real duplication of backtesting_engine.py's day-loop) --
the explicit tradeoff accepted when this was scoped as a new sibling
module rather than an edit to backtesting_engine.py itself.
"""

from dataclasses import replace
from datetime import date
from typing import Optional

import pandas as pd

from swing_research.backtesting_engine import Trade, simulate_portfolio

DEFAULT_ADV_LOOKBACK_DAYS = 20
DEFAULT_ILLIQ_COST_CAP_PCT = 0.05


def compute_trailing_adv(price_history: pd.DataFrame, lookback_days: int = DEFAULT_ADV_LOOKBACK_DAYS) -> pd.Series:
    """Trailing lookback_days average daily Volume, SHIFTED by 1 so a given
    day's value reflects only PRIOR days -- the ADV known "as of the start
    of today," matching every other no-lookahead convention in this program."""
    return price_history["Volume"].shift(1).rolling(lookback_days).mean()


def compute_trailing_illiq(price_history: pd.DataFrame, lookback_days: int = DEFAULT_ADV_LOOKBACK_DAYS) -> pd.Series:
    """Amihud (2002) ILLIQ: trailing mean of |daily return| / daily rupee
    volume (Close x Volume, the same disclosed rupee-volume proxy
    published_research_analyst.py's BETTING_AGAINST_BETA-adjacent
    candidates already establish as standard practice for this platform
    -- no intraday VWAP is available). Shifted by 1, no lookahead."""
    close = price_history["Close"]
    ret = close.pct_change()
    dollar_volume = close * price_history["Volume"]
    daily_illiq = (ret.abs() / dollar_volume).replace([float("inf"), -float("inf")], float("nan"))
    return daily_illiq.shift(1).rolling(lookback_days).mean()


def calibrate_illiq_cost_k(data: dict, target_median_one_way_cost_pct: float = 0.001,
                            representative_trade_dollar_value: float = 100_000,
                            adv_lookback_days: int = DEFAULT_ADV_LOOKBACK_DAYS) -> float:
    """
    Derives the cost-model constant k such that a stock at the MEDIAN
    trailing ILLIQ across the given universe, trading a representative
    position size (representative_trade_dollar_value -- a rough anchor for
    this program's typical position, given ~1% risk sizing against a
    virtual ~10L capital base), would incur roughly
    target_median_one_way_cost_pct one-way cost.

    This is a DISCLOSED, REASONED ANCHOR, not an empirically-fit
    market-impact coefficient -- no real historical order-book or bid-ask-
    spread data exists anywhere in this platform to fit one against (see
    execution_realism_framework_proposal.md's own caveat). The default
    target (10bps one-way for a MEDIAN-liquidity stock at a representative
    trade size) is a conservative, round-number judgment call, not a
    citation -- less liquid stocks (higher ILLIQ) scale up from this
    anchor proportionally, more liquid stocks scale down.
    """
    medians = []
    for df in data.values():
        if df is None or df.empty:
            continue
        illiq = compute_trailing_illiq(df.sort_index(), adv_lookback_days).dropna()
        if not illiq.empty:
            medians.append(illiq.median())
    if not medians:
        return 0.0
    medians.sort()
    median_illiq = medians[len(medians) // 2]
    if median_illiq <= 0:
        return 0.0
    return target_median_one_way_cost_pct / (median_illiq * representative_trade_dollar_value)


def _next_trading_day_open(df: pd.DataFrame, after_date: date) -> Optional[float]:
    later = df[df.index.date > after_date]
    if later.empty:
        return None
    return float(later.iloc[0]["Open"])


def apply_execution_realism(trades: list, data: dict,
                             max_participation_pct_of_adv: Optional[float] = None,
                             illiq_cost_k: Optional[float] = None,
                             illiq_cost_cap_pct: float = DEFAULT_ILLIQ_COST_CAP_PCT,
                             fill_timing: str = "same_day_close",
                             adv_lookback_days: int = DEFAULT_ADV_LOOKBACK_DAYS) -> dict:
    """
    trades: list[Trade] from backtesting_engine.simulate_portfolio() (or
    simulate_portfolio_single_unit()'s equivalent shape).
    data: the SAME {symbol: DataFrame} the original simulation used --
    needed to look up ADV/ILLIQ/next-day-open per symbol.

    Returns {"trades": list[Trade] (new objects, originals untouched),
    "skipped_no_next_day": int, "capped_trade_count": int,
    "illiq_cost_k_used": float or None} -- the counts let a caller sanity-
    check how much the adjustments actually did (e.g. confirm the cap is
    near-a-no-op for a liquid-name strategy, as expected per the
    validation plan in execution_realism_framework_proposal.md).
    """
    adv_cache, illiq_cache = {}, {}

    def adv_at(symbol: str, d: date) -> Optional[float]:
        if symbol not in adv_cache:
            df = data.get(symbol)
            adv_cache[symbol] = compute_trailing_adv(df.sort_index(), adv_lookback_days) if df is not None and not df.empty else None
        series = adv_cache[symbol]
        if series is None:
            return None
        matches = series[series.index.date == d]
        if matches.empty or pd.isna(matches.iloc[0]):
            return None
        return float(matches.iloc[0])

    def illiq_at(symbol: str, d: date) -> Optional[float]:
        if symbol not in illiq_cache:
            df = data.get(symbol)
            illiq_cache[symbol] = compute_trailing_illiq(df.sort_index(), adv_lookback_days) if df is not None and not df.empty else None
        series = illiq_cache[symbol]
        if series is None:
            return None
        matches = series[series.index.date == d]
        if matches.empty or pd.isna(matches.iloc[0]):
            return None
        return float(matches.iloc[0])

    new_trades = []
    skipped_no_next_day = 0
    capped_trade_count = 0

    for t in trades:
        symbol, entry_price, exit_price, quantity = t.symbol, t.entry_price, t.exit_price, t.quantity
        entry_date, exit_date = t.entry_date, t.exit_date

        # 1. Fill timing -- substitute next-day-open prices, same
        # methodology execution_realism_study.md already validated.
        if fill_timing == "next_day_open":
            df = data.get(symbol)
            if df is None or df.empty:
                skipped_no_next_day += 1
                new_trades.append(t)
                continue
            new_entry = _next_trading_day_open(df, entry_date)
            new_exit = _next_trading_day_open(df, exit_date)
            if new_entry is None or new_exit is None:
                skipped_no_next_day += 1
                new_trades.append(t)
                continue
            entry_price, exit_price = new_entry, new_exit

        # 2. Volume-relative sizing cap -- rescale quantity (and PnL
        # proportionally, exact since PnL is linear in quantity here).
        if max_participation_pct_of_adv is not None:
            adv = adv_at(symbol, entry_date)
            if adv is not None and adv > 0:
                cap_qty = int(max_participation_pct_of_adv * adv)
                if cap_qty <= 0:
                    # 5% of this stock's own trailing ADV rounds to less
                    # than a single share -- no economically meaningful
                    # position size exists at this participation limit, so
                    # the trade is dropped (below), not merely resized.
                    # Counted here too -- an earlier version only counted
                    # the 0 < cap_qty < quantity branch, silently
                    # under-reporting how many trades the cap actually
                    # affected (caught during SW-003/SW-008 validation,
                    # where 2 SW-008 trades vanished while
                    # capped_trade_count still read 0).
                    quantity = 0
                    capped_trade_count += 1
                elif cap_qty < quantity:
                    quantity = cap_qty
                    capped_trade_count += 1

        # 3. Illiquidity-linked cost -- adverse price adjustment on both
        # legs (pay more entering, receive less exiting), derived from
        # this symbol's own trailing ILLIQ and this TRADE's own dollar value.
        if illiq_cost_k is not None and quantity > 0:
            illiq = illiq_at(symbol, entry_date)
            if illiq is not None and illiq > 0:
                trade_dollar_value = entry_price * quantity
                cost_pct = min(illiq_cost_cap_pct, illiq_cost_k * illiq * trade_dollar_value)
                is_long = (t.direction == "BUY")
                if is_long:
                    entry_price = entry_price * (1 + cost_pct)
                    exit_price = exit_price * (1 - cost_pct)
                else:
                    entry_price = entry_price * (1 - cost_pct)
                    exit_price = exit_price * (1 + cost_pct)

        if quantity <= 0:
            continue  # position sized to zero by the participation cap -- never taken

        pnl = ((exit_price - entry_price) if t.direction == "BUY" else (entry_price - exit_price)) * quantity

        new_trades.append(replace(
            t, entry_price=entry_price, exit_price=exit_price, quantity=quantity, pnl=pnl,
        ))

    return {
        "trades": new_trades, "skipped_no_next_day": skipped_no_next_day,
        "capped_trade_count": capped_trade_count, "illiq_cost_k_used": illiq_cost_k,
    }


def build_approximate_daily_equity(trades: list, starting_capital: float, trading_calendar: list) -> dict:
    """
    A PROXY realized-equity curve for a POST-PROCESSED trade list --
    NOT backtesting_engine.simulate_portfolio()'s own daily_equity (which
    this module never recomputes, per its own disclosed sequential-
    equity-path caveat above).

    MUST be DENSE (one entry per day in trading_calendar, forward-filled
    between trade exits), not sparse (one entry per trade-exit date only)
    -- compute_metrics()'s Sharpe/Sortino annualize assuming daily-spaced
    observations (multiplying by sqrt(252)); a sparse, trade-exit-only
    series has consecutive entries spaced MANY days apart on average, so
    each "step"'s return is really several days of accumulated PnL
    compressed into one step, and annualizing that as if it were a single
    day's return wildly inflates Sharpe/Sortino (confirmed directly: an
    early version of this function that only recorded exit-date entries
    produced Sharpe 1.01 -> 3.37 for SW-003's volume-cap variant even
    though capped_trade_count was 0, i.e. literally zero trades differed
    from baseline -- proof the shift was a measurement artifact of this
    function, not a real effect, caught and fixed before this module's
    first real use). trading_calendar: from simulate_portfolio()'s own
    result["trading_calendar"] -- the same full backtest calendar the
    ORIGINAL trades were generated against, so density matches the baseline.

    Still does NOT reflect how a truly sequential re-simulation (where an
    early trade's changed size/cost changes the equity later trades would
    have been sized against) would look -- see this module's own
    docstring caveat. Dense-vs-sparse was a correctness bug; sequential-
    equity-path is a disclosed, accepted approximation.
    """
    equity = starting_capital
    pnl_by_exit_date = {}
    for t in trades:
        pnl_by_exit_date[t.exit_date] = pnl_by_exit_date.get(t.exit_date, 0.0) + t.pnl

    daily_equity = {}
    for d in sorted(trading_calendar):
        equity += pnl_by_exit_date.get(d, 0.0)
        daily_equity[d] = equity
    return daily_equity


def run_walk_forward_execution_realistic(strategy, data: dict, starting_capital: float, sector_map: dict,
                                          start_date: date, end_date: date, n_walk_forward_windows: int = 3,
                                          extra_columns_by_symbol: Optional[dict] = None,
                                          max_participation_pct_of_adv: Optional[float] = 0.05,
                                          illiq_cost_k: Optional[float] = None,
                                          fill_timing: str = "next_day_open",
                                          min_trades_total: int = 15, min_out_of_sample_trades: int = 3,
                                          min_consistent_window_fraction: float = 0.5) -> dict:
    """
    Execution-realism-aware counterpart to
    research_director.run_walk_forward_generic() -- SAME walk-forward
    windowing (research_lab.backtesting_engineer.walk_forward_split(),
    imported unmodified, identical to the standard pipeline) and SAME
    research_lab.statistical_auditor.audit() (imported unmodified,
    frozen-adjacent), but computes each window's audited metrics from
    apply_execution_realism()-adjusted trades instead of raw
    simulate_portfolio() trades -- so the ACCEPTANCE VERDICT itself
    reflects the volume cap + illiquidity cost + next-day-open fill
    assumptions, per execution_realism_framework_proposal.md (approved
    2026-08-15) and the SW-003/SW-008 validation that preceded any use of
    this function on a real strategy.

    Never modifies research_director.py's own run_walk_forward_generic()
    -- a fully separate function, plugged in via
    research_director.run_generic_swing_experiment()'s optional
    walk_forward_fn parameter (functools.partial-bind this function's own
    max_participation_pct_of_adv/illiq_cost_k/fill_timing kwargs before
    passing it in, since the caller only ever invokes it with the
    standard positional args).

    illiq_cost_k: if None, calibrated ONCE via calibrate_illiq_cost_k(data)
    using its own default target/anchor (10bps one-way at a representative
    Rs.100,000 trade for a median-ILLIQ universe stock) -- this happens
    ONCE per call, from the data, never re-tuned per-window or based on
    any strategy's own results (that would defeat the purpose of a
    disclosed, pre-declared cost methodology).

    Returns the same shape as run_walk_forward_generic()'s result, PLUS
    "diagnostic_walk_forward_metrics_zero_cost" (each window's RAW,
    unadjusted metrics -- reported for transparency only, never used by
    the audit above) and "illiq_cost_k_used".
    """
    from research_lab import backtesting_engineer, statistical_auditor
    from swing_research.metrics import compute_metrics

    if illiq_cost_k is None:
        illiq_cost_k = calibrate_illiq_cost_k(data)

    windows = backtesting_engineer.walk_forward_split(start_date, end_date, n_walk_forward_windows)
    walk_forward_metrics = []
    diagnostic_walk_forward_metrics_zero_cost = []
    all_trades_by_window = []

    for w_start, w_end in windows:
        windowed_data = {
            sym: df[(df.index.date >= w_start) & (df.index.date <= w_end)]
            for sym, df in data.items()
        }
        windowed_extra = None
        if extra_columns_by_symbol:
            windowed_extra = {
                sym: series[(series.index.date >= w_start) & (series.index.date <= w_end)]
                for sym, series in extra_columns_by_symbol.items()
            }
        result = simulate_portfolio(
            windowed_data, strategy, starting_capital, sector_map=sector_map,
            extra_columns_by_symbol=windowed_extra,
        )
        raw_trades = result["trades"]
        calendar = result["trading_calendar"]

        diagnostic_walk_forward_metrics_zero_cost.append(
            compute_metrics(raw_trades, starting_capital, calendar, daily_equity=result["daily_equity"])
        )

        adjusted = apply_execution_realism(
            raw_trades, windowed_data, max_participation_pct_of_adv=max_participation_pct_of_adv,
            illiq_cost_k=illiq_cost_k, fill_timing=fill_timing,
        )
        adjusted_equity = build_approximate_daily_equity(adjusted["trades"], starting_capital, calendar)
        metrics = compute_metrics(adjusted["trades"], starting_capital, calendar, daily_equity=adjusted_equity)
        walk_forward_metrics.append(metrics)
        all_trades_by_window.append(adjusted["trades"])

    out_of_sample_metrics = walk_forward_metrics[-1] if walk_forward_metrics else {}
    consistency_metrics = walk_forward_metrics[:-1]
    out_of_sample_trades = all_trades_by_window[-1] if all_trades_by_window else []
    all_trades = [t for trades in all_trades_by_window for t in trades]

    verdict = statistical_auditor.audit(
        consistency_metrics, out_of_sample_metrics,
        min_trades_total=min_trades_total, min_out_of_sample_trades=min_out_of_sample_trades,
        min_consistent_window_fraction=min_consistent_window_fraction,
    )

    return {
        "verdict": verdict, "walk_forward_metrics": consistency_metrics,
        "out_of_sample_metrics": out_of_sample_metrics, "out_of_sample_trades": out_of_sample_trades,
        "all_trades": all_trades, "windows": windows,
        "diagnostic_walk_forward_metrics_zero_cost": diagnostic_walk_forward_metrics_zero_cost,
        "illiq_cost_k_used": illiq_cost_k,
    }
