# MAX Effect (Lottery-Demand Anomaly)

## Mechanism
Stocks with an extreme maximum single-day return within the recent month get bid up by investors with a preference for lottery-like payoffs (skewness-seeking demand), then underperform as that demand fades. Documented as surviving controls for size, book-to-market, momentum, and short-term reversal -- a genuinely distinct behavioral mechanism from every strategy already tested in this program, though highly correlated with idiosyncratic volatility (a separate, not-yet-implemented roadmap candidate).

## Rationale
MAX(1), single highest daily return in the trailing month -- the paper's own headline, most-cited specification (not MAX(5), the paper's own secondary robustness check). 1-month formation / 1-month holding. Single-vintage holding, not the paper's overlapping-portfolio construction -- identical structural adaptation to every prior cross-sectional strategy in this program.

LONG ONLY (approved, disclosed, same reason as every prior strategy -- no NSE SLB infrastructure for a genuine short). MAX(1) ONLY, not the paper's MAX(5) robustness variant -- MAX(1) is itself the paper's primary specification, not a weaker substitute. SINGLE-VINTAGE HOLDING instead of the paper's overlapping-portfolio construction -- mirrors every prior cross-sectional strategy's own approved deviation. EXIT RULE is ONLY the 21-trading-day time-stop or the synthetic protective stop -- deliberately no percentile-based early exit, same discipline established for every prior strategy. PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source paper is a factor-return study with no position-level risk management whatsoever.

## Rules
MAX, each formation date: the single HIGHEST daily return within the trailing ONE MONTH (the paper's primary MAX(1) specification; MAX(5), the average of the 5 highest days, is a disclosed robustness variant, not the headline result). Cross-sectional decile sort by MAX at each formation date. Long the BOTTOM decile (lowest MAX -- calmest stocks); the paper's zero-cost portfolio shorts the top decile (highest MAX -- most lottery-like stocks). Holding period: one month, standard monthly-rebalance construction.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
