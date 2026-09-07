# VWAP Extension Exhaustion Fade

## Mechanism
Track each stock live distance from its own VWAP in ATR units. When that distance exceeds 1.5x the stock trailing 20-day typical intraday VWAP-extension, and the next two consecutive 5-min candles fail to print a new extreme (range/momentum decelerating), fade the move back toward VWAP.

## Rationale
Large intraday extensions away from VWAP are usually momentum/retail chasing rather than a fundamental repricing. When that chase stalls (no new high/low for two bars), the marginal buyer/seller has run out, and VWAP-benchmarked execution algos start pulling price back toward fair value.

## Rules
Entry: on close of the 2nd non-extending 5-min bar, enter opposite to the extension direction. Stop: beyond the extension extreme by 0.3 ATR. Target: VWAP itself (exit on touch) or EOD square-off. Filter: only top-20 liquidity names; skip if extension coincides with scheduled results/news day.

## Why this candidate was selected
Selected by Research Director ranking, 2026-09-07, informed by the cross-experiment review of 6 prior REJECTs.
