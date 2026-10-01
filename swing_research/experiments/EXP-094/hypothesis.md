# Minervini Trend Template Filter (US)

## Mechanism
Trend + relative-strength screening as a proxy for institutional accumulation already underway: a stock satisfying all 8 Trend Template criteria simultaneously (price/moving-average alignment, proximity to 52-week high, distance from 52-week low, top-30% relative strength vs. the universe) is in a confirmed, broad-based uptrend rather than a speculative or lagging one.

## Rationale
Named 'Trend Template Filter', deliberately NOT 'SEPA' or 'VCP breakout' (per explicit approval 2026-08-03) -- this tests the 8-criterion screen exactly as documented, with a disclosed mechanical entry trigger standing in for Minervini's real VCP base/pivot selection, which has no publicly documented canonical numeric form. See assumptions_impact below and swing_research/strategy_library/ for the full documented-rules-vs-assumptions breakdown.

Same 8-criterion screen, same entry-trigger/exit-rule/RS-percentile-substitute adaptations as the India version (see MINERVINI_TREND_TEMPLATE_FILTER above for the full reasoning) -- unchanged, since none of that is India-specific. No pyramiding, for the same reason as before (undocumented trigger/sizing). Long-only: this codebase has no short-selling infrastructure for ANY market yet (not an NSE-specific gap the way it was described in the India version -- the constraint is this codebase's own execution layer, not the exchange).

## Rules
8 criteria, ALL must pass: (1) price above both 150-day and 200-day MA; (2) 150-day MA above 200-day MA; (3) 200-day MA trending up >=1 month; (4) 50-day MA above both 150-day and 200-day MA; (5) price above 50-day MA; (6) price >=30% above 52-week low; (7) price within 25% of 52-week high; (8) Relative Strength ranking >=70th percentile vs. the universe. Stop-loss: 7-8% max from entry. Position sizing: 1.25-2.5% of equity at risk per trade. Pyramids into confirmed winners (trigger undocumented).

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
