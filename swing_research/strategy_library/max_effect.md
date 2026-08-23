# Strategy Library: MAX Effect (Lottery-Demand Anomaly) (SW-011)

**Status: Research complete. PASS on both checks performed — base run and recent-period. Deployment Status: RESEARCH — not promoted to PAPER_TRADING (per explicit direction; promotion requires a separate, later decision).**

---

## 1. GENERAL

| | |
|---|---|
| Strategy ID | SW-011 |
| Strategy Name | MAX Effect (Lottery-Demand Anomaly) |
| Research Source | Bali, T.G., Cakici, N. and Whitelaw, R.F. (2011), "Maxing Out: Stocks as Lotteries and the Cross-Section of Expected Returns," *Journal of Financial Economics*, Vol. 99, No. 2 |
| Research Verdict | **PASS** (base EXP-042 + recent-period EXP-043, clean, no conflict) |
| Evidence Quality | **HIGH (90.0/100)** on both runs |
| Deployment Status | RESEARCH (not promoted) |

## Documented Rules vs. Implementation Assumptions

**Documented:** MAX = the single highest daily return a stock experienced within the trailing one month (the paper's primary MAX(1) specification — MAX(5), the average of the 5 highest days, is the paper's own secondary robustness check, not the headline result). Cross-sectional decile sort by MAX at each formation date. Long the bottom decile (lowest MAX — calmest stocks) / short the top decile (highest MAX — most lottery-like stocks) in the original zero-cost portfolio. One-month holding period, standard monthly-rebalance construction. The paper's own regressions show the effect survives controlling for size, book-to-market, momentum, and short-term reversal.

**Disclosed implementation assumptions** (mirrors the precedent already established for every prior cross-sectional strategy in this program):
- **Long only**: DIRECTIONALLY UNKNOWN impact.
- **MAX(1) only, not MAX(5)**: MINOR — MAX(1) is itself the paper's primary, most-cited specification, not a weaker substitute.
- **Single-vintage holding, not overlapping portfolios**: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN — same structural deviation as every prior cross-sectional strategy.
- **Bottom-decile threshold (percentile ≤10)**.
- **Exit rule is ONLY the 21-trading-day time-stop or the synthetic protective stop — no percentile-based early exit.**
- **8% protective stop-loss and 1% position sizing: not part of the original methodology at all** — the source paper is a factor-return study with zero position-level risk management.
- **Known future overlap risk (disclosed, not a defect):** the paper reports MAX is highly correlated with idiosyncratic volatility — a separate, not-yet-implemented roadmap candidate. If idiosyncratic volatility is ever researched, the two should be disclosed together, not treated as independent diversification wins.

No parameters were tuned based on backtest results — every threshold above is either a direct restatement of the source paper or an existing standing convention already used by every prior strategy in this program.

---

## 2. EXPECTED PERFORMANCE (base run, EXP-042, full 457-symbol universe, 2016-08-22 to 2026-08-21)

| Metric | Value |
|---|---|
| CAGR | 11.49% |
| Profit Factor | 1.267 |
| Sharpe | 0.829 |
| Sortino | 0.869 |
| Maximum Drawdown | 41.95% |
| Win Rate | 47.92% |
| Expectancy | ₹174.07/trade |
| Recovery Factor | 1.325 |
| Total Return on Capital (10y) | 196.53% |

**Plain-English summary**: this strategy wins slightly under half its trades (48%) but with a profit factor of 1.27 (₹1.27 made for every ₹1 lost). CAGR (11.49%) and Sharpe (0.829) are moderate rather than exceptional relative to the program's other PASS strategies — the strongest thing about this result is not the return level but its *consistency*: 100% of walk-forward windows showed positive expectancy, and the out-of-sample holdout alone (381 trades) had a clean positive expectancy of +₹152.39/trade, on a genuinely novel, previously-untested mechanism for this program.

---

## 3. TRADING CHARACTERISTICS (from a full-period trade-level reproduction of the base run — see note below)

| Metric | Value |
|---|---|
| Average Holding Period | 25.7 days |
| Median Holding Period | 30 days |
| Minimum Holding Period | 0 days (a same-day stop-loss hit) |
| Maximum Holding Period | 33 days (the 21-trading-day time-stop, calendar-converted, plus a small tail) |
| Average Trades per Year | ~98.6 (2016–2026) |
| Average Open Positions (simultaneous) | 7.58 |
| Maximum Open Positions (simultaneous) | 9 |
| Capital Utilization (Exposure %) | 3.49% (base) / 6.22% (recent-period) |

