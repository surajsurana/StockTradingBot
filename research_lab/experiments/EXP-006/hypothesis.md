# Breadth-Thrust Laggard Catch-Up

## Mechanism
By 10:30am, if broad market breadth shows a bullish thrust (>70% of the universe trading above its own VWAP) while Nifty is up on the day, screen for LAGGARD stocks -- still below their own VWAP -- whose trailing 15-minute return already exceeds Nifty's trailing 15-minute return (an early relative-strength turn). Enter long the moment that laggard reclaims its own VWAP. Mirrors symmetrically for a bearish thrust (<30% breadth, Nifty down): short laggards losing relative strength, on VWAP loss.

## Rationale
EXP-004, originally proposed by the Quant Researcher and selected by the Research Director over 7 other survivors -- the first hypothesis to use market breadth as its primary trigger, requiring the cross-sectional engine (market_state.py + market_simulator.py) built specifically for it. Never backtested to completion -- re-run now (2026-09-07) per explicit direction.

## Rules
Only evaluated after 10:30am. Bullish case: breadth >70% above VWAP + Nifty up on day + stock below its own VWAP + stock trailing-15min return > Nifty trailing-15min return -> long on VWAP reclaim. Bearish mirror: breadth <30% + Nifty down + stock above VWAP + stock trailing-15min return < Nifty's -> short on VWAP loss. Target: fixed 1.5x risk (R-multiple) -- the hypothesis's own stated fallback, since the original dynamic target needs a historical log market_state does not keep.

## Why this candidate was selected
Re-run 2026-09-07: originally selected, never backtested to completion until now.
