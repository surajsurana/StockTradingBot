# Strategy Library: Idiosyncratic Volatility Anomaly (SW-012)

**Status: Research complete. INCONCLUSIVE — not approved for paper trading, not permanently rejected.**

## Original Publication

Ang, A., Hodrick, R.J., Xing, Y. and Zhang, X. (2006), "The Cross-Section of Volatility and Expected Returns," *The Journal of Finance*, Vol. 61, No. 1. Second RISK-BASED strategy in this program, after Betting Against Beta (SW-009, REJECTED).

## Summary

Stocks with the LOWEST idiosyncratic (residual, market-model-adjusted) volatility over the trailing month are expected to subsequently OUTPERFORM — the "low-vol puzzle": stocks that are calm once market-wide moves are stripped out earn anomalously higher returns than high-idio-vol stocks, the opposite of what a risk premium would predict. Long-only, bottom-decile (lowest idio-vol) selection, single-vintage, 21-trading-day hold.

## Documented Rules vs. Implementation Assumptions

**Documented:** idiosyncratic volatility = standard deviation of the residuals from a regression of daily stock returns on a factor model, estimated over the trailing month. The paper's PRIMARY, headline specification regresses on the FAMA-FRENCH 3-FACTOR model (market, SMB, HML). Quintile-sorted monthly; long the lowest-idio-vol quintile, short the highest, rebalanced monthly.