**Data note**: this section's trade-level detail (exit-reason split, holding-period distribution, position counts, sector P&L) comes from a separate, deliberately re-run continuous simulation using the identical strategy/parameters — 1,085 trades, close to but not identical to the officially-recorded 1,129/1,149 trade counts in EXP-042's saved metrics. The small discrepancy is consistent with this platform's already-disclosed yfinance data-provider limitation (an unofficial source that can return slightly different bars on a re-fetch) — not a methodology change. All *acceptance* figures (verdict, evidence quality, expectancy) are taken from the officially saved EXP-042/EXP-043 records, never from this reproduction run.

---

## 4. EXIT ANALYSIS (from the 1,085-trade reproduction run)

| Exit reason | Count | % |
|---|---|---|
| Signal exit (= the 21-trading-day time-stop — this strategy has no other signal-based exit rule) | 753 | 69.4% |
| Protective stop-loss | 324 | 29.9% |
| End-of-backtest forced close | 8 | 0.7% |

The average-vs-median holding period gap (25.7 vs. 30 days) confirms a meaningful share of trades exit early via the protective stop — 29.9% of trades, a moderate rate, lower than Short-Term Reversal's 41.5% but higher than the momentum strategies' typical stop-hit rate, consistent with MAX Effect entries following a period of already-heightened single-day volatility.

---

## 5. MARKET BEHAVIOUR

- **Best market conditions**: bullish regime — ₹129,237 of the base run's total P&L came from bullish-regime trades vs. ₹67,288 in bearish regime. The strategy works in both, roughly 2:1 in favor of bullish conditions.
- **Best sectors**: Healthcare (+₹588,718), Financial Services (+₹562,074), Capital Goods (+₹287,157), Chemicals (+₹209,438), Information Technology (+₹186,552).
- **Weak sectors**: Consumer Durables (-₹142,679), Consumer Services (-₹118,645), Metals & Mining (-₹85,135), Media Entertainment & Publication (-₹64,677) — the deepest negative sectors of the ten with net-negative P&L.
- **Holding-period P&L concentration**: the bulk of total profit (+₹623,672) comes from trades held up to the ~40-calendar-day (full time-stop) horizon; every shorter holding-period bucket (≤5d, ≤10d, ≤20d) is net NEGATIVE. This says the edge genuinely needs the full documented holding period to show up — trades that get cut short (mostly via the stop-loss) are, on average, losers, exactly as the exit-analysis stop-hit rate above would suggest.

---

## 6. RISK PROFILE

- **Risk Rating: MODERATE-to-HIGH** relative to the other strategies in this program — 41.95% base-run maximum drawdown is deep (though the recent 3-year window's drawdown, 23.48%, is much shallower, since the COVID crash isn't in that slice), and CAGR/Sharpe are toward the lower end of this program's PASS strategies. Offset by HIGH evidence quality (90/100) on both runs and a genuinely novel, previously-untested mechanism.
- No allocation or paper-trading capital recommendation is made here — this strategy is not being promoted at this time.

---

## 7. Validation Summary

- **Base run** (EXP-042, 2016–2026, full 457-symbol universe): PASS. 1,149 walk-forward trades, **100% window consistency**, OOS expectancy +₹152.39/trade. Evidence quality HIGH (90.0/100).
- **Recent-period check** (EXP-043, most recent 3 years, 744 available trading days, full 3 requested walk-forward windows used): PASS. 347 trades, **50% window consistency** — exactly at the acceptance framework's minimum threshold, disclosed here as a genuine caveat rather than a clean pass on every window. The out-of-sample holdout itself (121 trades) was cleanly positive at +₹26.56/trade. Evidence quality HIGH (90.0/100).
- **Official acceptance verdict: PASS** (`determine_acceptance_verdict("PASS", "PASS")`) — both runs independently PASS, no INCONCLUSIVE resolution needed.
- No supplementary robustness sub-period run was performed — the acceptance framework only requires one when the recent-period result REJECTs and needs a disclosed-conflict resolution; here recent-period itself PASSed, so no additional run was warranted (avoiding search for confirmation beyond what the standing process requires).

---

## 8. Comparison Against Every Strategy Researched So Far

