# Strategy Library: 52-Week High Momentum

**Status: Research complete. PASS — first strategy to pass both the base run and the mandatory recent-period check with no conflicting evidence.**

## Original Publication

George, T.J. and Hwang, C-Y. (2004), "The 52-Week High and Momentum Investing," *The Journal of Finance*, Vol. 59, No. 5. Central finding: a stock's nearness to its own 52-week high is a BETTER predictor of future returns than standard past-return (Jegadeesh-Titman) momentum.

## Summary

Cross-sectional decile sort on nearness-to-52-week-high; long the top decile. Single-vintage position per symbol (not the paper's overlapping-portfolio construction), K=6-month (126-trading-day) holding period, entered on the state-transition day a symbol first enters the top decile.

## Documented Rules vs. Implementation Assumptions

**Documented:** nearness ratio = Price / 52-week-high; cross-sectional decile sort each formation date; long top decile / short bottom decile; K-month holding period (K=3,6,9,12 tested, K=6 most cited); Jegadeesh-Titman overlapping-portfolio construction (new K-month position every month, K simultaneous vintages).

**Disclosed implementation assumptions (approved 2026-08-04, full table in the approved implementation plan):**
- **Long only**: DIRECTIONALLY UNKNOWN impact (same reasoning as Turtle).
- **Single-vintage holding, not overlapping portfolios**: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN — the single largest structural deviation from the source methodology in this experiment. A single-vintage system realizes one entry point's path per episode, with materially higher variance than the paper's smoothed multi-vintage average.
- **Exit rule is ONLY the 126-trading-day time-stop** (checked via an equivalent calendar-day threshold, since the engine's `exit_signal_at()` hook doesn't expose a shared trading-day bar index between entry and the current row) **or the synthetic protective stop — deliberately no percentile-based early exit.** An earlier draft of the implementation plan included one; it was explicitly removed before implementation began, since it would have been an invented rule with no basis in the source paper. Reserved as a possible future, separately-labeled research variant.
- **Protective stop-loss (8%) and position sizing (1% risk/unit): not part of the original methodology at all.** The source paper is a factor-return study with zero position-level risk management; both were required to run this through the position-based backtesting engine at all — the most significant "not really in the source material" addition of any strategy in this program so far.

## Implementation Status

**Complete.** Zero framework changes, per the platform freeze (2026-08-04, "Swing Research Platform v1 complete"). Reused `swing_research/cross_sectional.py`'s existing vectorized `.rank(pct=True, axis=1)` pattern via a new `compute_52w_high_nearness_percentile_ranks()` function — a strategy-specific signal, not a framework primitive, same category as Minervini's RS percentile. `acceptance_criteria.py`, `evidence_quality.py`, `cross_strategy_review.py`, and `backtesting_engine.py` used exactly as built, unmodified.

## Validation Status

Full pipeline run: 2-year pipeline validation (EXP-012), 10-year production run (EXP-013), mandatory recent-period-only check (EXP-014). No robustness sub-period pass was run — reserved by the approved plan for close/ambiguous or REJECT results, and this result was neither.

## NSE Results

**2-year validation** (EXP-012): PASS. 40 walk-forward trades, 100% window consistency, OOS expectancy +₹108.02/trade. Evidence quality MODERATE (55.7/100).

**Base run** (full 457-symbol frozen universe, 2016-08-04 to 2026-08-04, EXP-013): **PASS**. 311 walk-forward trades, 100% of windows positive, out-of-sample holdout (103 trades) expectancy +₹1,576.18/trade. Continuous full-period metrics: 306 trades, CAGR 22.8%, Sharpe 1.058, Max Drawdown 30.8%. **Evidence quality: HIGH (90.0/100)** — the highest of any strategy evaluated in this program so far (Turtle's base run and Minervini's base run were both materially thinner on at least one dimension). Outperformed both live production strategies (MA Crossover 5.08% CAGR, Mean Reversion 8.41% CAGR) on both CAGR and Sharpe; underperformed Buy & Hold (26.27% CAGR) slightly on CAGR but with materially lower drawdown (30.8% vs. 52.53%).

**Recent-period-only check** (most recent 3 years, EXP-014, window count auto-reduced from the requested 3 to 2 by the frozen strategy-aware windowing logic, exactly as designed for Minervini): **PASS**. 74 total trades, **100%** of windows positive (unlike Minervini's single-window coin-flip, this result rests on 2 genuinely agreeing windows), out-of-sample holdout (55 trades) expectancy +₹140.87/trade, continuous CAGR 10.81%, Sharpe 0.711, MaxDD 14.45%. Evidence quality MODERATE (62.6/100).

**No conflicting evidence between the official recent-period check and any independent robustness study** — unlike Minervini, there was no disagreement to resolve, so `determine_acceptance_verdict("PASS", "PASS")` returns a clean **PASS**, with no `INCONCLUSIVE` involved.

## Final Verdict

**PASS.** Per the standing acceptance criteria (`swing_research/acceptance_criteria.py`, unmodified): both the full-history run and the dedicated recent-period-only run passed, with no conflicting robustness evidence. This is the first strategy in the program's history to clear this bar cleanly. (Whether and when to proceed to paper trading remains a separate decision for the user — this research conclusion documents the statistical/methodological result only, not a trading recommendation.)

## Lessons Learned

1. **A published factor-investing study with zero position-level risk management still needs disclosed, non-original risk rules to be backtestable at all** — the protective stop-loss and position sizing here are entirely absent from the source paper, which is the most significant "not really in the source material" addition of any strategy tested so far. This should be weighed when interpreting how much of the result reflects the published effect vs. this program's own risk overlay.
2. **Single-vintage implementation of an academic overlapping-portfolio strategy is a real, disclosed source of variance**, not a minor technicality — the paper's own multi-vintage averaging smooths out exactly the kind of single-entry-point luck a single-vintage backtest is exposed to. A strong single-vintage result is still meaningful evidence, but should not be read as a precise estimate of the smoothed academic factor's own risk-adjusted return.
3. **A recent-period check resting on 2 windows can still be strong evidence when both windows agree** — this is the same 2-window structure Minervini's recent-period check had (both driven by the same strategy-aware windowing fix), but here 100% of windows were positive rather than a single-window coin-flip, illustrating that the earlier framework fix for window starvation, once applied, can support either a strong or a fragile verdict depending on the actual data — the fix's job was only to make the check possible at all, not to bias its outcome.
4. **Refusing to invent an exit rule not in the source material, even when the resulting system feels underspecified (pure time-stop, no interim risk management beyond the protective stop), kept this a faithful test of the published effect** rather than a hybrid — directly following the same discipline established during Minervini's planning (removing the percentile-based early exit before implementation began, per explicit direction).