**Disclosed implementation assumptions** (see `swing_research/published_research_analyst.py`'s `IDIOSYNCRATIC_VOLATILITY_ANOMALY.assumptions_impact` for the full text):
- **Single-factor (CAPM/market-model) residual volatility, not the paper's primary 3-factor construction**: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN — the largest fidelity gap in this implementation. SMB/HML require point-in-time market-cap and book-to-market data this platform has already confirmed unavailable. NOT an invented substitute: the paper's own robustness section reports the anomaly survives under a single-factor construction, but that is not a guarantee of the same magnitude on NSE's cross-section. Computed via the closed-form single-regressor OLS identity `idio_vol = sigma_stock x sqrt(1 - rho^2)` rather than running an explicit regression — mathematically exact, not an approximation.
- **Formation window (21 trading days, ~1 month)**: kept FAITHFUL to the paper's own monthly re-formation, no shortening needed — unlike Betting Against Beta's beta lookback, this is the one dimension where fidelity is NOT reduced.
- **Long only**: measures only the long, low-idio-vol leg's own stock-selection return, not the paper's long-short spread — same reasoning as every prior strategy's long-only reduction.
- **Single-vintage holding, not overlapping portfolios**: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN — same structural deviation as every prior cross-sectional strategy.
- **Bottom-decile threshold (percentile ≤10)**, 8% protective stop-loss, 1% position sizing: same standing conventions as every prior strategy, none part of the original methodology.
- **Known, un-mitigated interaction risk (disclosed, not a defect)**: the idio-vol measure is documented in the literature to interact with short-term reversal if not separately controlled for; this platform does not attempt that control. The paper's own prior research (Bali, Cakici & Whitelaw 2011, already implemented as MAX Effect/SW-011) also reports MAX is highly correlated with idiosyncratic volatility — two related, not fully independent, signals now both live in this program's research pipeline.

No parameters were tuned based on backtest results — every threshold above is either a direct restatement of the source paper (or its own disclosed robustness alternative) or an existing standing convention already used by every prior strategy in this program.

## Implementation Status

**Complete.** Added `compute_idiosyncratic_volatility_score()` / `compute_idiosyncratic_volatility_percentile_ranks()` to `swing_research/cross_sectional.py` (second signal in this module needing an external market-index series, after Betting Against Beta), `IdiosyncraticVolatilityStrategy` to `swing_research/strategies/idiosyncratic_volatility.py`, `run_idiosyncratic_volatility_experiment()` to `research_director.py`, and a `ResearchExperimentSpec` entry to `strategy_catalog.py`. 27 new unit tests added (`test_idiosyncratic_volatility.py` plus additions to `test_cross_sectional.py`), including an independent closed-form cross-check of the residual-volatility formula against plain numpy. Full local suite (798 tests) green before any backtest was run.

## NSE Results

**Base run** (full 457-symbol frozen universe, 2016-08-29 to 2026-08-28, EXP-045): **PASS**. 1,146 walk-forward trades, **100% window consistency**, out-of-sample holdout (373 trades) expectancy +₹146.63/trade. Continuous full-period metrics: 1,142 trades, CAGR 9.32%, Sharpe 0.65, Sortino 0.704, Max Drawdown 46.14%, Profit Factor 1.206, Win Rate 47.46%, Expectancy ₹125.89/trade, average holding period 25.3 days, exposure 3.97%. Evidence quality HIGH (90.0/100). Best sectors: Capital Goods (+₹43,869), Automobile & Auto Components (+₹30,824), Healthcare (+₹25,131); weakest: Consumer Services (-₹30,300), Consumer Durables (-₹18,957), Realty (-₹8,096). Holding-period P&L is concentrated in trades held to the full ~40-day time-stop horizon (+₹538,304); every shorter bucket (≤5d, ≤10d, ≤20d) is net negative — the same pattern already seen in MAX Effect, consistent with both signals needing their full documented holding period to show an edge.

**Recent-period check** (most recent 3 years, 2023-08-28 to 2026-08-28, 2/3 walk-forward windows used — window count reduced by the strategy's own 21-day `min_lookback_days` combined with how the frozen `_feasible_window_count()` apportions this particular slice, EXP-046): **REJECT**. Out-of-sample holdout (127 trades) expectancy **-₹77.15/trade** — negative on genuinely unseen data, the acceptance framework's single most important check. Walk-forward consistency 50% (1 of 2 windows positive: window 1 was strongly positive at +₹358.12/trade, window 2 — the most recent stretch — negative at -₹83.12/trade). Continuous period metrics still look reasonable in aggregate (345 trades, CAGR 8.56%, Sharpe 0.684, Max Drawdown 20.45%) — exactly the kind of case the acceptance framework exists to catch: decent-looking aggregate numbers with a genuinely losing true out-of-sample tail. Evidence quality HIGH (90.0/100).

**Supplementary robustness run** (second-half 5-year sub-period, 2021-08-30 to 2026-08-28, 3 windows, EXP-047 — additional evidence only, does not itself alter the official acceptance verdict): **PASS**. Out-of-sample holdout (202 trades) expectancy **+₹28.81/trade** — positive, though modest. 574 total trades, 50% walk-forward consistency (window 1 negative at -₹54.21/trade, window 2 strongly positive at +₹385.56/trade), continuous CAGR 4.51%, Sharpe 0.405, Max Drawdown 23.06%. Evidence quality HIGH (90.0/100).

**These two results conflict.** Unlike Betting Against Beta — where the second-half robustness run *corroborated* the recent-period REJECT (both independently pointed to genuine regime decay) — here the richer 5-year robustness window says the true-out-of-sample tail is positive, while the officially mandated 3-year recent-period check says it is negative. The disagreement traces to which slice of the last few years the out-of-sample holdout happens to land on: the 3-year check's holdout captures a specifically weak recent stretch, while the 5-year window's later, larger out-of-sample slice happens to be positive overall. Neither check is invalid — they are simply measuring different (materially overlapping but not identical) periods, and reach opposite conclusions about the current regime.

## Final Verdict

**INCONCLUSIVE** (`determine_acceptance_verdict("PASS", "REJECT", conflicting_robustness_evidence=True)` = `INCONCLUSIVE`, frozen/unmodified function — the same third verdict introduced for Minervini Trend Template Filter, now used for the second time). Per the acceptance framework: base run PASS, recent-period REJECT, and a disclosed, methodologically valid robustness study reaches the opposite conclusion from the recent-period REJECT — the framework has no principled basis to pick a winner, so this is recorded as a genuine, unresolved conflict rather than an arbitrary REJECT or a falsely reassuring PASS. **Not approved for paper trading. Not permanently rejected.** Research Verdict INCONCLUSIVE and Deployment Status RESEARCH are recorded independently in the registry (SW-012) — this record does not itself change Deployment Status, and no promotion of any kind is being made or recommended here.

## Comparison Against Prior Risk-Based Strategy

| Metric | SW-009 Betting Against Beta | **SW-012 Idiosyncratic Volatility** |
|---|---|---|
| Base run verdict | PASS | **PASS** |
| Recent-period verdict | REJECT | **REJECT** |
| Robustness sub-period verdict | REJECT (corroborates) | **PASS (conflicts)** |
| Official verdict | REJECT | **INCONCLUSIVE** |
| Base CAGR / Sharpe | 10.77% / 0.806 | **9.32% / 0.65** |
| Recent-period OOS expectancy | +₹33.78 (barely positive, but 0% window consistency) | **-₹77.15 (negative)** |

**Where this fits**: both risk-based strategies tested so far show measurable recency weakness relative to their own base-run strength — this factor family has not yet produced a clean PASS in this program. The structural difference is that Betting Against Beta's two independent recency checks agreed (genuine decay), while Idiosyncratic Volatility's disagree (unresolved), which is why the two REJECT-leaning results land at different final verdicts rather than the same one.

## Lessons Learned

1. **A conflicting-robustness INCONCLUSIVE is not rare once you look for it** — this is the second strategy (after Minervini) to land here, and the first among the risk-based/cross-sectional-percentile family. The three-way acceptance framework (`acceptance_criteria.py`) is earning its keep as designed: a genuine disagreement between two valid tests is being recorded honestly instead of forced into a binary PASS/REJECT.
2. **"Good aggregate numbers, bad true out-of-sample tail" struck again** — both the recent-period check's own continuous-period aggregate (CAGR 8.56%, Sharpe 0.684) and the base run's strong headline metrics would, on their own, look promising; only the dedicated out-of-sample holdout in each windowed check reveals the weaker recent reality. This is exactly why this platform's acceptance gate checks OOS expectancy specifically rather than accepting an aggregate backtest number at face value.
3. **The single-factor-vs-3-factor substitution is the open question for any future revisit** — if this strategy is ever reconsidered (e.g. after more market history accumulates, or if point-in-time market-cap/book-to-market data ever becomes available), sourcing the closer 3-factor construction should be the first change attempted, ahead of any other adjustment.
4. **This program now holds two related, not fully independent, factor-family results** (MAX Effect, PASS/PAPER_TRADING; Idiosyncratic Volatility, INCONCLUSIVE) that the original MAX Effect literature itself flagged as correlated — worth remembering if either is ever considered for further capital allocation, so the two are not mistakenly treated as independent diversification wins.
