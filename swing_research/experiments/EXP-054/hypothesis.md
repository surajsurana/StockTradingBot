# 52-Week High Momentum

## Mechanism
A stock's nearness to its own 52-week high, cross-sectionally ranked against the universe, is a better predictor of future returns than standard past-return (Jegadeesh-Titman) momentum -- stocks near their 52-week high continue to outperform, stocks far from it continue to underperform, an anchoring/reference-point effect distinct from pure trend-following.

## Rationale
K=6 months (the most commonly cited/replicated specification), top decile = nearness percentile >= 90 (a direct restatement of 'top decile' in this program's existing 0-100 percentile convention, already used by Minervini's RS percentile). Single-vintage holding, not the paper's overlapping-portfolio construction -- see scope_reductions and assumptions_impact below for why, and the magnitude of the resulting deviation.

LONG ONLY (approved, disclosed, same reason as Turtle -- no NSE SLB infrastructure for a genuine multi-month short). SINGLE-VINTAGE HOLDING instead of the paper's overlapping-portfolio construction (a new K-month position every month, K simultaneous vintages) -- the single largest structural deviation from the source methodology in this experiment, approved 2026-08-04. EXIT RULE is ONLY the 126-trading-day time-stop (the direct single-vintage analogue of the K=6-month holding period) or the synthetic protective stop -- deliberately NO percentile-based early exit, which was in an earlier draft of the implementation plan and was explicitly removed before implementation began, since it would have been an invented rule with no basis in the source paper. A percentile-based-exit variant is reserved for a future, separately labeled research iteration, never folded into this baseline. PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source paper is a factor-return study with no position-level risk management whatsoever; both are required to run this through this program's position-based backtesting engine at all.

## Rules
Nearness ratio, each formation date: ratio = Price / 52-week-high price. Cross-sectional decile sort by nearness ratio at each formation date. Long the top decile (nearest to the 52-week high); the paper's factor construction shorts the bottom decile. Holding period K, tested at K=3,6,9,12 months in the paper; K=6 months is the most commonly cited/replicated specification. Standard Jegadeesh-Titman overlapping-portfolio construction: a new K-month position initiated every month, K simultaneous 1/K-weighted vintages held at any time, realized return = equal-weighted average across active vintages.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
