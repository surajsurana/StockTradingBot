"""
Turn-of-the-Month Effect -- tests whether NSE stocks earn disproportionate
returns specifically in the few trading days spanning each month's turn,
long-only, applied to every symbol in the universe uniformly. First pure
CALENDAR/SEASONALITY strategy in this program -- every prior strategy
selects cross-sectionally (a percentile rank against the universe); this
one has NO per-symbol RANKING criterion at all, since the original
finding is a market-wide timing effect, not a stock-picking one.

Source: Ariel, R.A. (1987), "A Monthly Effect in Stock Returns," Journal
of Financial Economics, Vol. 18, No. 1. Returns are disproportionately
concentrated in the turn-of-month window (the last trading day of the
month through the first few trading days of the next) -- originally
linked to institutional cash-flow/payroll-driven buying patterns
concentrating around month-end/month-start.

=========================== DOCUMENTED RULES ===========================

- Turn-of-month window: the LAST trading day of the month through the
  THIRD trading day of the following month (a 4-trading-day window,
  Ariel's own "-1 to +3" definition) -- the paper's finding is that
  essentially ALL of the market's cumulative return over the sample
  period is concentrated in this window; the rest of the month is flat.

===================== IMPLEMENTATION ASSUMPTIONS =====================
(see swing_research/published_research_analyst.py's TURN_OF_MONTH record
for the full disclosed reasoning behind each)

1. APPLIED PER-SYMBOL, UNIFORMLY, NOT AS A MARKET-INDEX TIMING SIGNAL.
   The original paper tests the aggregate market (index) return; this
   platform's Strategy interface is per-symbol, so every symbol in the
   universe qualifies on the SAME calendar day (no ranking at all).
   Estimated impact: DIRECTIONALLY UNKNOWN -- a real implementation might
   reasonably use an index proxy instead; this backtest instead tells us
   whether the effect shows up in individual NSE stocks.
2. DIVERSIFICATION FIX -- TWO ATTEMPTS, disclosed in full:
   ATTEMPT 1 (discarded): the shared engine's max_units_total=10 cap
   (unchanged, same default every prior strategy already uses) applies
   across the ENTIRE undifferentiated universe here, not a pre-selected
   decile -- and the engine walks candidates in the `data` dict's own
   iteration order, which is the frozen universe's ALPHABETICAL ticker
   order (swing_research/universe.py), with zero economic meaning. A
   first backtest attempt with NO tie-breaker confirmed empirically that
   this collapses to the SAME ~10 alphabetically-early symbols filling
   every month for the entire 10-year history, touching only 10 of ~20
   sectors. A first fix restricted eligibility to a STATIC rotating 1/4
   slice of the universe (a fixed per-symbol bucket, unchanging every
   time that quarter came due) -- this improved sector coverage (10->17
   of ~20) but a follow-up check found it was NOT a full fix: every
   symbol from the 3 sectors still missing had 12-125 OTHER symbols in
   its SAME static bucket that come alphabetically before it, EVERY
   SINGLE TIME that bucket is active -- a persistent, near-permanent
   structural exclusion, not residual noise, because the underlying
   engine's fixed alphabetical iteration order was never actually
   touched, just the size of the pool competing within it.
   ATTEMPT 2 (current): replaces the STATIC per-symbol bucket with a
   PER-MONTH COMBINED RANK of (symbol, absolute_month_index) -- see
   compute_monthly_eligibility() below. Each calendar month, ALL
   currently-tradeable symbols are ranked afresh by a deterministic hash
   of their own name combined with THAT month's index, and only the top
   ELIGIBLE_PER_MONTH are eligible that month. Because the hash mixes in
   the month itself, WHICH symbols land in the eligible pool -- and,
   within it, each symbol's own alphabetical standing relative to that
   month's specific cohort -- genuinely reshuffles every month, instead
   of repeating the same fixed competitive landscape forever. A symbol
   that was permanently disadvantaged under Attempt 1 now has a real,
   periodically-favorable draw (few alphabetically-earlier rivals in that
   month's specific eligible set) roughly as often as any other symbol,
   rather than a fixed handicap applied identically every time.
   Both attempts are strategy-level mechanisms only -- NEITHER changes
   swing_research/backtesting_engine.py or any other strategy's own
   frozen results; both inject their column via extra_columns_by_symbol,
   the same mechanism every prior strategy already uses for its own
   cross-sectional signal.
3. HOLDING PERIOD = exactly 3 trading days after entry (computed by ROW
   POSITION, not the generic HOLDING_PERIOD_CALENDAR_DAYS approximation
   every other strategy uses -- that trading-day-to-calendar-day
   conversion is a reasonable approximation for the 21+ trading day holds
   used elsewhere, but would be unreliable at this strategy's much
   shorter, weekend-sensitive 3-trading-day horizon). No percentile-based
   early exit (there is no percentile at all for this strategy).
4. PROTECTIVE STOP-LOSS: 8% below entry, same convention as every prior
   strategy. NOT PART OF THE ORIGINAL METHODOLOGY AT ALL.
5. POSITION SIZING: standard risk_pct_per_unit convention (1% of equity
   per unit) -- not documented in the source.
6. min_lookback_days = 1 -- this signal needs essentially NO historical
   warm-up (no rolling window of any kind, purely calendar position),
   the opposite structural situation from Long-Term Reversal's
   multi-year-formation problem: this strategy should get MANY feasible
   walk-forward windows in the mandatory recent-period check.

Entry fires on the LAST trading day of every calendar month, restricted
to whichever symbols that month's per-month eligibility ranking selected
-- no state-transition check needed (unlike every percentile-based
strategy) since the underlying is_month_end flag is naturally a
single-day event, never a multi-day plateau.
"""

