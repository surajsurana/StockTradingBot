# Gap-and-Go with VWAP Hold Confirmation

## Mechanism
A stock gapping up on real (above-average) volume, whose price never trades back down through its own intraday VWAP during or after the opening 15 minutes, is read as a sign the overnight order-flow imbalance is being absorbed by committed buyers rather than faded by retail round-tripping.

## Rationale
EXP-001, originally proposed by the Quant Researcher and selected by the Research Director over 1 other hard-filter survivor. Never backtested to completion -- re-run now (2026-09-07) per explicit direction.

## Rules
Enter on a breakout of the opening 15-minute high, on a gap-up of at least 1% with first-15-minute volume at least 1.5x the 20-day average, and only if price has not closed back through its own cumulative VWAP at any point since the open. Stop at the current VWAP. Target one gap-size measured from the prior close. Long-only.

## Why this candidate was selected
Re-run 2026-09-07: originally selected, never backtested to completion until now.