| Metric | SW-002 Minervini | SW-003 52-Week High | SW-006 Cross-Sectional Mom. | SW-008 Short-Term Reversal | SW-010 Amihud | **SW-011 MAX Effect** |
|---|---|---|---|---|---|---|
| Research Verdict | INCONCLUSIVE | PASS | PASS | PASS | PASS* | **PASS** |
| CAGR | 24.7% | 22.8% | 20.67% | 21.98% | — | **11.49%** |
| Sharpe | 1.038 | 1.058 | 1.046 | 1.135 | — | **0.829** |
| Max Drawdown | 32.85% | 30.8% | 32.79% | 45.58% | — | **41.95%** |
| Profit Factor | 2.13 | 2.30 | 1.70 | 1.37 | — | **1.267** |
| Win Rate | 28.3% | 36.0% | 28.7% | 46.2% | — | **47.92%** |
| Expectancy/trade | ₹1,231 | ₹2,220 | ₹1,594 | ₹511 | — | **₹174.07** |
| Avg Holding Period | 30.8d | 89.1d | 79.3d | 23.1d | — | **25.7d** |
| Trade Count (base) | 657 | 306 | 348 | 1,254 | — | **1,149** |
| Evidence Quality | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | HIGH | **HIGH (90)** |

*Amihud's figures are omitted from the numeric row comparison — it was researched under a different (execution-realistic) engine with modeled transaction costs, not directly comparable to this table's zero-cost figures without restating.

**Where MAX Effect fits**: the lowest CAGR, Sharpe, and expectancy-per-trade of any PASS-verdict strategy in the program so far — a genuinely weaker standalone result than 52-Week High Momentum, Cross-Sectional Momentum, or Short-Term Reversal. Its case for research value rests on being a mechanistically distinct, previously-untested signal (the first true behavioral/lottery-demand mechanism in this program) with a clean, consistent PASS on both required checks, not on being the strongest performer.

---

## 9. Paper Trading Expectation Report (operational planning only — derived from backtest frequency, not a guarantee; not currently applicable since this strategy is not being deployed)

| Metric | Expected (if ever deployed) |
|---|---|
| BUY signals per month | ~8 |
| Trades per year | ~99 |
| Average holding period | ~26–30 days |
| Average open positions | ~7–8 (max observed 9) |
| Capital utilization | ~3–6% of paper capital |

---

## 10. Portfolio Impact Analysis (advisory only — informational for a future decision, not applicable now since this strategy is not being deployed)

**Methodology disclosure**: correlation below is estimated from each strategy's own `monthly_returns_pct` series (already saved in every experiment's `metrics.json`), aligned by calendar month across strategies with overlapping periods — a **monthly-return-correlation proxy**, not a true daily-return correlation.

| Comparison | Overlapping months | Monthly-return correlation |
|---|---|---|
| vs. Turtle (SW-001) | 75 | 0.226 (weak) |
| vs. Minervini (SW-002) | 105 | 0.099 (essentially uncorrelated) |
| vs. 52-Week High Momentum (SW-003) | 80 | 0.413 (moderate) |
| vs. Cross-Sectional Momentum (SW-006) | 90 | 0.439 (moderate) |
| vs. Short-Term Reversal (SW-008) | 120 | **0.657 (fairly strong)** |

**Interpretation**: MAX Effect's monthly returns are essentially uncorrelated with Minervini's and only weakly correlated with Turtle's, but meaningfully more correlated with the cross-sectional cluster — notably Short-Term Reversal at 0.657, higher than any pairwise correlation Short-Term Reversal itself showed against the momentum strategies (0.356–0.392) in its own library entry. This is disclosed honestly here: while MAX Effect's *selection mechanism* (calm vs. lottery-like stocks) is genuinely distinct from every existing strategy's mechanism, its *portfolio-level monthly returns* are not as independent from the existing paper-trading strategies as the zero factor-tag overlap alone would suggest. A plausible explanation is that all of these are long-only, cross-sectional decile-sort strategies on the same 457-symbol NSE universe, so they partly share exposure to the same broad market months even when their individual stock selections differ — this is a hypothesis, not confirmed here, and would need per-strategy holdings-overlap data (not currently persisted) to verify directly.

**Not making a deployment recommendation**: per explicit direction, this strategy is not being promoted to PAPER_TRADING at this time. This section is recorded now, while the analysis is fresh, so it is available if/when a future promotion decision is considered.

---

## 11. Final Deployment Recommendation

**No promotion recommendation is being made at this time, per explicit direction.** This strategy has a clean, valid PASS research verdict (both required checks passed, HIGH evidence quality on both) and is a genuinely distinct addition to the research pipeline — the first behavioral/lottery-demand mechanism tested in this program. Its standalone return profile is the weakest of the program's PASS-verdict strategies, and its monthly-return correlation with Short-Term Reversal (0.657) is higher than its zero factor-tag overlap would suggest, both worth weighing carefully if and when a promotion decision is ever considered. Research Verdict and Deployment Status remain fully independent — this record does not itself change Deployment Status; any future promotion remains a separate, explicit decision.
