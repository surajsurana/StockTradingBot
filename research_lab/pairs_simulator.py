"""
Pairs simulation engine -- the third engine in research_lab, alongside
backtesting_engineer.run_backtest() (one symbol at a time, no
cross-symbol awareness) and market_simulator.simulate_universe_cross_
sectional() (whole-universe MarketState shared per bar, but still one
Signal per symbol per call). Neither can express "one decision opens two
legs at once", which is what a pairs trade IS: long one stock, short its
correlated peer in equal notional, exit both together when the SPREAD
between them reverts (or widens further, or the day ends). Built
2026-09-07 for EXP-009 (Intra-Sector Pair Spread Reversion), the first
market-neutral hypothesis this lab has tested.

Reuses backtesting_engineer.py's Trade and _trade_pnl (imported, not
duplicated -- same pattern as market_simulator.py); the per-leg P&L
math is identical, a pair's P&L is just the two legs summed.

What is deliberately DIFFERENT from the single-symbol engines, and why:
- Sizing is equal-notional (capital_per_pair split 50/50 across the two
  legs, each rounded down to whole shares), NOT risk_per_trade_pct
  against a stop distance -- a pairs trade has no per-leg price stop to
  size off; its risk is defined on the spread.
- Exit is a z-score check on the spread (_check_spread_exit), NOT
  _check_exit's Low/High-vs-stop/target on a single price. Stop is
  direction-aware: it only fires if the spread moves FURTHER in the
  entry's own direction (the hypothesis's literal wording, "widens
  further"); a swing all the way through zero to the opposite side
  counts as the spread having reverted (target), not as a stop.
- One completed pair round-trip becomes exactly ONE Trade (symbol =
  "A/B", pnl = both legs summed, entry_price/exit_price = the ratio at
  entry/exit, direction = "BUY" when leg A is the long leg, i.e. long
  the ratio). This keeps compute_metrics()'s trade count and expectancy
  honest -- one decision, one trade -- so statistical_auditor.audit()'s
  min_trades_total means real pair events, not legs counted twice.
  Disclosed side effect: performance_analyst.compute_sector_breakdown()
  buckets "A/B" under "Unknown" (sector breakdown is narrative-only, not
  part of the verdict).

Output shape of simulate_pairs() matches run_backtest()'s EXACTLY
({"trades", "trading_calendar", "symbols", "capital_per_symbol"}) so the
walk-forward / audit / narrative / save pipeline downstream is unchanged.
"symbols" lists pair labels; "capital_per_symbol" is capital_per_pair
under the existing key name, on purpose, not silently.

No lookahead anywhere: the per-day spread baseline and correlation only
ever see data strictly before the day being simulated; the live z-score
only sees bars up to and including the current one.
"""

from datetime import date
from typing import Optional

import pandas as pd

from research_lab.backtesting_engineer import Trade, _trade_pnl
from research_lab.pairs_base import PairStrategy
from research_lab.risk_manager_research import RiskParameters, should_block_new_trade


def _inner_ratio_series(df_a: pd.DataFrame, df_b: pd.DataFrame) -> pd.Series:
    """Close_A / Close_B at every timestamp present in BOTH legs."""
    joined = pd.concat([df_a["Close"].rename("a"), df_b["Close"].rename("b")], axis=1, join="inner").dropna()
    return joined["a"] / joined["b"]


def _daily_close_correlation(daily_a: Optional[pd.DataFrame], daily_b: Optional[pd.DataFrame],
                              as_of: date, lookback_days: int = 60) -> Optional[float]:
    """
    Pearson correlation of the two legs' daily-close PERCENT RETURNS (not
    price levels -- two trending stocks' levels correlate near 1.0
    regardless of whether they actually move together day to day, a
    well-known pitfall) over the trailing `lookback_days` common trading
    days strictly before `as_of`. None if either series is missing or
    there are fewer than lookback_days+1 overlapping prior rows -- the
    caller treats None as "not eligible" (fails closed).
    """
    if daily_a is None or daily_b is None or daily_a.empty or daily_b.empty:
        return None
    a = daily_a["Close"]
    b = daily_b["Close"]
    a = a[pd.DatetimeIndex(a.index).date < as_of]
    b = b[pd.DatetimeIndex(b.index).date < as_of]
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    if len(joined) < lookback_days + 1:
        return None
    returns = joined.tail(lookback_days + 1).pct_change().dropna()
    corr = returns["a"].corr(returns["b"])
    return None if pd.isna(corr) else float(corr)


