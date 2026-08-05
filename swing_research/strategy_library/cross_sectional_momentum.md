# Strategy Library: Cross-Sectional Momentum (SW-006)

**Status: Research complete. Research Verdict PASS. Deployment Status: RESEARCH / HOLD — not registered for paper trading, per explicit decision 2026-08-04.**

## Original Publication

Jegadeesh, N. and Titman, S. (1993), "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency," *The Journal of Finance*, Vol. 48, No. 1. The foundational cross-sectional momentum paper — the origin of the "momentum" anomaly itself, and the paper 52-Week High Momentum's own source (George & Hwang 2004) explicitly built on and partly subsumed.

## Documented Rules vs. Implementation Assumptions

**Documented:** formation-period return over J months (J=3,6,9,12 tested); cross-sectional decile sort at each formation date; long top decile / short bottom decile; K-month holding (K=3,6,9,12 tested, J=6/K=6 the paper's most-cited specification); Jegadeesh-Titman overlapping-portfolio construction; a 1-week skip period noted as a refinement, not the headline result.

**Disclosed implementation assumptions (approved 2026-08-04, deliberately mirroring 52-Week High Momentum's own approved precedent):**
- **Long only**: DIRECTIONALLY UNKNOWN impact (same reasoning as every prior strategy).
- **Single-vintage holding, not overlapping portfolios**: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN — identical structural deviation to 52-Week High Momentum's own, for the identical reason.
- **No skip period between formation and holding**: MINOR-to-MODERATE, DIRECTIONALLY UNKNOWN — the paper's own headline J=6/K=6 result doesn't require one; adding one would be an invented refinement.
- **Exit rule is ONLY the 126-trading-day time-stop or the synthetic protective stop — deliberately no percentile-based early exit.**
- **Protective stop-loss (8%) and position sizing (1% risk/unit): not part of the original methodology at all** — the source paper has zero position-level risk management.

## Implementation Status

**Complete.** Zero framework changes, per the platform freeze. Reused `swing_research/cross_sectional.py`'s existing vectorized `.rank(pct=True, axis=1)` pattern via a new `compute_momentum_percentile_ranks()` — a single J=6-month formation return, distinct from Minervini's own multi-horizon blended RS-Rating substitute (a different signal for a different strategy). `acceptance_criteria.py`, `evidence_quality.py`, `cross_strategy_review.py`, `deployment/` (paper trading, certification, drift reporting) all used exactly as built, unmodified.

## Validation Status

Full pipeline: 2-year validation (EXP-015), 10-year production run (EXP-016), mandatory recent-period-only check (EXP-017) — both PASSed, both with 100% walk-forward window consistency, no conflicting evidence between them (no INCONCLUSIVE needed). Additionally, per explicit direction, one **supplementary post-COVID (2020-01-01 onward) robustness run (EXP-018)** was performed — see below.

## NSE Results

**Base run** (EXP-016, full 457-symbol universe, 2016-2026): **PASS**. 338 walk-forward trades, 100% window consistency, OOS expectancy +₹2,008.58/trade. Continuous: 348 trades, CAGR 20.67%, Sharpe 1.046, MaxDD 32.79%. Evidence quality HIGH (90.0/100).

**Recent-period check** (EXP-017, most recent 3 years): **PASS**. 110 trades, 100% window consistency (all 3 requested windows used — the lighter 126-day lookback never triggered the strategy-aware windowing reduction Minervini needed). OOS expectancy +₹736.26/trade. Continuous: 108 trades, CAGR 5.38%, Sharpe 0.329, MaxDD 21.93%. Evidence quality HIGH (78.8/100).

**Official acceptance verdict: PASS** (`determine_acceptance_verdict("PASS", "PASS")`, unmodified frozen logic) — base and recent-period agree, no conflict to resolve.

**Supplementary post-COVID robustness run** (EXP-018, 2020-01-01 to 2026-08-04, additional evidence only, per explicit direction — does NOT feed into or alter the official verdict above): **REJECT**. Out-of-sample expectancy -₹54.02/trade on 224 trades, evidence quality HIGH (90.0/100, a rich sample). This is a genuine, evidence-quality-strong finding that the strategy's edge did NOT hold up when tested specifically over the post-COVID structural regime — worth weighing heavily in any deployment decision even though, per the approved governance for this run, it does not change the recorded Research Verdict.

## Direct Comparison vs. 52-Week High Momentum

| Metric | Cross-Sectional Momentum (base, EXP-016) | 52-Week High Momentum (base, EXP-013) | CSM (recent, EXP-017) | 52WH (recent, EXP-014) |
|---|---|---|---|---|
| CAGR | 20.67% | 22.8% | 5.38% | 10.81% |
| Sharpe | 1.046 | 1.058 | 0.329 | 0.711 |
| Sortino | 1.794 | 1.655 | n/a | n/a |
| Max Drawdown | 32.79% | 30.8% | 21.93% | 14.45% |
| Profit Factor | 1.704 | 2.301 | 1.275 | 1.933 |
| Win Rate | 28.7% | 36.0% | 26.9% | 33.9% |
| Expectancy/trade | ₹1,593.98 | ₹2,220.41 | ₹157.09 | ₹553.19 |
| Avg Holding Period | 79.3 days | 89.1 days | 72.0 days | 95.7 days |
| Trade Count (base) | 348 | 306 | 108 | 65 |
| Recent-Period Result | PASS | PASS | — | — |
| Evidence Quality (base) | HIGH (90.0) | HIGH (90.0) | HIGH (78.8) | MODERATE (62.6) |
| Acceptance Verdict | **PASS** | **PASS** | — | — |

**Diversification assessment.** On every single metric compared, Cross-Sectional Momentum is *weaker* than 52-Week High Momentum — lower CAGR, lower Sharpe, higher drawdown, lower profit factor, lower win rate, lower expectancy, both at the base and recent-period horizon. This alone would already caution against treating it as a strong standalone addition. More importantly, the **sector breakdown reveals genuine behavioral divergence, not just weaker performance of the same underlying trade**: 52-Week High Momentum's single largest profit contributor is Financial Services (+₹252,890) — for Cross-Sectional Momentum, Financial Services is *slightly negative* (-₹390). Conversely, Realty is 52-Week High Momentum's 4th-largest winning sector (+₹60,408) but Cross-Sectional Momentum's single *worst* sector by a wide margin (-₹28,051). Healthcare, Construction, and Information Technology are meaningfully profitable for Cross-Sectional Momentum but comparatively minor or negative for 52-Week High Momentum. This is a real, disclosed difference in *which* stocks each strategy actually captures, not a duplicate signal wearing different parameters — the two strategies are drawing on genuinely different momentum sources (nearness-to-52-week-high vs. raw 6-month formation return), and the sector exposure evidence bears that out.

However, the **post-COVID REJECT (EXP-018)** is a material caveat that 52-Week High Momentum's own evaluation never produced — 52-Week High Momentum's official recent-period check and every robustness check run against it agreed cleanly (PASS). Cross-Sectional Momentum's official PASS rests on a base run and 3-year recent-period check that both happen not to isolate the post-COVID regime specifically, and when that specific, most-recent-relevant regime is isolated, the edge disappears (negative OOS expectancy on a rich, HIGH-evidence-quality 224-trade sample).

**Net assessment**: genuinely diversifying in exposure (sector, and by extension likely signal timing), but weaker on every raw performance metric AND carrying a real, evidence-quality-strong red flag (the post-COVID REJECT) that 52-Week High Momentum does not share. This is presented as a governance decision point, not a recommendation either way — see the Final Verdict section.

## Final Verdict

**Research Verdict: PASS** (official, per the frozen acceptance framework, unaffected by the supplementary post-COVID finding — Research Verdict and Deployment Status remain fully independent, per standing program governance).

**Deployment Status: RESEARCH / HOLD** — explicit human decision, 2026-08-04. Per the approved governance step, a clean PASS does not automatically trigger paper-trading registration. Reasoning for the HOLD: (1) the supplementary post-COVID robustness run (EXP-018, additional evidence only, does not alter the Research Verdict) produced REJECT on a high-quality 224-trade sample (evidence quality HIGH, 90.0/100) — negative out-of-sample expectancy specifically in the most recent structural regime; (2) the strategy underperformed the existing paper-trading strategy, 52-Week High Momentum (SW-003), on *every* primary performance metric at both the base and recent-period horizon. The diversification comparison above (genuine sector-exposure divergence from SW-003) is preserved as real evidence but is judged **insufficient on its own** to justify paper trading at this stage. May be reconsidered later if additional published strategies (e.g. PEAD) demonstrate complementary behavior that changes the overall portfolio decision.

## Lessons Learned

1. **A strategy can clear the official acceptance framework cleanly while a differently-scoped supplementary robustness check still finds a real problem** — the post-COVID REJECT here is not a contradiction of the official base/recent-period PASS (they test different, non-identical windows), but it is directly decision-relevant evidence a governance process should weigh, even when the acceptance framework itself is not designed to automatically incorporate it.
2. **Structurally similar strategies (same academic lineage, same single-vintage adaptation pattern) can still diversify meaningfully at the sector-exposure level** — raw performance-metric comparison alone would have understated how differently these two strategies actually behave; the sector breakdown was the more informative diversification signal here.
3. **Weaker-on-every-metric does not automatically mean "not worth deploying"** — the value of a second paper-traded strategy is partly about portfolio diversification, not only standalone strength; this is exactly why the new governance step asks for an explicit decision rather than a threshold-based auto-registration.
