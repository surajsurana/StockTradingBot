# Overnight Return Anomaly

## Mechanism
A stock's TOTAL (Close-to-Close) return decomposes into an overnight (Close-to-Open) component and an intraday (Open-to-Close) component. The paper's central finding is a genuine 'tug of war': the overnight component PERSISTS (high past cumulative overnight return predicts high future overnight return -- attributed to sustained institutional/attention-driven buying pressure that clears mostly at the open), while the intraday component REVERSES over the same horizon -- two halves of the same total return moving in OPPOSITE directions. First strategy in this program built on a return COMPONENT rather than total return.

## Rationale
21-trading-day (1-month) formation window -- this program's own established convention for a short-horizon anomaly (identical choice to Short-Term Reversal, SW-008), not one single window uniquely prescribed by the source papers, which examine several horizons. See swing_research/cross_sectional.py's OVERNIGHT_RETURN_FORMATION_DAYS.

LONG ONLY (approved, disclosed, same reason as every prior strategy). SINGLE-VINTAGE HOLDING with an 8% protective stop-loss and 1% risk-per-unit sizing, NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- same disclosed pattern as every other strategy. HOLDING PERIOD = 1 TRADING DAY (the shortest this program's Trade-based backtesting engine can express), combined with fill_timing="close_to_next_open" (added 2026-09-06 to execution_realism_engine.py, purpose-built for this strategy) -- this now achieves a genuine buy-at-close/sell-at-next-open round trip: entry_price stays at that day's Close (correct, no substitution needed), and only exit_price is replaced, with exit_date's OWN Open (not the day after) -- since the 1-trading-day hold already makes exit_date the very next trading day after entry, this is exactly Close(entry_date) -> Open(exit_date). FIXED 2026-09-06 after the original 2026-09-05 run (EXP-076, REJECT) used fill_timing="next_day_open" instead, which shifts BOTH legs to an Open price, spanning Open(t+1) to Open(t+2) -- one full extra intraday session the source papers say moves opposite the effect under test, contaminating that result. Re-run under the corrected mechanics.

## Rules
Daily overnight return_t = Open_t / Close_{t-1} - 1. Formation-period signal = cumulative (compounded) overnight return over a trailing formation window. Cross-sectional decile sort by this figure at each formation date. Long the TOP decile (strongest cumulative overnight return) -- the paper's own long-side persistence finding.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
