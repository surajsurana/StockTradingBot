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

from array import array
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from swing_research.base import OpenPosition, PositionUnit, Strategy
from swing_research.candidate_ranking import rank_candidate_symbols


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


def size_quantity(raw_quantity: float, strategy) -> float:
    """Whole shares (int) for every equity strategy; 6-decimal fractions
    when the strategy declares fractional_quantities (crypto lane,
    2026-09-13). Added so the crypto lane needs no parallel engine."""
    if getattr(strategy, "fractional_quantities", False):
        return round(float(raw_quantity), 6)
    return int(raw_quantity)


def _trade_pnl(direction: str, entry_price: float, exit_price: float, quantity: int) -> float:
    if _is_long(direction):
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def _to_date(ts) -> date:
    return ts.date() if hasattr(ts, "date") else ts


def date_window(frame, start: date, end: date):
    """
    The rows of `frame` (DataFrame or Series) dated start..end inclusive -- exactly
    frame[(frame.index.date >= start) & (frame.index.date <= end)], the walk-forward window
    slice every lane uses.

    WHY (2026-10-25). That boolean selection always copies, so each walk-forward window held a
    second copy of a third of the universe (~25 MB for the 503-symbol US run) on top of the
    full data. When the selected rows are one contiguous run -- always so for a date-sorted
    index -- this returns the same rows as a positional slice, which pandas shares with the
    full frame instead of copying. Same rows, labels, order and values; anything else (an
    unsorted index whose selection has gaps, or no rows at all) takes the original boolean path.
    """
    days = frame.index.date
    mask = (days >= start) & (days <= end)
    hits = np.flatnonzero(mask)
    if len(hits) and hits[-1] - hits[0] + 1 == len(hits):
        return frame.iloc[hits[0]:hits[-1] + 1]
    return frame[mask]


def _sorted_by_date(df):
    """df.sort_index() without the copy when the index is already sorted -- the usual case for
    fetched bars. Identical rows either way: sort_index() itself returns the frame unchanged
    (as a copy) when the index is monotonic increasing. Added 2026-10-25 so the benchmark
    engines do not hold a second copy of the whole universe for the length of the run."""
    return df if df.index.is_monotonic_increasing else df.sort_index()


def _day_keys(index, key_intern: dict) -> list:
    """_to_date() of every index entry, interned in key_intern so all symbols share one date
    object per calendar day (2026-10-25: one fresh date per symbol per day was ~50 MB on the
    503-symbol US universe). sorted(key_intern) is then the union calendar."""
    return [key_intern.setdefault(k, k) for k in map(_to_date, index)]


def _positions_on_calendar(keys: list, slot: dict, n_days: int) -> array:
    """Calendar slot -> row position in `keys` (-1 = no bar that day), as a 4-byte-per-day array
    instead of a {date: position} dict per symbol (2026-10-25, memory). Later rows win on a
    duplicated date, exactly as the {date: ... for ...} dict comprehensions this replaces did."""
    pos = array("i", [-1]) * n_days
    for p, key in enumerate(keys):
        pos[slot[key]] = p
    return pos


def _prune_dead_view_refs(df) -> None:
    """
    Free pandas' bookkeeping for slices of `df` that no longer exist. Under copy-on-write
    (always on from pandas 3) every view sliced from a frame leaves a weakref on the frame,
    and dead ones are only pruned every ~500 new views -- so the benchmark loop's daily
    df.iloc[: i + 1] window leaves up to ~500 dead weakrefs per block per symbol alive, ~85 MB
    across the 503-symbol US universe (measured 2026-10-25). has_reference() is what pandas
    itself calls before a write to prune them; it reads and changes no data. Best-effort: a
    pandas without these internals (or without copy-on-write tracking) just skips it.
    """
    try:
        for block in df._mgr.blocks:
            block.refs.has_reference()
        index_refs = getattr(df.index, "_references", None)
        if index_refs is not None:
            index_refs.has_reference()
    except AttributeError:
        pass


