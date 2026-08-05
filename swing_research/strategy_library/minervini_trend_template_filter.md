# Strategy Library: Minervini Trend Template Filter

**Status: Research complete. INCONCLUSIVE — not approved for paper trading, not permanently rejected.**

## Original Publication

Mark Minervini, *Trade Like a Stock Market Wizard* (2013) and *Think & Trade Like a Champion* (2016). Named "Trend Template Filter" — deliberately NOT "SEPA" or "VCP breakout" — because the entry trigger implemented here is a disclosed mechanical interpretation of the Template's qualification state, not Minervini's real Volatility Contraction Pattern base/pivot selection, which has no publicly documented canonical numeric form.

## Summary

An 8-criterion trend + relative-strength screen (price/moving-average alignment, proximity to 52-week high, distance from 52-week low, top-30% Relative Strength vs. the universe) used as a proxy for institutional accumulation already underway. Single-unit, no pyramiding this round.

## Documented Rules vs. Implementation Assumptions

**Documented (corroborated across independent sources):**
1. Price above both 150-day and 200-day MA.
2. 150-day MA above 200-day MA.
3. 200-day MA trending up ≥1 month.
4. 50-day MA above both 150-day and 200-day MA.
5. Price above 50-day MA.
6. Price ≥30% above 52-week low.
7. Price within 25% of 52-week high.
8. Relative Strength ≥70th percentile vs. the universe.
Stop-loss 7-8% range; position sizing 1.25-2.5% of equity at risk per trade.