def _pooled_ratio_baseline(df_a: pd.DataFrame, df_b: pd.DataFrame, trade_date: date,
                            lookback_days: int = 20) -> tuple:
    """
    (mean, std) of the intraday Close_A/Close_B ratio, pooling EVERY
    common bar across the trailing `lookback_days` trading days strictly
    before trade_date -- not time-of-day-matched, the same disclosed
    simplification already used for avg_max_vwap_extension_atr_20d.
    (None, None) until a full lookback_days of prior common days exist.
    Sample std (pandas default ddof=1).
    """
    ratio = _inner_ratio_series(df_a, df_b)
    if ratio.empty:
        return None, None
    prior = ratio[ratio.index.date < trade_date]
    prior_days = sorted(set(prior.index.date))
    if len(prior_days) < lookback_days:
        return None, None
    keep = set(prior_days[-lookback_days:])
    window = prior[[d in keep for d in prior.index.date]]
    std = float(window.std())
    if pd.isna(std) or std <= 0:
        return float(window.mean()), None
    return float(window.mean()), std


def _compute_pair_day_context(df_a: pd.DataFrame, df_b: pd.DataFrame, symbol_a: str, symbol_b: str,
                               trade_date: date, lookback_days: int = 20,
                               correlation_lookback_days: int = 60, correlation_threshold: float = 0.8,
                               daily_a: Optional[pd.DataFrame] = None,
                               daily_b: Optional[pd.DataFrame] = None) -> dict:
    """The per-pair, per-day facts a PairStrategy needs but cannot see in
    today's bars alone -- the pairs analogue of backtesting_engineer.
    _compute_day_context(). See pairs_base.PairStrategy for the key
    meanings. correlation_ok is False whenever the correlation is None."""
    mean, std = _pooled_ratio_baseline(df_a, df_b, trade_date, lookback_days)
    corr = _daily_close_correlation(daily_a, daily_b, trade_date, correlation_lookback_days)
    return {
        "symbol_a": symbol_a, "symbol_b": symbol_b,
        "baseline_mean": mean, "baseline_std": std,
        "correlation": corr,
        "correlation_ok": corr is not None and corr > correlation_threshold,
    }


def _zscore(ratio: float, context: dict) -> Optional[float]:
    mean, std = context.get("baseline_mean"), context.get("baseline_std")
    if mean is None or std is None or std <= 0:
        return None
    return (ratio - mean) / std


def _check_spread_exit(z: float, entry_z: float, stop_zscore: float, target_zscore: float) -> tuple:
    """Returns (hit_stop, hit_target) for one bar, stop checked first
    (same precedence as _check_exit). Stop: the spread has widened
    FURTHER on the entry's own side to at least stop_zscore. Target: the
    spread has reverted to within target_zscore of zero, OR has crossed
    all the way to the opposite side (which is past the target, however
    fast it got there)."""
    same_side = (z > 0) == (entry_z > 0)
    if same_side and abs(z) >= stop_zscore:
        return True, False
    if not same_side or abs(z) <= target_zscore:
        return False, True
    return False, False


