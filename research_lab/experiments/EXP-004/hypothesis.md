# Prior-Day-High Failed-Breakout Exhaustion Fade

## Mechanism
A stock pokes above its prior day high (PDH) on a real relative-volume spike -- drawing in momentum/breakout chasers -- but then fails to hold above PDH for two consecutive 5-minute closes. Those late longs are now trapped and forced to liquidate as price falls back through the level, a self-reinforcing unwind (a trapped-trader order-flow story, the opposite of a breakout-continuation bet).

## Rationale
EXP-003, originally proposed by the Quant Researcher and selected by the Research Director over 5 other survivors. This project's first short-side strategy. Never backtested to completion -- re-run now (2026-09-07) per explicit direction.

## Rules
SHORT when a poke above PDH (before 11:00am, on volume >=1.5x the 20-day average for that time slot) fails to hold for two consecutive 5-minute closes back below PDH. Target: whichever of (current VWAP, prior day close) is numerically closer to entry, fixed at entry time. Stop: back above the poke high. Limited to the top-30 liquid names.

## Why this candidate was selected
Re-run 2026-09-07: originally selected, never backtested to completion until now.
