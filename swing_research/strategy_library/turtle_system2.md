# Strategy Library: Turtle Trading — System 2 (Donchian Channel Breakout)

**Status: Research complete. REJECTED for paper trading (temporal robustness failure).**

## Original Publication

Richard Dennis / William Eckhardt's 1983–84 trading program, documented by Curtis Faith in *Way of the Turtle* (2007) and the publicly-archived original Turtle Rules. Highest rule-fidelity source identified across the entire published-swing-research candidate report (see the original candidate report artifact) — real, audited trading history behind it, not just a book's claim.

## Summary

Pure trend-following: 55-day price breakout entry, 20-day opposite-breakout exit, 2N (Wilder-smoothed True Range) stop-loss, volatility-and-equity-normalized unit sizing, pyramiding up to 4 units per symbol with correlation-group portfolio caps. Long-only for this NSE Cash implementation (approved scope reduction — see Implementation Status).

## Trading Rules (as faithfully implemented — System 2, long side)

- **N**: 20-day Wilder-smoothed True Range (`N_today = (19×N_yesterday + TR_today)/20`, seeded by a simple 20-day average).
- **Entry**: Close breaks above the highest High of the prior 55 days.
- **Exit (signal)**: Close breaks below the lowest Low of the prior 20 days.
- **Stop-loss**: 2N below the most recent unit's entry; whole-position stop rises with each pyramid unit, never lowered.
- **Position sizing**: 1 Unit = `floor(equity × 1% / N)` shares (dollar-value-per-point = 1 for cash equities, vs. a futures contract multiplier in the original).
- **Pyramiding**: +1 unit per +0.5N favorable move from the last unit, max 4 units/symbol.
- **Portfolio limits**: max 4 units/symbol, 6 units per correlated group (NSE sector used as a proxy for the original's futures asset-class grouping — a disclosed adaptation, not the exact original grouping), 10 units total portfolio-wide (long-only collapses the original's separate 10/12 caps into one).

## Implementation Status

**Complete.** System 2 chosen over System 1 (20-day/whipsaw-filtered variant) as the cleaner, more purely mechanical first test — System 1 not yet built. **Long-only** — NSE cash equities lack the SLB infrastructure for a genuine multi-week short; this is a disclosed, approved reduction in scope from the original long/short-symmetric system, not a silent adaptation.

Built entirely in `swing_research/` (new package, sibling to the intraday `research_lab/`), reusing `research_lab`'s Statistical Auditor, Performance Analyst, Experiment Manager, and Knowledge Base by import (unmodified). Production swing strategies (`strategies/ma_crossover.py`, `strategies/mean_reversion.py`) and all protected paths remain completely untouched throughout.

## Validation Status

Full pipeline run: walk-forward validation, out-of-sample holdout, Statistical Auditor, Performance Analyst narrative, robustness analysis (sub-period, universe-subset, walk-forward-window-count sensitivity). A Research Audit (2026-08-03) additionally confirmed and fixed two real implementation issues before final validation: a capital-allocation asymmetry that made the benchmark comparison unfair (production strategies were implicitly given unlimited capital vs. Turtle's real constrained pool), and a sector-mapping bug that misattributed 100% of P&L to "Unknown." Both fixed; see `swing_research/backtesting_engine.py`'s `simulate_portfolio_single_unit()` and `research_director.py`'s `_sector_map_for_trades()`.

## NSE Results

**Base run** (full 457-symbol frozen universe, 2016-08-03 to 2026-08-03, EXP-002): PASS. 244 total trades, 100% walk-forward consistency, out-of-sample expectancy +₹647.78/trade. CAGR 28.87%, Sharpe 0.73, Sortino 1.97, Max Drawdown 31.0%, Recovery Factor 2.97, average holding period 51.7 days, exposure 4.2%. Outperformed MA Crossover (5.04% CAGR), Mean Reversion (8.25% CAGR), Buy & Hold (26.21% CAGR), and the Nifty 500 index (11.96% CAGR) on CAGR; underperformed Buy & Hold and the index on Sharpe (classic trend-following payoff shape — low win rate, 38%, offset by a 2.13 profit factor from rare large winners).

**Robustness analysis** (EXP-003 through EXP-006):

| Variant | Verdict | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| First half of window (2016–2021) | PASS | 35.47% | 0.79 | 24.3% |
| **Second half of window (2021–2026)** | **REJECT** | 9.68% | 0.40 | 42.0% |
| 150-symbol universe subset, full period | PASS | 34.63% | 0.76 | 32.1% |
| 5 walk-forward windows (vs. base run's 3) | PASS | 28.94% | 0.73 | 31.0% |

Universe composition and walk-forward window count are not fragile — results hold up under both changes. **Time period is fragile**: tested in isolation, the most recent ~5 years (2021–2026) produced a REJECT verdict, with out-of-sample expectancy of **-₹113.67/trade**. The full-window PASS is substantially carried by the earlier (2016–2021) era.

## Final Verdict

**REJECTED for paper trading.** Per the Swing Research Program's standing acceptance criteria (`swing_research/acceptance_criteria.py`, established 2026-08-03 directly because of this finding): no strategy is eligible for paper trading unless it demonstrates positive out-of-sample performance **and** remains robust in the most recent market period specifically. Turtle System 2 satisfies the first condition but fails the second. This is not a rejection of the implementation or the audit — the platform worked exactly as intended, faithfully implementing and rigorously stress-testing a world-renowned methodology and catching a real degradation the base run alone would have missed.

No parameter tuning or optimization was attempted or will be attempted on this result, per the research program's mandate.

## Lessons Learned

1. **A long backtest's aggregate PASS can hide a strategy that has already stopped working.** Turtle's full 10-year result looked strong specifically because 2016–2021 was strong; 2021–2026 alone was a clear REJECT. This is now a standing, mandatory check (`acceptance_criteria.py`'s `run_recent_period_check()`) for every future strategy in this program, not a one-off analysis.
2. **A capital-allocation asymmetry can silently make a benchmark comparison meaningless** — giving unconstrained strategies "unlimited" capital relative to a properly-constrained one produces denominator artifacts (near-zero CAGR) that look like weak signals but are really a modeling bug. Always verify capital/portfolio assumptions are comparable before trusting a cross-strategy comparison.
3. **Symbol-format mismatches between modules are an easy, silent failure mode** — `Trade.symbol` (`.NS`-suffixed) vs. `research_lab`'s sector map (bare symbol) produced a 100%-"Unknown" sector breakdown that could easily have been read as "no real sector concentration" rather than "broken lookup."
4. **An LLM narrative can confidently state something false if the prompt doesn't carry enough context** — the auto-generated narrative assumed the textbook (long/short) version of Turtle from its own training knowledge, since `research_lab.performance_analyst.explain()`'s prompt only carries a hypothesis *name*, not its full rules/scope-reduction text. Fixed via prompt-injection, but worth remembering for every future strategy's narrative: verify it against what was actually implemented, don't take it as given.
5. **The domain-transfer question (futures → NSE single-asset-class equities) is real** — Turtle's original edge came partly from diversification across ~20+ historically low-correlated futures markets; a single-asset-class NSE implementation loses that diversification benefit, which may itself contribute to the weaker recent-period performance (worth investigating further if this strategy is ever revisited).