def _size_pair_legs(capital_per_pair: float, price_a: float, price_b: float) -> tuple:
    """Equal-notional split: capital_per_pair/2 on each leg, each rounded
    DOWN to whole shares independently -- so the two legs' realized
    notional differs slightly after rounding (disclosed; same
    whole-shares convention as every other engine here)."""
    if price_a <= 0 or price_b <= 0:
        return 0, 0
    notional = capital_per_pair / 2
    return int(notional // price_a), int(notional // price_b)


def simulate_pair(df_a: pd.DataFrame, df_b: pd.DataFrame, symbol_a: str, symbol_b: str,
                   strategy: PairStrategy, capital_per_pair: float,
                   daily_a: Optional[pd.DataFrame] = None, daily_b: Optional[pd.DataFrame] = None,
                   risk_params: Optional[RiskParameters] = None, lookback_days: int = 20,
                   correlation_lookback_days: int = 60, correlation_threshold: float = 0.8) -> list:
    """
    One pair, day by day, bar by bar over the timestamps present in BOTH
    legs that day (inner join -- a bar missing on either side is skipped;
    a day with under 4 common bars is skipped entirely, same partial-day
    guard as market_simulator). Mandatory EOD square-off of both legs at
    the pair's last common bar. With risk_params=None, one round-trip per
    pair per day (simulate_symbol's own default); with RiskParameters,
    the pair's combined realized P&L and trade count are checked against
    should_block_new_trade() using capital_per_pair as the denominator.
    No re-entry on the bar a position was just closed on.

    Returns the list of combined Trades (one per round-trip -- see the
    module docstring for the exact field semantics).
    """
    trades = []
    label = f"{symbol_a}/{symbol_b}"
    common_dates = sorted(set(df_a.index.date) & set(df_b.index.date))

    for trade_date in common_dates:
        day_a = df_a[df_a.index.date == trade_date]
        day_b = df_b[df_b.index.date == trade_date]
        common_ts = day_a.index.intersection(day_b.index).sort_values()
        if len(common_ts) < 4:
            continue
        context = _compute_pair_day_context(
            df_a, df_b, symbol_a, symbol_b, trade_date, lookback_days=lookback_days,
            correlation_lookback_days=correlation_lookback_days, correlation_threshold=correlation_threshold,
            daily_a=daily_a, daily_b=daily_b,
        )

        position = None
        trades_today = 0
        pnl_today = 0.0

        for i, ts in enumerate(common_ts):
            is_last_bar = i == len(common_ts) - 1
            bars_a = day_a.loc[common_ts[:i + 1]]
            bars_b = day_b.loc[common_ts[:i + 1]]
            close_a = float(bars_a.iloc[-1]["Close"])
            close_b = float(bars_b.iloc[-1]["Close"])
            ratio = close_a / close_b

            if position is not None:
                z = _zscore(ratio, context)
                hit_stop, hit_target = (False, False) if z is None else _check_spread_exit(
                    z, position["entry_z"], position["stop_zscore"], position["target_zscore"])
                if hit_stop:
                    exit_reason = "stop_loss"
                elif hit_target:
                    exit_reason = "target"
                elif is_last_bar:
                    exit_reason = "eod_square_off"
                else:
                    continue
                long_exit = close_a if position["long_is_a"] else close_b
                short_exit = close_b if position["long_is_a"] else close_a
                pnl = (_trade_pnl("BUY", position["long_entry"], long_exit, position["long_qty"])
                       + _trade_pnl("SELL", position["short_entry"], short_exit, position["short_qty"]))
                trades.append(Trade(
                    symbol=label, entry_date=trade_date, exit_date=trade_date,
                    entry_price=position["entry_ratio"], exit_price=ratio,
                    quantity=position["qty_a"], pnl=pnl, exit_reason=exit_reason,
                    entry_hour=position["entry_hour"],
                    direction="BUY" if position["long_is_a"] else "SELL",
                ))
                trades_today += 1
                pnl_today += pnl
                position = None
                continue

            if is_last_bar:
                continue
            if risk_params is None:
                if trades_today >= 1:
                    continue
            elif should_block_new_trade(pnl_today, trades_today, capital_per_pair, risk_params):
                continue

            signal = strategy.generate_pair_signal(bars_a, bars_b, context)
            if signal is None:
                continue
            qty_a, qty_b = _size_pair_legs(capital_per_pair, close_a, close_b)
            if qty_a <= 0 or qty_b <= 0:
                continue
            long_is_a = signal.long_leg == "a"
            position = {
                "long_is_a": long_is_a,
                "long_entry": close_a if long_is_a else close_b,
                "short_entry": close_b if long_is_a else close_a,
                "long_qty": qty_a if long_is_a else qty_b,
                "short_qty": qty_b if long_is_a else qty_a,
                "qty_a": qty_a,
                "entry_ratio": ratio, "entry_z": signal.entry_zscore,
                "stop_zscore": signal.stop_zscore, "target_zscore": signal.target_zscore,
                "entry_hour": ts.hour + ts.minute / 60,
            }

    return trades


def simulate_pairs(data: dict, pairs: list, strategy: PairStrategy, capital_per_pair: float,
                    daily_data: Optional[dict] = None, risk_params: Optional[RiskParameters] = None,
                    lookback_days: int = 20, correlation_lookback_days: int = 60,
                    correlation_threshold: float = 0.8) -> dict:
    """
    data: {symbol: intraday DataFrame} for every underlying symbol across
    all configured pairs (bare-symbol keys, same as run_backtest()).
    pairs: [(symbol_a, symbol_b), ...]. A pair missing either leg's data
    is skipped with a warning, not a crash.
    daily_data: optional {symbol: daily DataFrame} (bare-symbol keys) for
    the correlation filter; without it, correlation_ok is False for every
    pair on every day and nothing trades -- deliberately fail-closed.

    Returns run_backtest()'s exact shape; see the module docstring.
    """
    daily_data = daily_data or {}
    trades = []
    calendar = set()
    labels = []
    for symbol_a, symbol_b in pairs:
        df_a, df_b = data.get(symbol_a), data.get(symbol_b)
        if df_a is None or df_b is None or df_a.empty or df_b.empty:
            print(f"WARNING: skipping pair {symbol_a}/{symbol_b} -- no intraday data for one or both legs.")
            continue
        labels.append(f"{symbol_a}/{symbol_b}")
        trades.extend(simulate_pair(
            df_a, df_b, symbol_a, symbol_b, strategy, capital_per_pair,
            daily_a=daily_data.get(symbol_a), daily_b=daily_data.get(symbol_b),
            risk_params=risk_params, lookback_days=lookback_days,
            correlation_lookback_days=correlation_lookback_days, correlation_threshold=correlation_threshold,
        ))
        calendar.update(set(df_a.index.date) & set(df_b.index.date))
    return {
        "trades": trades, "trading_calendar": sorted(calendar),
        "symbols": labels, "capital_per_symbol": capital_per_pair,
    }
