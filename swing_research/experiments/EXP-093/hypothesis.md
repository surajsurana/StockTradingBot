# Turnover / Liquidity Anomaly

## Mechanism
Stocks that trade a smaller fraction of their own shares outstanding are harder to enter and exit in size, and demand a return premium for that illiquidity -- a second, independent operationalization of the same liquidity-premium family already tested via Amihud Illiquidity (SW-011, a price-impact-per-rupee-traded proxy). Turnover and Amihud's ILLIQ are correlated but not identical: turnover ignores price impact entirely and only measures how much of a company's own float actually changes hands.

## Rationale
1-month (21-trading-day) trailing average turnover, this program's own established cross-sectional formation window (same as RS, momentum, reversal, MAX effect) rather than a paper-specific window independently re-verified beyond the paper's existence. 1-month holding, single-vintage, same structural adaptation as every prior cross-sectional strategy.

LONG ONLY (approved, disclosed, same reason as every prior strategy). SHARES OUTSTANDING IS A CURRENT SNAPSHOT (data/fetch_shares_outstanding.py), not a historical series -- applied across the whole backtest. This is the central disclosed approximation of this strategy: mild for a large, stable Nifty 500 constituent, more material for anything with a big past split, bonus issue, buyback or follow-on dilution between the backtest's start date and today. SINGLE-VINTAGE HOLDING instead of any overlapping-portfolio construction. EXIT RULE is ONLY the 21-trading-day time-stop or the synthetic protective stop -- no percentile-based early exit, same discipline as every prior strategy. PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source paper is a factor-return study with no position-level risk management whatsoever.

## Rules
Turnover, each formation date: trailing average of (daily Volume / shares outstanding). Cross-sectional decile sort by turnover at each formation date. Long the BOTTOM decile (lowest turnover, most illiquid) -- the paper's documented finding is an inverse relationship between turnover and subsequent return.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
