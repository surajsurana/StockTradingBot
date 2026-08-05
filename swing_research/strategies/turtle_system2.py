"""
Turtle Trading -- System 2 (the 55-day/20-day long-term breakout variant,
no whipsaw filter). Implemented exactly as documented by Richard Dennis /
William Eckhardt's original 1983-84 rules, per Curtis Faith's "Way of the
Turtle" (2007) and the publicly-archived original Turtle Rules -- see the
approved implementation plan for full source discussion and why System 2
was chosen over System 1 (the whipsaw-filtered 20-day variant) as the
first faithful test.

THIS IS A DELIBERATE, DISCLOSED SCOPE REDUCTION FROM THE ORIGINAL SYSTEM,
approved before implementation began:
- LONG ONLY. The original system is symmetric long/short. NSE cash equities
  don't have the SLB (securities lending & borrowing) infrastructure this
  bot would need for a genuine multi-week short position, and MIS intraday
  shorting doesn't fit a multi-week swing hold. Not silently dropped --
  stated here and in every experiment record this strategy produces.
- Single asset class (NSE cash equities), not the original's ~20+
  diversified, historically low-correlated futures markets. This is
  exactly the "does the mechanism transfer" question this experiment
  exists to answer, not a flaw being patched around.

Rules (System 2, long side):
- N = 20-day Wilder-smoothed True Range: N_today = (19*N_yesterday + TR_today)/20,
  seeded by a simple 20-day average of TR for the first value.
  TR = max(High-Low, |High-PrevClose|, |Low-PrevClose|).
- Entry: today's Close breaks above the highest High of the PRIOR 55 days
  (strictly prior -- .shift(1) before .rolling(55).max(), no lookahead).
- Exit (signal-based): today's Close breaks below the lowest Low of the
  PRIOR 20 days (.shift(1) before .rolling(20).min()).
- Stop-loss: 2N below the most recent unit's entry price; the WHOLE
  position's stop rises with each new pyramid unit (never lowered).
- Position sizing: 1 Unit = floor(equity * risk_pct_per_unit / N) shares,
  where risk_pct_per_unit=0.02 here -- mathematically equivalent to the
  original "1% of equity per N, stop at 2N" convention, since
  risk_per_share at entry is exactly 2N (see backtesting_engine.py's
  sizing formula, which uses entry-to-stop distance generically): risking
  2% of equity over a 2N stop distance is the same dollar risk as the
  original's "1% per N of movement, 2N to the stop."
- Pyramiding: add 1 unit every +0.5N of favorable movement from the LAST
  unit's own entry price, up to 4 units total per symbol (max_units=4).
  Each new unit is itself sized at 2% of CURRENT equity / (its own 2N),
  and its 2N-below-entry level becomes the new whole-position stop.
"""

from typing import Optional
import pandas as pd

from swing_research.base import OpenPosition, Signal, Strategy

BREAKOUT_ENTRY_DAYS = 55
BREAKOUT_EXIT_DAYS = 20
N_PERIOD = 20
STOP_N_MULTIPLE = 2.0
PYRAMID_N_STEP = 0.5


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def _wilder_n(true_range: pd.Series, period: int = N_PERIOD) -> pd.Series:
    """N_today = (19*N_yesterday + TR_today) / 20, seeded by a simple
    period-day average of TR for the first value -- exact Wilder smoothing
    per the original Turtle Rules, not a plain/EMA approximation."""
    n = pd.Series(index=true_range.index, dtype=float)
    seed = true_range.iloc[:period].mean()
    if period - 1 < len(n):
        n.iloc[period - 1] = seed
    for i in range(period, len(true_range)):
        prev_n = n.iloc[i - 1]
        if pd.isna(prev_n):
            continue
        n.iloc[i] = ((period - 1) * prev_n + true_range.iloc[i]) / period
    return n


class TurtleSystem2Strategy(Strategy):
    name = "turtle_system2"
    max_units = 4
    risk_pct_per_unit = 0.02   # see module docstring -- equivalent to "1% per N, stop at 2N"
    min_lookback_days = BREAKOUT_ENTRY_DAYS  # the binding constraint (55) vs. N's 20-day seed

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        tr = _true_range(df)
        df["N"] = _wilder_n(tr)
        # Strictly prior days only -- .shift(1) BEFORE the rolling window,
        # so today's own High/Low never leaks into today's breakout levels.
        df["entry_level"] = df["High"].shift(1).rolling(BREAKOUT_ENTRY_DAYS).max()
        df["exit_level"] = df["Low"].shift(1).rolling(BREAKOUT_EXIT_DAYS).min()
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if pd.isna(row.N) or pd.isna(row.entry_level) or row.N <= 0:
            return None
        if row.Close <= row.entry_level:
            return None
        entry_price = float(row.Close)
        stop_loss = entry_price - STOP_N_MULTIPLE * float(row.N)
        if stop_loss >= entry_price:
            return None
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            strategy_name=self.name,
            reason=f"55-day breakout above {row.entry_level:.2f} (N={row.N:.2f})",
        )

    def pyramid_signal_at(self, row, open_position: OpenPosition) -> Optional[Signal]:
        if pd.isna(row.N) or row.N <= 0:
            return None
        last_entry = open_position.last_unit_entry_price
        favorable_move = float(row.Close) - last_entry
        if favorable_move < PYRAMID_N_STEP * float(row.N):
            return None
        entry_price = float(row.Close)
        stop_loss = entry_price - STOP_N_MULTIPLE * float(row.N)
        if stop_loss >= entry_price or stop_loss <= open_position.stop_loss:
            return None  # never lower the whole position's stop
        return Signal(
            symbol="", direction="BUY", entry_price=entry_price, stop_loss=stop_loss,
            strategy_name=self.name,
            reason=f"Pyramid unit {len(open_position.units) + 1}: +0.5N move from last unit ({last_entry:.2f})",
        )

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if pd.isna(row.exit_level):
            return None
        if row.Close < row.exit_level:
            return float(row.Close)
        return None
