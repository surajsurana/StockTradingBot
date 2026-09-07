## Why it failed

The VWAP Extension Exhaustion Fade rests on the assumption that price stretched away from VWAP will mean-revert intraday. The 0/3 walk-forward pass rate confirms this isn't a stable, repeatable edge — the negative expectancy (-116.78/trade) and sub-1 profit factor (0.671) show that when extensions occurred, continuation (trend/momentum) beat reversion far more often than not, consistent with the strategy essentially fading a broad market drift rather than catching genuine exhaustion.

The damage is concentrated, not uniform. Information Technology (-6,006.80) and Healthcare (-2,171.45) alone account for over 70% of total losses, suggesting these sectors trend too persistently intraday for exhaustion fades to work — likely driven by global/macro flow (IT-USD sensitivity, FII positioning) rather than local liquidity-driven overextension. The 13:00-14:00 window (-4,303 and -4,417 respectively) is the single biggest time drag, pointing to post-lunch momentum continuation rather than reversal — exactly when the strategy is fading. Both bullish (-6,557) and bearish (-4,886) regimes lost money, meaning this isn't a directional-bias problem; the mechanism itself misreads trend continuation as exhaustion regardless of market tone.

**Follow-up ideas:**
1. Test the same fade logic but exclude IT/Healthcare and restrict entries to 9:00-12:00 and 15:00, where losses were smallest/positive, to see if a narrower regime has real edge.
2. Add a momentum/ADX filter to distinguish genuine exhaustion from trend continuation before fading.