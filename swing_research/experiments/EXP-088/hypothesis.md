# Realized Low Volatility (Nifty100 Low Volatility 30 methodology)

## Mechanism
Low-volatility stocks earn returns comparable to or above high-volatility stocks with far less risk: benchmark-constrained institutions overpay for volatile names and cannot lever the calm ones, and lottery-seeking retail demand crowds into high-volatility stocks. NSE's index applies it mechanically: the 30 least volatile Nifty 100 stocks, inverse-volatility weighted.

## Rationale
Bottom decile of this program's Nifty 500 universe by 1-year realized volatility (percentile <= 10), ranked lowest-first for the 10 slots; entry on the first day a stock enters the decile; hold 126 trading days (one reconstitution period); percentile computed on full history with a one-year warm-up so no walk-forward window starts blind.

Nifty 500 in place of Nifty 100 (no point-in-time Nifty 100 membership held). No inverse-volatility weighting (equal risk sizing). At most 10 positions, 8% stop, 1% risk-per-unit -- program conventions, not in the methodology. Quarterly weight rebalance not reproduced.

## Rules
From the Nifty 100, volatility = standard deviation of daily log returns over one year; select the 30 lowest; weight by inverse volatility; reconstitute semi-annually (weights rebalanced quarterly). Live since 2016; the index's own factsheets show lower drawdown than the Nifty 100 with comparable long-run return.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
