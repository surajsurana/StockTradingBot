# Analysis: Prior-Day-High Failed-Breakout Exhaustion Fade

## Why This Outcome Occurred

The verdict is unambiguous: **zero trades were generated** across the entire backtest window. This is not a case of a weak edge or poor risk-adjusted returns — the strategy's entry logic simply never fired. Every downstream metric (win rate, profit factor, expectancy, Sharpe, Sortino, drawdown, recovery factor) is null or zero by construction, not because the strategy broke even or lost money, but because there was no sample to measure. With 0 trades against a 30-trade minimum for statistical meaningfulness, and 0 out-of-sample trades against a 5-trade minimum for holdout validation, there is no basis to infer anything about edge, robustness, or regime dependency.

## Sector / Time / Regime Dependency

The P&L breakdowns by sector, entry-hour bucket, and market regime (bullish/bearish/unknown) are all empty or flat zero. This confirms the issue is upstream in the signal/filter logic — likely the failed-breakout detection criteria (e.g., wick rejection thresholds, volume confirmation, or the prior-day-high proximity band) are too restrictive given the instrument universe or date range tested, or there's a logic/data-alignment bug preventing any qualifying setups from being flagged.

## Follow-Up Ideas

1. **Diagnostic pass before re-testing**: Log near-misses (setups that failed only one filter condition) to identify whether thresholds are miscalibrated or whether the pattern is genuinely rare in NSE cash-equity intraday data.
2. **Widen the universe/lookback** or relax the breakout-failure definition (e.g., allow partial wick rejection) to generate enough occurrences to even evaluate the hypothesis before judging its economic merit.