def _arrays_by_name(sources, index) -> dict:
    """{column name: ndarray} for every uniquely named column of the frames/Series in `sources`
    that share `index` exactly -- arrays the caller holds anyway, so a precomputed column equal
    to one of them need not be stored twice."""
    found = {}
    for src in sources:
        if src is None or not src.index.equals(index):
            continue
        if isinstance(src, pd.Series):
            if isinstance(src.name, str) and isinstance(src.dtype, np.dtype):
                found.setdefault(src.name, src.to_numpy())
        elif src.columns.is_unique:
            for name in src.columns:
                if isinstance(name, str) and isinstance(src[name].dtype, np.dtype):
                    found.setdefault(name, src[name].to_numpy())
    return found


class _SharedDates:
    """One date object per distinct day, shared by every symbol's date-valued columns."""
    __slots__ = ("code_of", "table")

    def __init__(self):
        self.code_of, self.table = {}, []

    def code(self, value) -> int:
        code = self.code_of.get(value)
        if code is None:
            code = self.code_of[value] = len(self.table)
            self.table.append(value)
        return code


def _compact_object_column(values, shared_dates: _SharedDates):
    """Row-position getter for an object column, returning exactly the objects
    ndarray.item() would (see _LazySymbolRows), stored compactly when possible."""
    if len(values) and all(type(v) is bool for v in values):
        return np.array(values, dtype=bool).item          # .item() -> the same Python bools
    if len(values) and all(type(v) is date for v in values):
        codes = array("i", [shared_dates.code(v) for v in values])
        return lambda p, _codes=codes, _table=shared_dates.table: _table[_codes[p]]
    return np.array(values, dtype=object).item


class _LazySymbolRows:
    """
    One symbol's precomputed bars, looked up by date -- a memory-lean
    stand-in for the {date: row-namedtuple} dict simulate_portfolio() used
    to build for every symbol up front.

    WHY (2026-10-25). That dict held one namedtuple of boxed Python scalars
    plus one fresh date object per symbol per day for the whole run: ~1.26M
    rows for the 503-symbol, 10-year US lane, ~450 MB, which is what got
    run_us_experiment.py OOM-killed on the 458 MB VPS. This keeps the same
    values column by column instead (numpy arrays, never re-typed) and builds
    a row only when the day loop asks for it. A row built here is exactly the
    row itertuples(index=False) would have produced: the same namedtuple class
    pandas builds (taken from pandas itself) and the same per-cell conversion
    pandas' Series iteration does (ndarray.item for numpy-backed columns, the
    Series' own iteration otherwise) -- so every strategy hook sees identical
    values and every trade, fill and metric is unchanged.

    Further savings, all value-preserving:
    - A numeric column bit-identical to one the caller already holds -- the
      input frame's own OHLCV (after the strategy's df.copy()) or the injected
      cross-sectional column -- reuses that array instead of a second copy.
    - An object column holding only Python bools is kept as a numpy bool
      array: ndarray.item() gives back the very same True/False objects.
    - An object column holding only datetime.date values (e.g. a strategy's
      df["date"] = df.index.date) is kept as one shared date object per
      calendar day plus a 4-byte index per row, instead of an 8-byte pointer
      and its own date object per symbol per day. Same values, same type;
      only object identity differs, which nothing compares.
    """

    __slots__ = ("keys", "last_key", "_pos", "_slot", "_make", "_getters")

    def __init__(self, precomputed, sources, key_intern: dict, shared_dates: _SharedDates):
        # Calendar keys, interned so every symbol shares one object per day.
        self.keys = _day_keys(precomputed.index, key_intern)
        self.last_key = None
        self._pos = None
        self._slot = None
        self._make = (type(next(precomputed.iloc[:1].itertuples(index=False)))._make
                      if len(precomputed) else tuple)
        reusable = _arrays_by_name(sources, precomputed.index)
        getters = []
        for k, col in enumerate(precomputed.columns):
            series = precomputed.iloc[:, k]
            dtype = series.dtype
            if isinstance(dtype, np.dtype) and dtype.kind not in "mM":
                # numpy-backed: Series iteration yields ndarray.item(i) -- reproduce exactly.
                values = series.to_numpy()
                held = reusable.get(col) if isinstance(col, str) else None
                if (held is not None and dtype.kind != "O" and held.dtype == dtype
                        and held.tobytes() == values.tobytes()):   # bitwise, so -0.0/NaN payloads count
                    getters.append(held.item)
                elif dtype.kind == "O":
                    getters.append(_compact_object_column(values, shared_dates))
                else:
                    # Own copy: a bare view would pin the precomputed frame's whole 2-D block.
                    getters.append(np.array(values, copy=True).item)
            else:
                # Extension/datetime dtypes: keep exactly the objects Series iteration yields.
                getters.append(list(series).__getitem__)
        self._getters = tuple(getters)

    def index_against(self, slot: dict, n_days: int) -> None:
        """Attach the shared calendar (see _positions_on_calendar); the per-row key list is
        only needed to build that, so it is dropped once the last bar's day is noted."""
        self._pos = _positions_on_calendar(self.keys, slot, n_days)
        self._slot = slot
        self.last_key = max(self.keys)
        self.keys = None

    def get(self, day, default=None):
        i = self._slot.get(day)
        if i is None:
            return default
        p = self._pos[i]
        if p < 0:
            return default
        return self._make([getter(p) for getter in self._getters])


