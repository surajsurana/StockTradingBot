# Why This Was Rejected

The verdict hinges on sample adequacy, not on whether the strategy "looks good" — and the numbers confirm the auditor's caution is warranted. With only 25 total trades, every headline metric (56% win rate, 1.63 profit factor, 1.26 Sharpe) is built on a sample too small to distinguish genuine edge from noise. A pair-reversion mechanism should, by construction, generate frequent signals as spreads oscillate around a mean; 25 trades over the observed window (Feb–Aug 2026) suggests either an overly restrictive entry filter or a genuinely rare divergence condition — either way, the strategy hasn't been tested enough to trust.

The out-of-sample collapse is the critical tell: 1 holdout trade at -142.80 expectancy versus a positive in-sample/overall expectancy of +110.13 is not evidence of failure, but it's *zero evidence of success* either — you cannot validate mean-reversion behavior from a single observation. This is exactly the kind of gap that in-sample overfitting to specific spread dynamics (e.g., a few sector pairs whose historical correlation happened to hold) would produce.

The timing/regime breakdowns (bearish regime and 9-10am entries driving most P&L) are suggestive of a real mechanism — pairs likely revert faster amid morning volatility and in risk-off tape — but with this few trades, these could just as easily be artifacts of 2-3 lucky trades clustering in those buckets.

**Follow-up ideas:** (1) Widen the sector-pair universe or loosen the entry z-score threshold to generate 3-5x more signals before re-testing, explicitly tracking whether the 9-10am/bearish-regime edge persists at scale. (2) Run a proper walk-forward with rolling holdouts of ≥10 trades each, rather than a single terminal holdout, to get more than one OOS data point before judging expectancy sign.

## Engineer's addendum (code-verified, 2026-09-07)

The narrative's guess of "an overly restrictive entry filter" is confirmed and pinned down: it is the hypothesis's own pair-eligibility rule, "only trade pairs with rolling correlation > 0.8 over trailing 60 days". Measured on the same yfinance daily data the backtest used (trailing-60-day daily-close-return correlation, last 240 trading days):

| Pair | Days with corr > 0.8 | median corr |
|---|---|---|
| HDFCBANK/ICICIBANK | 0 / 240 (0%) | 0.49 |
| TCS/INFY | 117 / 240 (49%) | 0.80 |
| SUNPHARMA/CIPLA | 0 / 240 (0%) | 0.38 |
| ULTRACEMCO/AMBUJACEM | 21 / 240 (9%) | 0.63 |
| BAJFINANCE/SHRIRAMFIN | 31 / 240 (13%) | 0.55 |

Even the most "obviously similar" NSE large-cap pairs have 60-day daily-return correlations of ~0.4-0.6; only TCS/INFY clears 0.8, and only about half the time. So the 25 trades are essentially one pair's worth of signal, and the sample starvation is a property of the rule as stated -- not of the z-score trigger, and not something to tune away (that would be a different hypothesis). Same failure class the cross-experiment review already flagged: a well-meant filter starving the sample before the core mechanism gets a fair out-of-sample test.