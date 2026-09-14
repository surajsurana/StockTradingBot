# Downside Beta

## Mechanism
Investors who are averse to losses in bad states (disappointment aversion) demand a premium for stocks whose co-movement with the market is strongest when the market falls. Downside beta -- the market beta estimated only on days the market's return is below its mean -- captures that exposure; the highest-downside-beta stocks earn roughly 6% per year more than the lowest, and the premium survives controls for regular beta, size, book-to-market, momentum, coskewness and liquidity. A risk premium, not a mispricing, so it is not expected to be arbitraged away.

## Rationale
The paper's own one-year daily-return estimator with the downside condition and the mean taken within each rolling 252-day window; Nifty 50 as the market; top quintile = cross-sectional percentile >= 80; state-transition entry (the first day a stock enters the top quintile), 21-trading-day single-vintage hold -- this program's standard translation of monthly portfolio formation (max_effect, high_volume_return_premium).

LONG ONLY, the high-downside-beta quintile; the paper's spread also shorts the low quintile. At most 10 positions ranked by percentile, 8% protective stop and 1% risk-per-unit sizing, none of which are in the source. Daily returns not converted to excess returns. One year of extra history fetched as warm-up; the walk-forward judgement starts a year after the data start so no window is blind. The roadmap profile's direction text (long LOW downside beta) contradicted the source and was corrected before implementation.

## Rules
Each month: estimate downside beta from the past 12 months of daily returns, cov(r_i, r_m | r_m < mu_m) / var(r_m | r_m < mu_m); sort stocks into quintiles; hold the portfolios one month, equal-weighted; re-form monthly. US 1963-2001: top-minus-bottom downside-beta quintile spread about 6% per year, robust across sub-periods.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
