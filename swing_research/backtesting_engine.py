"""
Swing Research Program's backtesting engine -- built fresh because
research_lab/backtesting_engineer.py is structurally incompatible with
multi-day holding (confirmed by direct inspection: its simulate_symbol()
re-initializes `position = None` INSIDE a per-trading-day loop and
force-closes anything open at each day's last bar with
exit_reason="eod_square_off", regardless of the strategy's own exit
logic -- not a flag to flip, the loop shape itself prevents multi-day
holds). This engine iterates by DATE across the whole multi-year history
with positions persisting naturally across the loop; nothing is ever
force-closed at a day boundary.

Two simulation functions, for two different callers:

- simulate_portfolio(): the primary engine, for swing_research.base.Strategy
  implementations (precompute-based -- see base.py's module docstring for
  why). Fast (vectorized precompute + O(1) day-by-day lookups), supports
  pyramiding and portfolio-level correlation-group unit caps. Used for
  Turtle Trading.
- simulate_symbol_single_unit(): a simpler, single-position-at-a-time
  engine matching backtest/backtester.py's PROVEN day-by-day growing-window
  calling convention exactly (strategy.generate_signal(window)) -- used
  ONLY to benchmark the two READ-ONLY production strategies
  (strategies/ma_crossover.py, strategies/mean_reversion.py) in their own
  native interface, never adapted. No pyramiding (neither production
  strategy does), no portfolio-level caps (aggregated by the caller across
  symbols instead) -- deliberately minimal, since its only job is a fair
  benchmark comparison, not to be this program's primary engine.

PORTFOLIO-LEVEL ASSUMPTIONS (explicit, not silently applied -- see the
approved implementation plan's Section 9/10 discussion):

- Correlation-group unit limits: the ORIGINAL Turtle rules cap concurrent
  units at 4 per market, 6 per "closely correlated" group, 10 in one
  direction across "loosely correlated" groups, 12 total portfolio-wide.
  Futures markets were grouped by asset class (grains, currencies, metals,
  etc.) for correlation purposes -- NSE cash equities have no equivalent
  built-in grouping, so this engine uses GICS-style SECTOR (from
  research_lab/performance_analyst.py's load_sector_map(), read-only reuse)
  as the "closely correlated group" proxy. Disclosed, reasonable mapping,
  not the original's exact grouping.
- Because Turtle's first run here is long-only, the "loosely correlated
  direction" cap (10) and "total portfolio" cap (12) collapse into one
  effective cap: max_units_total, set to 10 (the tighter of the two, since
  every unit is in the same direction).
- Position sizing uses REALIZED (cash-basis) equity, updated only when a
  trade closes -- not a daily mark-to-market of open positions' unrealized
  P&L. The original futures system's "account equity" naturally reflected
  margin-marked open positions; this cash-equity backtest (no leverage)
  uses realized capital as the sizing base instead. Disclosed simplification.
- A signal is silently skipped (not an error, no forced minimum position)
  if free capital can't cover even the computed unit size -- same
  soft-fail convention already used throughout this codebase's production
  risk manager.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from swing_research.base import OpenPosition, PositionUnit, Strategy


@dataclass
class Trade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    exit_reason: str        # "stop_loss", "signal_exit", "end_of_backtest"
    direction: str = "BUY"
    unit_number: int = 1    # which pyramid unit this trade represents (1 = first/only unit)


def _is_long(direction: str) -> bool:
    return direction == "BUY"


def _risk_per_share(entry_price: float, stop_loss: float, direction: str) -> float:
    return (entry_price - stop_loss) if _is_long(direction) else (stop_loss - entry_price)


def _hit_stop(direction: str, bar_low: float, bar_high: float, stop_loss: float) -> bool:
    return bar_low <= stop_loss if _is_long(direction) else bar_high >= stop_loss


def _trade_pnl(direction: str, entry_price: float, exit_price: float, quantity: int) -> float:
    if _is_long(direction):
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def _to_date(ts) -> date:
    return ts.date() if hasattr(ts, "date") else ts


def simulate_portfolio(data: dict, strategy: Strategy, starting_capital: float,
                        sector_map: Optional[dict] = None,
                        max_units_per_symbol: Optional[int] = None,
                        max_units_per_sector: int = 6, max_units_total: int = 10,
                        min_bars_required: int = 60,
                        extra_columns_by_symbol: Optional[dict] = None) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars, full multi-year history}.
    strategy: a swing_research.base.Strategy (precompute + entry/pyramid/exit hooks).
    starting_capital: realized-equity starting point (see module docstring
    on sizing basis).
    sector_map: {bare_symbol: sector}, from performance_analyst.load_sector_map()
    -- read-only reuse. None disables the sector-level cap.
    max_units_per_symbol: defaults to strategy.max_units.
    max_units_per_sector / max_units_total: correlation-group caps, see
    module docstring.
    min_bars_required: a symbol with fewer bars than this is skipped
    entirely -- not enough history for any rolling indicator to be
    meaningful. Real runs use the 60-bar default (matches this codebase's
    existing convention elsewhere); tests lower this to fit short
    synthetic fixtures.
    extra_columns_by_symbol: {symbol: pd.Series or pd.DataFrame}, joined
    onto each symbol's OHLCV frame (by date index) BEFORE
    strategy.precompute() is called -- for strategies that need a
    CROSS-SECTIONAL computation (e.g. Minervini's RS-percentile-vs-universe
    criterion, from swing_research/cross_sectional.py) that no single
    symbol's own precompute() can produce alone, since precompute() only
    ever sees one symbol's own data. Keeps Strategy.precompute()'s
    interface symbol-agnostic and uniform: by the time it runs, an
    injected column is just an ordinary column already present on the
    frame, same as any indicator precompute() would add itself.

    Returns {"trades": list[Trade], "trading_calendar": list[date],
    "symbols": list[str], "daily_equity": dict[date -> float],
    "starting_capital": float}.
    """
    max_units_per_symbol = max_units_per_symbol or strategy.max_units

    # Precompute once per symbol (vectorized), then convert to a
    # date-keyed dict of row-tuples for O(1) lookup in the day loop below
    # -- avoids re-slicing/re-filtering any DataFrame inside the loop.
    rows_by_symbol = {}
    for symbol, df in data.items():
        if df is None or df.empty or len(df) < min_bars_required:
            continue
        df = df.sort_index()
        if extra_columns_by_symbol and symbol in extra_columns_by_symbol:
            df = df.join(extra_columns_by_symbol[symbol])
        precomputed = strategy.precompute(df)
        rows_by_symbol[symbol] = {_to_date(ts): row for ts, row in
                                   zip(precomputed.index, precomputed.itertuples(index=False))}

    all_dates = sorted({d for rows in rows_by_symbol.values() for d in rows.keys()})
    last_bar_by_symbol = {symbol: max(rows.keys()) for symbol, rows in rows_by_symbol.items()}
    last_close_by_symbol = {}  # updated as we walk, for end-of-backtest force-close

    equity = starting_capital
    open_positions = {}   # symbol -> OpenPosition
    trades = []
    daily_equity = {}
    calendar = set()

    def bare_symbol(sym: str) -> str:
        return sym.replace(".NS", "")

    def sector_unit_count(sector: str, exclude_symbol: Optional[str] = None) -> int:
        return sum(
            len(pos.units) for sym, pos in open_positions.items()
            if sym != exclude_symbol and sector_map and sector_map.get(bare_symbol(sym)) == sector
        )

    def total_unit_count(exclude_symbol: Optional[str] = None) -> int:
        return sum(len(pos.units) for sym, pos in open_positions.items() if sym != exclude_symbol)

    def capital_deployed() -> float:
        return sum(u.entry_price * u.quantity for pos in open_positions.values() for u in pos.units)

    def close_position(symbol: str, exit_price: float, exit_date_: date, exit_reason: str):
        nonlocal equity
        pos = open_positions.pop(symbol)
        for i, unit in enumerate(pos.units, start=1):
            pnl = _trade_pnl(pos.direction, unit.entry_price, exit_price, unit.quantity)
            equity += pnl
            trades.append(Trade(
                symbol=symbol, entry_date=unit.entry_date, exit_date=exit_date_,
                entry_price=unit.entry_price, exit_price=exit_price, quantity=unit.quantity,
                pnl=pnl, exit_reason=exit_reason, direction=pos.direction, unit_number=i,
            ))

    for today in all_dates:
        calendar.add(today)

        # 1. Exits first -- mechanical stop-loss (price-triggered) takes
        # priority over the strategy's own signal-based exit (close-triggered).
        for symbol in list(open_positions.keys()):
            row = rows_by_symbol.get(symbol, {}).get(today)
            if row is None:
                continue
            last_close_by_symbol[symbol] = float(row.Close)
            pos = open_positions[symbol]

            if _hit_stop(pos.direction, float(row.Low), float(row.High), pos.stop_loss):
                close_position(symbol, pos.stop_loss, today, "stop_loss")
                continue

            signal_exit_price = strategy.exit_signal_at(row, pos)
            if signal_exit_price is not None:
                close_position(symbol, float(signal_exit_price), today, "signal_exit")

        # 2. Pyramid adds for symbols already held (below max units).
        for symbol, pos in list(open_positions.items()):
            if len(pos.units) >= max_units_per_symbol:
                continue
            row = rows_by_symbol.get(symbol, {}).get(today)
            if row is None:
                continue

            sector = sector_map.get(bare_symbol(symbol)) if sector_map else None
            if sector and sector_unit_count(sector, exclude_symbol=symbol) + len(pos.units) >= max_units_per_sector:
                continue
            if total_unit_count(exclude_symbol=symbol) + len(pos.units) >= max_units_total:
                continue

            signal = strategy.pyramid_signal_at(row, pos)
            if signal is None:
                continue
            risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
            if risk_per_share <= 0:
                continue
            quantity = int((equity * strategy.risk_pct_per_unit) / risk_per_share)
            if quantity <= 0:
                continue
            cost = quantity * signal.entry_price
            if cost > (equity - capital_deployed()):
                continue

            pos.units.append(PositionUnit(entry_price=signal.entry_price, entry_date=today, quantity=quantity))
            pos.stop_loss = signal.stop_loss

        # 3. New entries for symbols with no open position.
        for symbol, rows in rows_by_symbol.items():
            if symbol in open_positions:
                continue
            row = rows.get(today)
            if row is None:
                continue
            last_close_by_symbol[symbol] = float(row.Close)

            sector = sector_map.get(bare_symbol(symbol)) if sector_map else None
            if sector and sector_unit_count(sector) >= max_units_per_sector:
                continue
            if total_unit_count() >= max_units_total:
                continue

            signal = strategy.entry_signal_at(row)
            if signal is None:
                continue
            risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
            if risk_per_share <= 0:
                continue
            quantity = int((equity * strategy.risk_pct_per_unit) / risk_per_share)
            if quantity <= 0:
                continue
            cost = quantity * signal.entry_price
            if cost > (equity - capital_deployed()):
                continue

            open_positions[symbol] = OpenPosition(
                symbol=symbol, direction=signal.direction,
                units=[PositionUnit(entry_price=signal.entry_price, entry_date=today, quantity=quantity)],
                stop_loss=signal.stop_loss,
            )

        daily_equity[today] = equity  # realized equity only -- see module docstring

    # End of data: force-close anything still open at the last available price.
    for symbol in list(open_positions.keys()):
        exit_price = last_close_by_symbol.get(symbol)
        exit_date_ = last_bar_by_symbol.get(symbol, all_dates[-1] if all_dates else None)
        close_position(symbol, exit_price, exit_date_, "end_of_backtest")
        daily_equity[exit_date_] = equity

    return {
        "trades": trades, "trading_calendar": sorted(calendar),
        "symbols": list(rows_by_symbol.keys()), "daily_equity": daily_equity,
        "starting_capital": starting_capital,
    }