def simulate_portfolio(data: dict, strategy: Strategy, starting_capital: float,
                        sector_map: Optional[dict] = None,
                        max_units_per_symbol: Optional[int] = None,
                        max_units_per_sector: int = 6, max_units_total: int = 10,
                        min_bars_required: int = 60,
                        extra_columns_by_symbol: Optional[dict] = None,
                        min_trade_notional: float = 0.0) -> dict:
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

    # Precompute once per symbol (vectorized), then index by date for O(1)
    # lookup in the day loop below -- avoids re-slicing/re-filtering any
    # DataFrame inside the loop. Since 2026-10-25 the rows are held
    # column-wise and built on demand rather than as a {date: row} dict --
    # see _LazySymbolRows for why, and why every row is identical to before.
    rows_by_symbol = {}
    key_intern, shared_dates = {}, _SharedDates()
    for symbol, df in data.items():
        if df is None or df.empty or len(df) < min_bars_required:
            continue
        sources = [df]
        df = df.sort_index()
        if extra_columns_by_symbol and symbol in extra_columns_by_symbol:
            sources.append(extra_columns_by_symbol[symbol])
            df = df.join(extra_columns_by_symbol[symbol])
        precomputed = strategy.precompute(df)
        rows_by_symbol[symbol] = _LazySymbolRows(precomputed, sources, key_intern, shared_dates)
        del df, precomputed, sources

    all_dates = sorted(key_intern)
    slot = {d: i for i, d in enumerate(all_dates)}
    for rows in rows_by_symbol.values():
        rows.index_against(slot, len(all_dates))
    last_bar_by_symbol = {symbol: rows.last_key for symbol, rows in rows_by_symbol.items()}
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
            # Fixed price target (2026-09-16): the same mechanical check the paper
            # engine has had since 2026-09-06, so a strategy with a documented
            # target (ma_pullback, volume_backed_breakout) backtests as it trades.
            # Stop first, then target -- the conservative order on a day that
            # touches both. No-op for every strategy that never sets one.
            target = getattr(pos, "target_price", None)
            if target is not None and (float(row.High) >= target if pos.direction == "BUY" else float(row.Low) <= target):
                close_position(symbol, float(target), today, "target")
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
            quantity = size_quantity((equity * strategy.risk_pct_per_unit) / risk_per_share
                                     * getattr(signal, "size_multiplier", 1.0), strategy)
            if quantity <= 0:
                continue
            cost = quantity * signal.entry_price
            # A trade too small to carry its own flat charges is not worth taking. The DP charge is
            # Rs13.5 per scrip however small the trade, so a Rs3,500 position pays 0.45% in DP alone
            # -- see swing_research/broker_costs.py. Skipped, never scaled up: scaling up to clear
            # the floor would silently spend several times the strategy's own risk budget on one
            # position, and the honest answer is that this book is too small for this trade.
            if min_trade_notional and cost < min_trade_notional:
                continue
            if cost > (equity - capital_deployed()):
                continue

            pos.units.append(PositionUnit(entry_price=signal.entry_price, entry_date=today, quantity=quantity))
            pos.stop_loss = signal.stop_loss

        # 3. New entries for symbols with no open position. Collected
        # FIRST, across the whole universe, independent of dict order --
        # then RANKED by each candidate's own Signal.confidence (a
        # strategy's own already-computed percentile/magnitude where one
        # exists, a neutral default of 1.0 for every candidate otherwise)
        # with ties broken by a date-seeded, symbol-name-independent key
        # -- see swing_research/candidate_ranking.py -- and ONLY THEN
        # walked through the sector/total-cap/cash checks below. This is
        # what makes which candidates actually get today's limited slots
        # a function of rank, never of rows_by_symbol's own (alphabetical
        # universe-list) iteration order -- the defect SW-013 exposed.
        new_entry_signals = {}
        for symbol, rows in rows_by_symbol.items():
            if symbol in open_positions:
                continue
            row = rows.get(today)
            if row is None:
                continue
            last_close_by_symbol[symbol] = float(row.Close)

            signal = strategy.entry_signal_at(row)
            if signal is None:
                continue
            new_entry_signals[symbol] = signal

        ranked_symbols = rank_candidate_symbols(
            [(symbol, signal.confidence) for symbol, signal in new_entry_signals.items()], today,
        )
        for symbol in ranked_symbols:
            signal = new_entry_signals[symbol]

            sector = sector_map.get(bare_symbol(symbol)) if sector_map else None
            if sector and sector_unit_count(sector) >= max_units_per_sector:
                continue
            if total_unit_count() >= max_units_total:
                continue

            risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
            if risk_per_share <= 0:
                continue
            quantity = size_quantity((equity * strategy.risk_pct_per_unit) / risk_per_share
                                     * getattr(signal, "size_multiplier", 1.0), strategy)
            if quantity <= 0:
                continue
            cost = quantity * signal.entry_price
            # A trade too small to carry its own flat charges is not worth taking. The DP charge is
            # Rs13.5 per scrip however small the trade, so a Rs3,500 position pays 0.45% in DP alone
            # -- see swing_research/broker_costs.py. Skipped, never scaled up: scaling up to clear
            # the floor would silently spend several times the strategy's own risk budget on one
            # position, and the honest answer is that this book is too small for this trade.
            if min_trade_notional and cost < min_trade_notional:
                continue
            if cost > (equity - capital_deployed()):
                continue

            open_positions[symbol] = OpenPosition(
                symbol=symbol, direction=signal.direction,
                units=[PositionUnit(entry_price=signal.entry_price, entry_date=today, quantity=quantity)],
                stop_loss=signal.stop_loss, size_multiplier=getattr(signal, "size_multiplier", 1.0),
                target_price=getattr(signal, "target_price", None),
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

    # Per-symbol day lookup as one shared calendar + a compact position array, and no sorted
    # copy of an already-sorted frame (2026-10-25, memory -- the old {date: position} dict per
    # symbol and the sorted copies were ~200 MB on the 503-symbol US universe). Same rows, same
    # positions, same calendar; see _day_keys / _positions_on_calendar / _sorted_by_date.
    prepped = {}
    key_intern = {}
    for symbol, df in data.items():
        if df is None or df.empty or len(df) < min_bars:
            continue
        df = _sorted_by_date(df)
        prepped[symbol] = {"df": df, "keys": _day_keys(df.index, key_intern)}

    all_dates = sorted(key_intern)
    slot = {d: i for i, d in enumerate(all_dates)}
    for pos_info in prepped.values():
        pos_info["positions"] = _positions_on_calendar(pos_info.pop("keys"), slot, len(all_dates))

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
            idx = pos_info["positions"][slot[today]]
            if idx < 0:
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
                idx = pos_info["positions"][slot[today]]
                if idx < 0 or idx < min_bars:
                    continue

                window = pos_info["df"].iloc[: idx + 1]
                signal = strategies_by_symbol[symbol].generate_signal(window)
                _prune_dead_view_refs(pos_info["df"])   # memory only -- see its docstring
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
