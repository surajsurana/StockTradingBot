# Cross-Sectional Momentum (US)

## Mechanism
Stocks with the highest returns over a J-month formation period continue to outperform over a subsequent K-month holding period -- the foundational cross-sectional momentum anomaly, the origin of the 'momentum' factor itself, and the paper 52-Week High Momentum's own source (George & Hwang 2004) explicitly built on and partly subsumed.

## Rationale
J=6 months formation, K=6 months holding -- the paper's own most-cited specification. Single-vintage holding, not the paper's overlapping-portfolio construction -- identical structural adaptation to 52-Week High Momentum, approved 2026-08-04 for the identical underlying reason (see scope_reductions below).

Same J=6/K=6, single-vintage, no-skip-period adaptations as the India version (see CROSS_SECTIONAL_MOMENTUM above for the full reasoning) -- unchanged, none of it is India-specific. Long-only: this codebase has no short-selling infrastructure for ANY market yet (not an NSE-specific gap -- the constraint is this codebase's own execution layer, not the exchange).

## Rules
Formation-period return: cumulative return over the prior J months (J=3,6,9,12 tested). Cross-sectional decile sort by formation-period return at each formation date. Long the top decile (past winners); the paper's zero-cost portfolio shorts the bottom decile (past losers). Holding period K months (K=3,6,9,12 tested); J=6,K=6 is the specification the paper highlights as generating the strongest, most-cited result. Standard overlapping-portfolio construction: a new K-month portfolio formed every month, K simultaneous vintages held at once. The paper notes a 1-week skip between formation and holding as a refinement that avoids some short-term bid-ask/reversal contamination -- not part of the headline J=6/K=6 result.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
