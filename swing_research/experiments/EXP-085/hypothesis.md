# Crypto Time-Series Momentum (12-month)

## Mechanism
An asset's own past 12-month return predicts its next month: initial under-reaction to news and delayed over-reaction by trend chasers keep a move going. MOP find it in 58 futures across equities, bonds, currencies and commodities 1965-2009, with the strongest results in extreme markets; Liu & Tsyvinski find Bitcoin's own past returns predict its future returns.

## Rationale
12-month lookback read on the last UTC calendar day of each month, five majors (BTC, ETH, BNB, XRP, SOL) in equal 20% sleeves, signal computed on full history and supplied to every walk-forward window as warm-up. Same coins, same sleeves and same stop as the Faber rule in Pool F, so the two are directly comparable.

LONG ONLY (negative signal = cash). NO volatility scaling: this engine sizes from the stop distance, so MOP's 40%/sigma scaling is replaced by equal sleeves. 20% protective stop, not in the source. Costs and India's 31.2% no-offset tax applied before the audit; pre-tax recorded.

## Rules
Each month-end, for every asset: long if the trailing 12-month excess return is positive, short if negative; scale each position to 40% annualised ex-ante volatility; hold one month; rebalance monthly. Diversified across assets the strategy earned a Sharpe ratio above 1 in MOP's sample.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