**Our disclosed implementation assumptions, with estimated impact (see `swing_research/published_research_analyst.py`'s `MINERVINI_TREND_TEMPLATE_FILTER.assumptions_impact` for the full text):**
- **Entry trigger** (Template-qualification transition day, not a real VCP pivot breakout): LIKELY MATERIAL, probably UNDERSTATES real SEPA.
- **Exit rule** (close below 50-day MA, our own interpretation — least-documented part of the source material): MODERATE.
- **Stop-loss** (fixed 8%, top of documented range): MINOR.
- **No pyramiding** (undocumented trigger/sizing, not invented): MODERATE, ONE-DIRECTIONAL — can only understate real returns.
- **Relative Strength substitute** (open 3/6/9/12-month blend vs. IBD's proprietary formula): LIKELY MATERIAL, DIRECTIONALLY UNKNOWN — the single biggest fidelity gap.

## Implementation Status

**Complete.** Required one new framework capability beyond what Turtle needed: genuinely cross-sectional RS percentile ranking (`swing_research/cross_sectional.py`'s `compute_rs_percentile_ranks()`, a single vectorized `.rank(pct=True, axis=1)` call over the whole universe), injected into each symbol's precomputed frame via `simulate_portfolio()`'s new `extra_columns_by_symbol` parameter. Built in `swing_research/`, reusing `research_lab`'s Statistical Auditor, Performance Analyst, Experiment Manager, and Knowledge Base by import (unmodified). Production strategies and all protected paths remain untouched.

## Validation Status

Full pipeline run: 2-year pipeline validation, 10-year full-history production run, and the standing mandatory recent-period-only check (`swing_research/acceptance_criteria.py`). During the recent-period check, a real bug was found and fixed: naively splitting a 3-year recent-period slice into 3 walk-forward windows starved every window of tradeable days, since Minervini needs 252 bars of lookback (its longest requirement, the 52-week high/low window) before a first signal is even possible — leaving each ~365-day window with almost no room to trade, producing a false 0-trade REJECT. Fixed generically in `acceptance_criteria.py`: window count is now derived from each strategy's own declared `min_lookback_days` (a new `Strategy` base-class attribute, `swing_research/base.py`) and the recent-period slice's actual available trading days, capped at whatever was requested — not a hardcoded override for this one strategy, and applies identically to every future strategy in the roadmap.

## NSE Results

**Base run** (full 457-symbol frozen universe, 2016-08-03 to 2026-08-03, EXP-008): **PASS**. 574 walk-forward trades, out-of-sample expectancy +₹244.27/trade. Continuous full-period metrics: 657 trades, CAGR 24.7%, Sharpe 1.038, Max Drawdown 32.85%. Outperformed MA Crossover (5.04% CAGR, Sharpe 0.613) and Mean Reversion (8.34% CAGR, Sharpe 0.842) on both CAGR and Sharpe; slightly underperformed Buy & Hold (26.24% CAGR, Sharpe 1.239) on CAGR but with materially lower drawdown (32.85% vs. 52.41%).

**Recent-period-only check** (most recent 3 years, EXP-009, re-run after the windowing fix above): **REJECT**. 117 total trades (2 feasible windows, down from the requested 3, per the strategy-aware windowing fix), out-of-sample holdout (47 trades) had a strongly positive expectancy of +₹483.73/trade (CAGR 14.63%, Sharpe 0.791) — but the single non-holdout "consistency" window (2024-08 to 2025-02) had negative expectancy (-₹141.90/trade, CAGR -6.75%, Sharpe -1.396), so walk-forward consistency = 0% (0 of 1 windows positive), below the 50% threshold. This REJECT rests on judging consistency from a **single** window — a direct, unavoidable consequence of Minervini's heavy 252-day lookback leaving only 2 feasible windows in a 3-year slice.

**Independent robustness sub-period check** (second half of the 10-year window, 2021-2026, EXP-011, 3 walk-forward windows — materially overlapping the same recent years as the check above): **PASS**, strongly. 164 total trades, **100%** of windows positive, out-of-sample expectancy +₹646.28/trade, continuous CAGR 34.51%, Sharpe 1.308, MaxDD only 11.51% — better than the 2016-2021 first half (CAGR 18.13%, Sharpe 0.81, MaxDD 32.85%).

**These two results conflict.** Unlike Turtle — where the second-half robustness sub-period *corroborated* the recent-period REJECT (both genuinely pointed the same direction: real decay) — here a thin, single-window official check says REJECT while a richer, three-window independent check covering most of the same years says PASS, and PASSes more strongly than the earlier era.

## Final Verdict

**INCONCLUSIVE.** Per explicit direction, the acceptance framework does not let either result silently override the other, and does not force an artificial PASS/REJECT choice when two methodologically valid, independent checks disagree. A new third standing verdict, **INCONCLUSIVE**, was added generically to `swing_research/acceptance_criteria.py` (`determine_acceptance_verdict()`) for exactly this situation — base run PASS, recent-period REJECT, with disclosed conflicting robustness evidence — and Minervini Trend Template Filter is the first strategy to receive it. This means: **not approved for paper trading, but not permanently rejected either.** Future evidence (additional market history as it accumulates, alternate universes, further robustness studies) may resolve the conflict in either direction. No parameter tuning, rule changes, or strategy-specific overrides were made in pursuit of any particular verdict at any point — only two genuine, generically-applicable framework gaps (the window-sizing bug, and the missing third verdict) were fixed, each verified against the full test suite (443 tests) before being applied to this strategy's result.

## Lessons Learned

1. **A strategy's own required lookback period can silently break a short evaluation window** — walk-forward window sizing must account for how much history a strategy needs before it can trade at all, not just how much calendar time is available. Now handled generically via `Strategy.min_lookback_days` + `acceptance_criteria._feasible_window_count()`, applicable to every future long-lookback strategy (52-Week High Momentum, Cross-Sectional Momentum) without any strategy-specific hardcoding.
2. **A 0-trade REJECT and a genuine negative-expectancy REJECT look identical in a one-line verdict** — always inspect the underlying trade counts and per-window metrics before accepting an audit verdict at face value; the first run's REJECT here was actually evidence of a framework bug, not of the strategy failing.
3. **A REJECT built on very few walk-forward windows is weaker evidence than one built on many** — the acceptance criteria's binary "≥50% of windows positive" threshold becomes a near-coin-flip when a strategy's lookback requirement only leaves room for 1-2 consistency windows in a fixed recent-period length; this is a real limitation of applying one fixed `RECENT_PERIOD_YEARS` uniformly across strategies with very different lookback needs, worth revisiting if a future strategy's evaluation is this thin.
4. **A recent-period check and an independent robustness study can legitimately disagree** — treating one as automatically authoritative over the other (in either direction) would silently substitute an arbitrary choice for a real, disclosed conflict in the evidence. The acceptance framework now has a principled third outcome, `INCONCLUSIVE`, for exactly this case, applicable to any future strategy where the same conflict arises.
5. **The Relative Strength substitute remains the single largest fidelity gap of this whole implementation** (per the disclosed assumptions-impact estimate) — any future revisit of this strategy should prioritize sourcing a closer IBD RS Rating approximation over any other single change.
