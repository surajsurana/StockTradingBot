# High-Volume Return Premium

## Mechanism
An INVESTOR RECOGNITION / VISIBILITY effect (building on Merton, 1987): a sudden spike in a stock's trading volume, relative to its own typical level, draws attention to it and temporarily expands its investor base and demand -- this takes time to fully play out, producing predictable subsequent price appreciation. Distinct from Amihud's illiquidity premium (SW-010), which uses volume as the DENOMINATOR of a liquidity-cost proxy -- this strategy selects on volume ITSELF (a visibility/attention shock), not a cost measure volume happens to enter into.

## Rationale
Recent window = 5 trading days (1 week, the paper's own upper-bound recent-window language), baseline window = 252 trading days (1 year, ending immediately before the recent window, no overlap) -- this program's own reasoned operationalization of "recent vs. typical" volume, since the paper's own precise ratio construction was not independently reproduced here. See swing_research/cross_sectional.py's VOLUME_SHOCK_RECENT_DAYS/VOLUME_SHOCK_BASELINE_DAYS. 21-trading-day (1-month) holding period -- a direct restatement of the paper's own headline horizon.

LONG ONLY (approved, disclosed, same reason as every prior strategy). SINGLE-VINTAGE HOLDING with an 8% protective stop-loss and 1% risk-per-unit sizing, NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- same disclosed pattern as every other strategy. The recent/baseline volume-shock window lengths are a reasoned, disclosed interpretive choice (same category as Amihud's regression-to-decile-sort translation, SW-010), not an independently-reproduced replication of the paper's own precise construction.

## Rules
Volume shock = a stock's recent (day-to-week) trading volume relative to its own typical (longer-run) trading volume. Cross-sectional decile sort by this shock ratio at each formation date. Long the TOP decile (largest positive shock) -- the paper's own long-side finding. Holding period: approximately one month.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
