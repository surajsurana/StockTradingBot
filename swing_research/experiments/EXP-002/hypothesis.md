# Turtle Trading -- System 2 (Donchian Channel Breakout, long-term)

## Mechanism
Pure trend-following: markets trend more often and longer than a random walk would predict. Volatility-normalized position sizing (via N, the 20-day Wilder-smoothed True Range) keeps risk constant per unit regardless of how volatile a given instrument currently is.

## Rationale
System 2 (55-day entry / 20-day exit, no whipsaw filter) over System 1 (20-day entry / 10-day exit, skips a signal if the prior signal in that market won). System 1's filter requires tracking each symbol's own prior-signal outcome across the whole backtest -- more state, more implementation-risk surface. System 2 is purely mechanical per-signal. Recommended and approved as the first, cleaner test; System 1 documented in the implementation plan as a fast-follow, not started.

LONG ONLY (approved, disclosed) -- NSE cash equities lack the SLB infrastructure for a genuine multi-week short. Single asset class (NSE cash equities only) vs. the original's ~20+ diversified, historically low-correlated futures markets -- this is the transferability question the experiment exists to answer, not a gap being patched.

## Rules
Entry: Close breaks above the highest High of the prior 55 days. Exit: Close breaks below the lowest Low of the prior 20 days. Stop-loss: 2N below the most recent unit's entry price, whole-position stop rises with each pyramid unit, never lowered. Position sizing: 1 Unit = floor(equity x 1% / N) shares (dollar-value-per-point=1 for cash equities, vs. a futures contract multiplier in the original). Pyramiding: add 1 unit per +0.5N favorable move from the last unit's entry, up to 4 units per symbol. Portfolio limits: max 4 units/symbol, 6 units per correlated group (sector proxy for NSE equities), 10 units total (long-only collapses the original's separate 10/12 caps into one).

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