def simulate_symbol_single_unit(symbol: str, price_history, raw_strategy, starting_capital: float,
                                 risk_pct_per_unit: float = 0.01, regime_series=None,
                                 min_bars: int = 55) -> list:
    """
    Single-position-at-a-time engine matching backtest/backtester.py's
    PROVEN day-by-day growing-window pattern exactly -- used ONLY to
    benchmark the two production strategies in their own native interface
    (raw_strategy.generate_signal(window) -> a strategies/base.py Signal,
    single positional argument). No pyramiding, no portfolio-level caps --
    those are aggregated by the caller across symbols for the benchmark
    comparison, not modeled here.

    regime_series: optional research_lab/market_regime-style boolean
    Series, if the production strategy being benchmarked wants a BUY gated
    on strategies.market_regime.is_bullish_on() -- read-only reuse,
    imported by the caller, not this module (keeps this module import-free
    of the production package it's benchmarking).

    Returns list[Trade] for this one symbol.
    """
    from strategies.market_regime import is_bullish_on  # local import -- read-only benchmark use only

    trades = []
    open_trade = None   # (entry_price, stop_loss, target, quantity, entry_date, direction)
    equity = starting_capital

    for i in range(min_bars, len(price_history)):
        window = price_history.iloc[: i + 1]
        today = _to_date(price_history.index[i])
        today_row = price_history.iloc[i]

        if open_trade is not None:
            entry_price, stop_loss, target, quantity, entry_date, direction = open_trade
            hit_stop = float(today_row["Low"]) <= stop_loss if direction == "BUY" else float(today_row["High"]) >= stop_loss
            hit_target = float(today_row["High"]) >= target if direction == "BUY" else float(today_row["Low"]) <= target
            if hit_stop or hit_target:
                exit_price = stop_loss if hit_stop else target
                pnl = _trade_pnl(direction, entry_price, exit_price, quantity)
                equity += pnl
                trades.append(Trade(
                    symbol=symbol, entry_date=entry_date, exit_date=today,
                    entry_price=entry_price, exit_price=exit_price, quantity=quantity,
                    pnl=pnl, exit_reason="stop_loss" if hit_stop else "target", direction=direction,
                ))
                open_trade = None
            continue

        signal = raw_strategy.generate_signal(window)
        if signal is None:
            continue
        if regime_series is not None and signal.direction == "BUY" and not is_bullish_on(regime_series, price_history.index[i]):
            continue
        risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
        if risk_per_share <= 0:
            continue
        quantity = int((equity * risk_pct_per_unit) / risk_per_share)
        if quantity <= 0:
            continue
        open_trade = (signal.entry_price, signal.stop_loss, signal.target, quantity, today, signal.direction)

    if open_trade is not None:
        entry_price, stop_loss, target, quantity, entry_date, direction = open_trade
        last_close = float(price_history.iloc[-1]["Close"])
        last_date = _to_date(price_history.index[-1])
        pnl = _trade_pnl(direction, entry_price, last_close, quantity)
        trades.append(Trade(
            symbol=symbol, entry_date=entry_date, exit_date=last_date,
            entry_price=entry_price, exit_price=last_close, quantity=quantity,
            pnl=pnl, exit_reason="end_of_backtest", direction=direction,
        ))

    return trades