import zlib
from typing import Optional

import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

TOM_EXIT_LAG_TRADING_DAYS = 3   # Ariel's own "-1 to +3" turn-of-month window
STOP_LOSS_PCT = 0.08

# Disclosed diversification fix, attempt 2 (see module docstring, point
# 2). 40 is a generous buffer over the shared engine's max_units_total=10
# (accounting for the 6-per-sector sub-cap and sector-size skew still
# occasionally leaving slots unfilled) while being a SMALL ENOUGH fraction
# of the ~457-symbol universe (~8.75%) that the top-40-by-hash actually
# reshuffles composition meaningfully from month to month, rather than
# converging back toward "most of the universe, every time" (which would
# reproduce Attempt 1's problem in a different guise). Chosen for this
# reasoning BEFORE any backtest was re-run with it, not tuned on results.
ELIGIBLE_PER_MONTH = 40


def _symbol_month_priority_hash(symbol: str, absolute_month_index: int) -> int:
    """
    Deterministic combined hash of a symbol and an absolute month index,
    via zlib.crc32 -- a standard, well-distributed, stdlib checksum,
    reproducible across runs and machines (deliberately NOT Python's
    built-in hash(), which is salted per-process by default and would
    make backtests non-reproducible run-to-run).
    """
    return zlib.crc32(f"{symbol}|{absolute_month_index}".encode())


def compute_monthly_eligibility(data: dict, eligible_per_month: int = ELIGIBLE_PER_MONTH) -> dict:
    """
    data: {symbol: DataFrame of daily OHLCV bars}.

    For every calendar month present anywhere in `data`, ranks every
    symbol that has at least one bar in that month by
    _symbol_month_priority_hash(symbol, month) and marks the top
    eligible_per_month as eligible for that month -- a genuinely
    ROTATING front-of-queue whose composition changes every month (see
    module docstring's "ATTEMPT 2" for why this fixes Attempt 1's
    persistent, verified exclusion of specific symbols).

    Returns {symbol: pd.Series of bool, indexed by that symbol's own
    dates} -- True on every day within a calendar month this symbol
    ranked in the top eligible_per_month for that month -- for injection
    via extra_columns_by_symbol (see
    research_director.run_turn_of_month_experiment()).
    """
    period_set_by_symbol = {}
    dates_by_symbol = {}
    all_periods = set()
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        sorted_dates = df.sort_index().index
        periods = sorted_dates.to_period("M")
        period_set_by_symbol[symbol] = set(periods)
        dates_by_symbol[symbol] = sorted_dates
        all_periods.update(periods)

    eligible_periods_by_symbol = {symbol: set() for symbol in period_set_by_symbol}
    for period in all_periods:
        absolute_month_index = period.year * 12 + period.month
        candidates = [symbol for symbol, periods in period_set_by_symbol.items() if period in periods]
        if not candidates:
            continue
        ranked = sorted(candidates, key=lambda s: _symbol_month_priority_hash(s, absolute_month_index))
        for symbol in ranked[:eligible_per_month]:
            eligible_periods_by_symbol[symbol].add(period)

    result = {}
    for symbol, dates in dates_by_symbol.items():
        eligible_periods = eligible_periods_by_symbol[symbol]
        symbol_periods = dates.to_period("M")
        values = [period in eligible_periods for period in symbol_periods]
        result[symbol] = pd.Series(values, index=dates, name="eligible_this_month")
    return result


class TurnOfMonthStrategy(Strategy):
    name = "turn_of_month"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 1

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        df["date"] = df.index.date

        year_month = df.index.to_period("M")
        last_trading_day_per_month = df.groupby(year_month).apply(lambda g: g.index.max())
        df["is_month_end"] = df.index.isin(last_trading_day_per_month.values)

        # Exit day = exactly TOM_EXIT_LAG_TRADING_DAYS trading-day ROWS after
        # an is_month_end row -- a row-position shift, not a calendar-day
        # approximation, so weekends/holidays never distort the intended
        # 3-TRADING-day holding period. NaN-safe: shift() naturally leaves
        # the last few rows of the dataset False (nothing to compare).
        df["is_tom_exit_day"] = df["is_month_end"].shift(TOM_EXIT_LAG_TRADING_DAYS).fillna(False)

        # eligible_this_month is injected by the caller (research_director,
        # via simulate_portfolio()'s extra_columns_by_symbol) BEFORE
        # precompute() runs -- see compute_monthly_eligibility() above and
        # run_turn_of_month_experiment() in research_director.py. If
        # genuinely absent (e.g. a unit test not exercising the eligibility
        # wiring), treat as "not eligible" -- same "absent column means
        # don't qualify" convention as every percentile-based strategy's
        # own missing-column fallback.
        if "eligible_this_month" not in df.columns:
            df["eligible_this_month"] = False
        df["qualifies_for_entry"] = df["is_month_end"] & df["eligible_this_month"].fillna(False)

        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if not bool(row.qualifies_for_entry):
            return None
        entry_price = float(row.Close)
        stop_loss = entry_price * (1 - STOP_LOSS_PCT)
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            strategy_name=self.name,
            reason=("Last trading day of the month, symbol ranked in this month's eligible "
                    "cohort -- entering the turn-of-month window"),
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if bool(row.is_tom_exit_day):
            return float(row.Close)
        return None
