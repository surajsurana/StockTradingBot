# Overnight Return Anomaly

## Mechanism
A stock's TOTAL (Close-to-Close) return decomposes into an overnight (Close-to-Open) component and an intraday (Open-to-Close) component. The paper's central finding is a genuine 'tug of war': the overnight component PERSISTS (high past cumulative overnight return predicts high future overnight return -- attributed to sustained institutional/attention-driven buying pressure that clears mostly at the open), while the intraday component REVERSES over the same horizon -- two halves of the same total return moving in OPPOSITE directions. First strategy in this program built on a return COMPONENT rather than total return.

## Rationale
21-trading-day (1-month) formation window -- this program's own established convention for a short-horizon anomaly (identical choice to Short-Term Reversal, SW-008), not one single window uniquely prescribed by the source papers, which examine several horizons. See swing_research/cross_sectional.py's OVERNIGHT_RETURN_FORMATION_DAYS.

LONG ONLY (approved, disclosed, same reason as every prior strategy). SINGLE-VINTAGE HOLDING with an 8% protective stop-loss and 1% risk-per-unit sizing, NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- same disclosed pattern as every other strategy. *** THE MOST IMPORTANT DISCLOSED GAP: HOLDING PERIOD = 1 TRADING DAY (the shortest this program's Trade-based backtesting engine can express), combined with next-day-open fill timing (execution_realism_engine.py, same mechanism approved for Amihud, SW-010) -- this was expected to approximate a pure overnight-only round trip, but does NOT: shifting BOTH the entry and exit to an Open price means the realized hold spans Open(t+1) to Open(t+2), i.e. one FULL intraday session plus its flanking overnight moves, not overnight return in isolation. Per the source papers' own finding, that intraday session is expected to move in the OPPOSITE direction from the overnight-persistence effect under test -- so this implementation nets the effect against a same-magnitude, oppositely-signed contaminating return, rather than isolating it. This program's Trade-based engine (single entry_price/exit_price per trade, no intra-trade session accounting) cannot express a genuine buy-at-close/sell-at-next-open round trip without a session-aware execution model this program does not have -- disclosed as a real, un-worked-around limitation, not papered over.

## Rules
Daily overnight return_t = Open_t / Close_{t-1} - 1. Formation-period signal = cumulative (compounded) overnight return over a trailing formation window. Cross-sectional decile sort by this figure at each formation date. Long the TOP decile (strongest cumulative overnight return) -- the paper's own long-side persistence finding.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