def simulate_portfolio_single_unit(data: dict, strategy_factory, starting_capital: float,
                                    regime_series=None, min_bars: int = 55,
                                    max_open_positions: int = 10, max_deployed_capital_pct: float = 0.60,
                                    max_capital_per_trade_pct: float = 0.12, risk_per_trade_pct: float = 0.01,
                                    daily_loss_circuit_breaker_pct: float = 0.03) -> dict:
    """
    Shared-capital-pool benchmark engine for the two READ-ONLY production
    strategies (MA Crossover, Mean Reversion) -- built for the Research
    Audit requested 2026-08-03, which found that comparing Turtle (a
    capital-constrained, shared-pool, portfolio-capped strategy) against
    simulate_symbol_single_unit() calls made independently per symbol (each
    with its OWN full starting_capital, no cross-symbol cap) was NOT an
    apples-to-apples comparison: it implicitly gave the benchmarks
    unlimited aggregate capital (starting_capital x number of symbols)
    while Turtle operated under a real, single, constrained pool. That
    asymmetry alone was enough to make the benchmarks' near-zero CAGR
    largely a denominator artifact, not a real reflection of the
    strategies' quality.

    This engine gives MA Crossover / Mean Reversion the SAME single
    starting_capital pool Turtle gets, under those production strategies'
    OWN real, live risk discipline -- the exact formulas and default values
    from risk/risk_manager.py and config/settings.py's RISK_PER_TRADE_PCT /
    MAX_OPEN_POSITIONS / MAX_DEPLOYED_CAPITAL_PCT / MAX_CAPITAL_PER_TRADE_PCT
    / DAILY_LOSS_CIRCUIT_BREAKER_PCT -- REIMPLEMENTED independently here
    (never importing risk/risk_manager.py itself), the same isolation
    convention research_lab/risk_manager_research.py already established
    for exactly this reason. This is the fairness principle: not identical
    numeric caps between Turtle and the benchmarks (Turtle keeps its own
    documented correlation-group unit limits -- that IS its published
    methodology, changing it would violate "implement exactly as
    described"), but comparable METHODOLOGICAL RIGOR -- every strategy
    operates under ITS OWN real, disclosed capital discipline, none gets
    an unlimited-capital advantage over the others.

    strategy_factory: a zero-arg callable returning a FRESH
    strategies/base.py Strategy instance -- called once per symbol (each
    symbol's own instance, matching how run_daily.py itself creates a
    Strategy) rather than sharing one instance across symbols, since
    neither production strategy holds cross-call state, but a factory
    keeps that assumption explicit rather than assumed.

    Single position per symbol (neither production strategy pyramids),
    portfolio-wide caps checked before each new entry. No transaction
    costs or slippage modeled -- see the Research Audit for why this is
    disclosed as a shared limitation, not fixed here.
    """
    from strategies.market_regime import is_bullish_on  # local import -- read-only benchmark use only

    prepped = {}
    for symbol, df in data.items():
        if df is None or df.empty or len(df) < min_bars:
            continue
        df = df.sort_index()
        prepped[symbol] = {
            "df": df,
            "date_to_pos": {_to_date(ts): i for i, ts in enumerate(df.index)},
        }

    all_dates = sorted({d for sym in prepped.values() for d in sym["date_to_pos"].keys()})

    equity = starting_capital
    open_positions = {}   # symbol -> (entry_price, stop_loss, target, quantity, entry_date, direction)
    trades = []
    daily_equity = {}
    calendar = set()
    strategies_by_symbol = {symbol: strategy_factory() for symbol in prepped}

    def capital_deployed() -> float:
        return sum(qty * entry for (entry, _, _, qty, _, _) in open_positions.values())

    for today in all_dates:
        calendar.add(today)
        realized_pnl_today = 0.0

        # 1. Exits -- O(1) row lookup, single position per symbol.
        for symbol in list(open_positions.keys()):
            pos_info = prepped[symbol]
            idx = pos_info["date_to_pos"].get(today)
            if idx is None:
                continue
            bar = pos_info["df"].iloc[idx]
            entry_price, stop_loss, target, quantity, entry_date, direction = open_positions[symbol]
            is_long = direction == "BUY"
            hit_stop = float(bar["Low"]) <= stop_loss if is_long else float(bar["High"]) >= stop_loss
            hit_target = float(bar["High"]) >= target if is_long else float(bar["Low"]) <= target
            if hit_stop or hit_target:
                exit_price = stop_loss if hit_stop else target
                pnl = _trade_pnl(direction, entry_price, exit_price, quantity)
                equity += pnl
                realized_pnl_today += pnl
                trades.append(Trade(
                    symbol=symbol, entry_date=entry_date, exit_date=today,
                    entry_price=entry_price, exit_price=exit_price, quantity=quantity,
                    pnl=pnl, exit_reason="stop_loss" if hit_stop else "target", direction=direction,
                ))
                del open_positions[symbol]

        # 2. New entries -- same RiskManager formula/order as risk/risk_manager.py's
        # evaluate(): daily-loss circuit breaker, then open-position count cap,
        # then per-trade risk sizing, then per-trade capital cap, then
        # portfolio-deployed-capital cap.
        circuit_breached = realized_pnl_today < 0 and abs(realized_pnl_today) >= equity * daily_loss_circuit_breaker_pct
        if not circuit_breached and len(open_positions) < max_open_positions:
            for symbol, pos_info in prepped.items():
                if symbol in open_positions:
                    continue
                if len(open_positions) >= max_open_positions:
                    break
                idx = pos_info["date_to_pos"].get(today)
                if idx is None or idx < min_bars:
                    continue

                window = pos_info["df"].iloc[: idx + 1]
                signal = strategies_by_symbol[symbol].generate_signal(window)
                if signal is None:
                    continue
                if regime_series is not None and signal.direction == "BUY" and not is_bullish_on(regime_series, window.index[-1]):
                    continue

                risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
                if risk_per_share <= 0:
                    continue
                quantity = int((equity * risk_per_trade_pct) / risk_per_share)
                if quantity <= 0:
                    continue

                cost = quantity * signal.entry_price
                max_per_trade = equity * max_capital_per_trade_pct
                if cost > max_per_trade:
                    quantity = int(max_per_trade / signal.entry_price)
                    cost = quantity * signal.entry_price
                if quantity <= 0:
                    continue

                max_deployed = equity * max_deployed_capital_pct
                if capital_deployed() + cost > max_deployed:
                    remaining = max_deployed - capital_deployed()
                    quantity = int(remaining / signal.entry_price) if remaining > 0 else 0
                    cost = quantity * signal.entry_price
                if quantity <= 0:
                    continue

                open_positions[symbol] = (signal.entry_price, signal.stop_loss, signal.target,
                                           quantity, today, signal.direction)

        daily_equity[today] = equity

    for symbol, (entry_price, stop_loss, target, quantity, entry_date, direction) in open_positions.items():
        df = prepped[symbol]["df"]
        last_close = float(df.iloc[-1]["Close"])
        last_date = _to_date(df.index[-1])
        pnl = _trade_pnl(direction, entry_price, last_close, quantity)
        equity += pnl
        trades.append(Trade(
            symbol=symbol, entry_date=entry_date, exit_date=last_date,
            entry_price=entry_price, exit_price=last_close, quantity=quantity,
            pnl=pnl, exit_reason="end_of_backtest", direction=direction,
        ))
        daily_equity[last_date] = equity

    return {
        "trades": trades, "trading_calendar": sorted(calendar),
        "symbols": list(prepped.keys()), "daily_equity": daily_equity,
        "starting_capital": starting_capital,
    }
