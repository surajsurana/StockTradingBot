# Crypto Volatility-Managed Exposure

## Mechanism
Volatility is highly persistent from one month to the next while expected returns are not, so the risk-return trade-off is worst right after a volatile month. Scaling exposure by the inverse of last month's realized variance -- less after volatile months, more after calm ones -- raised the Sharpe ratio of the US market and most factor portfolios in MM's 1926-2015 sample, with the gain coming mainly from sidestepping the worst drawdowns. Crypto volatility clusters even more strongly.

## Rationale
Cash-constrained, no leverage: weight = min(1, (60% / sigma)^2), sigma = trailing 30-day realized volatility annualised; five majors in 20% sleeves each scaled by its own weight; month-end rebalance implemented as exit-and-re-enter at the new weight, skipped when the weight moved by 10% or less; weight computed on full history as warm-up.

No leverage (MM's weights exceed 1 in calm months); 60% target instead of MM's variance-matching constant, a disclosed choice with a one-directional effect on scale but not on the month ranking. 20% protective stop, not in the source. Monthly exit/re-entry books a taxable gain in every profitable month -- the paper's own turnover, at the lane's limit. Costs and India's 31.2% no-offset tax before the audit; pre-tax recorded.

## Rules
Weight on the asset each month = c / (previous month's realized daily-return variance), c a scale constant set so the managed and unmanaged series have equal long-run volatility; rebalance monthly; long only by construction. Market portfolio 1926-2015: alpha ~4.9% per year, Sharpe up from 0.41 to 0.55.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
