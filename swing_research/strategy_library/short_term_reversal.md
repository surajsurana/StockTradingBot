# Strategy Library: Short-Term Reversal (SW-008)

**Status: Research complete. PASS on every check performed — base run, recent-period, and the supplementary post-COVID robustness run. The strongest and most consistent result of any strategy in this program to date. Deployment Status: RESEARCH — paper-trading decision pending, see Final Deployment Recommendation below.**

---

## 1. GENERAL

| | |
|---|---|
| Strategy ID | SW-008 |
| Strategy Name | Short-Term Reversal |
| Research Source | Jegadeesh, N. (1990), "Evidence of Predictable Behavior of Security Returns," *The Journal of Finance*, Vol. 45, No. 3 |
| Research Verdict | **PASS** (base EXP-020 + recent-period EXP-021, both clean, no conflict; supplementary post-COVID EXP-022 also PASS) |
| Evidence Quality | **HIGH (90.0/100)** on every single run performed — base, recent-period, and post-COVID |
| Deployment Status | RESEARCH (decision pending — see Section 7) |

## Documented Rules vs. Implementation Assumptions

**Documented:** formation period = prior 1-month return; cross-sectional decile sort at each formation date; long the bottom decile (worst performers) / short the top decile (best performers) in the original zero-cost portfolio; 1-month holding period; standard Jegadeesh-style overlapping-portfolio construction.

**Disclosed implementation assumptions** (mirrors the precedent already established for 52-Week High Momentum and Cross-Sectional Momentum, polarity reversed, horizon shortened):
- **Long only**: DIRECTIONALLY UNKNOWN impact.
- **Single-vintage holding, not overlapping portfolios**: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN — same structural deviation as every prior cross-sectional strategy, but note the much shorter 1-month holding period (vs. 6 months for the momentum strategies) means meaningfully higher trade turnover, a genuine behavioral difference in its own right.
- **Bottom-decile threshold (percentile ≤10)** — the mirror image of every momentum strategy's ≥90 threshold, by design.
- **Exit rule is ONLY the 21-trading-day time-stop or the synthetic protective stop — no percentile-based early exit.**
- **8% protective stop-loss and 1% position sizing: not part of the original methodology at all** — the source paper has zero position-level risk management.
- **Naming**: explicitly distinguished from the unrelated existing production strategy `strategies/mean_reversion.py` (an RSI/Bollinger-Band per-symbol technical signal — a completely different mechanism). This strategy's key is `short_term_reversal`, never `mean_reversion`.

---

## 2. EXPECTED PERFORMANCE (base run, EXP-020, full 457-symbol universe, 2016–2026)

| Metric | Value |
|---|---|
| CAGR | 21.98% |
| Annualized Return (10y total return basis) | 21.98% (equity-curve CAGR) |
| Profit Factor | 1.37 |
| Sharpe | 1.135 |
| Sortino | 1.326 |
| Maximum Drawdown | 45.58% |
| Win Rate | 46.2% |
| Expectancy | ₹510.64/trade |
| Recovery Factor | 1.89 |

**Plain-English summary**: this strategy wins less than half its trades (46%) but the winners are large enough relative to the losers to still be strongly profitable (profit factor 1.37 — for every ₹1 lost, roughly ₹1.37 is made). It trades far more often than any other strategy in this program (1,254 walk-forward trades vs. 234–657 for the others), so its statistical evidence is unusually strong (HIGH evidence quality on every single run). The 45.58% maximum drawdown is the largest of any strategy tested so far — a real cost of the strategy's nature (buying stocks that just fell sharply means occasionally buying into a stock that keeps falling). Recovery factor of 1.89 means the strategy's total profit over the full period was about 1.89x its worst peak-to-trough loss — it does recover, but the drawdowns along the way are the steepest in the program.

---

## 3. TRADING CHARACTERISTICS (base run, computed from the actual trade list)

| Metric | Value |
|---|---|
| Average Holding Period | 23.1 days (metrics.json) — trade-level median 30 days |
| Median Holding Period | 30 days |
| Minimum Holding Period | 1 day (an immediate stop-loss hit) |
| Maximum Holding Period | 33 days (the 21-trading-day time-stop, calendar-converted, plus a small tail) |
| Average Trades per Year | 114.0 |
| Average Trades per Month | 9.5 |
| Average Open Positions (simultaneous) | 8.25 |
| Maximum Open Positions (simultaneous) | 15 |
| Capital Utilization (Exposure %) | 4.08% (base) / 6.95% (recent-period) / 4.76% (post-COVID) |
| Exposure % | Same as above |

Note the average-vs-median holding period gap (23.1 vs. 30 days): a meaningful share of trades exit early via the protective stop (see Exit Analysis below), pulling the average down below the time-stop-driven median.

---

## 4. EXIT ANALYSIS (from the actual 1,254-trade base-run trade list)

| Exit reason | Count | % |
|---|---|---|
| Signal exit (= the 21-trading-day time-stop — this strategy has no other signal-based exit rule) | 726 | 57.9% |
| Protective stop-loss | 520 | 41.5% |
| End-of-backtest forced close | 8 | 0.6% |

**41.5% of trades hit the protective stop** — meaningfully higher than any momentum strategy in this program, consistent with the strategy's own nature (buying stocks that just declined sharply carries real risk of continued decline before reversing).

---

## 5. MARKET BEHAVIOUR

- **Best market conditions**: bullish regime — ₹418,078 of the base run's total P&L came from bullish-regime trades vs. ₹211,031 in bearish regime (regime_breakdown, EXP-020). The strategy works in both, but noticeably better in bullish conditions.
- **Worst market conditions**: bearish regime is still net positive but roughly half as productive per the regime breakdown above.
- **Best sectors**: Financial Services (+₹152,400), Capital Goods (+₹136,138), Healthcare (+₹100,894), Oil Gas & Consumable Fuels (+₹75,428).
- **Weak sectors**: Power (-₹21,116), Chemicals (-₹17,173), Services (-₹9,121), Textiles (-₹1,405) — the only four sectors with net negative P&L across the whole 10-year base run.
- **Best months** (aggregate P&L by calendar month, all years): April (+₹176,617), September (+₹90,824), June (+₹83,688).
- **Worst months**: March (-₹108,401), October (-₹31,715), January (-₹20,243).

---

## 6. RISK PROFILE

- **Risk Rating: HIGH** relative to the other strategies in this program — largest maximum drawdown (45.58% base, 47.05% post-COVID), highest trade frequency (more exposure to any single bad stretch), and the highest protective-stop-hit rate (41.5%) of any strategy tested. Offset by the highest evidence quality (HIGH, 90/100, on every run) and strong Sharpe/Sortino.
- **Suggested capital allocation if eventually deployed**: given the drawdown profile, a smaller allocation than a typical first paper-trading candidate — see Section 7's specific recommendation.

## Live Monitoring Targets (expected operating ranges, for comparing paper-trading reality against this backtest)

| Metric | Expected range |
|---|---|
| Win Rate | 44%–50% |
| Holding Period | 20–33 days (time-stop bound), median ~30 |
| Trades per Year | ~90–140 |
| Drawdown | Should not persistently exceed ~50% without triggering a closer review |
| Expectancy | ₹150–₹550/trade (recent-period low end to base-run high end) |

---

## 7. Validation Summary

- **Base run** (EXP-020, 2016–2026): PASS. 1,254 walk-forward trades, **100% window consistency**, OOS expectancy +₹385.03/trade. Evidence quality HIGH (90.0/100).
- **Recent-period check** (EXP-021, most recent 3 years): PASS. 374 trades, **100% window consistency** using the full requested 3 windows (the lightest lookback — 21 days — of any strategy tested, so no windowing reduction was needed). OOS expectancy +₹167.41/trade. Evidence quality HIGH (90.0/100).
- **Official acceptance verdict: PASS** (`determine_acceptance_verdict("PASS", "PASS")`) — clean, no conflict.
- **Supplementary post-COVID robustness run** (EXP-022, 2020–2026, additional evidence only, does not alter the official verdict): **also PASS**, 848 trades, 100% window consistency, OOS expectancy +₹151.55/trade, evidence quality HIGH (90.0/100). Unlike Cross-Sectional Momentum's post-COVID REJECT, Short-Term Reversal's edge holds up cleanly across every window tested, including the most recent structural regime.

---

## 8. Comparison Against Every Strategy Researched So Far

| Metric | SW-001 Turtle | SW-002 Minervini | SW-003 52-Week High | SW-004 MA Crossover* | SW-005 Mean Reversion* | SW-006 Cross-Sectional Mom. | **SW-008 Short-Term Reversal** |
|---|---|---|---|---|---|---|---|
| Research Verdict | REJECT | INCONCLUSIVE | PASS | REJECT | REJECT | PASS | **PASS** |
| CAGR | 28.87% | 24.7% | 22.8% | 4.73%* | 8.75%* | 20.67% | **21.98%** |
| Sharpe | 0.726 | 1.038 | 1.058 | 0.559* | 0.978* | 1.046 | **1.135** |
| Sortino | 1.972 | 2.449 | 1.655 | 0.517* | 1.548* | 1.794 | **1.326** |
| Max Drawdown | 31.01% | 32.85% | 30.8% | 16.73%* | 16.78%* | 32.79% | **45.58%** |
| Profit Factor | 2.13 | 2.13 | 2.30 | 1.17* | 1.21* | 1.70 | **1.37** |
| Win Rate | 38.0% | 28.3% | 36.0% | 36.8%* | 33.6%* | 28.7% | **46.2%** |
| Expectancy/trade | ₹4,968 | ₹1,231 | ₹2,220 | ₹557* | ₹366* | ₹1,594 | **₹511** |
| Avg Holding Period | 51.7d | 30.8d | 89.1d | 24.8d* | 10.0d* | 79.3d | **23.1d** |
| Trade Count (base) | 234 | 657 | 306 | 299* | 882* | 348 | **1,254** |
| Evidence Quality | — | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | **HIGH (90)** |

*MA Crossover/Mean Reversion figures are from their certification's out-of-sample window (`deployment/certification_experiments/EXP-001`/`EXP-003`), not a continuous full-period run — the certification pipeline doesn't compute a separate continuous run the way published-strategy experiments do. Disclosed methodology difference, not an apples-to-apples continuous CAGR.

**Where Short-Term Reversal fits**: highest Sharpe (1.135) and highest win rate (46.2%) of every strategy in the program, but also the deepest drawdown (45.58%) and by far the highest trade count (1,254 vs. the next-highest 882 for Mean Reversion's OOS window). It is the only strategy alongside 52-Week High Momentum and Cross-Sectional Momentum to achieve a clean PASS — and unlike Cross-Sectional Momentum, it has zero conflicting robustness evidence anywhere.

---

## 9. Paper Trading Expectation Report (operational planning only — derived from backtest frequency, not a guarantee)

| Metric | Expected |
|---|---|
| BUY signals per month | ~9–10 |
| EXIT signals per month | ~9–10 (steady-state, roughly matches entries once positions are seasoned) |
| Trades per year | ~114 |
| Average holding period | ~23–30 days |
| Average open positions | ~8 (max observed 15) |
| Capital utilization | ~4–7% of paper capital |
| Telegram message frequency | One message every trading day (per the existing daily paper-trading cadence) — roughly 9–10 of those messages/month will contain an actual BUY, a similar number an EXIT, the rest "no qualifying setups" |
| Portfolio turnover | High relative to the other strategies — 1-month holding period vs. 6 months for the momentum strategies means roughly 6x the position turnover for the same capital base |

---

## 10. Portfolio Impact Analysis (advisory only — does not modify the frozen framework, acceptance criteria, evidence quality scoring, deployment system, or governance logic)

**Methodology disclosure**: exact overlapping daily-return correlation isn't persisted per experiment. Correlation below is estimated from each strategy's own `monthly_returns_pct` series (already saved in every experiment's `metrics.json`), aligned by calendar month across strategies with overlapping periods — a **monthly-return-correlation proxy**, not a true daily-return correlation. This is stated explicitly, not presented as exact.

| Comparison | Overlapping months | Monthly-return correlation |
|---|---|---|
| vs. Turtle (SW-001) | 75 | **-0.047** (essentially uncorrelated) |
| vs. Minervini (SW-002) | 105 | **0.120** (weak) |
| vs. 52-Week High Momentum (SW-003) | 80 | **0.392** (moderate) |
| vs. Cross-Sectional Momentum (SW-006) | 90 | **0.356** (moderate) |

**Interpretation**: Short-Term Reversal's monthly returns are essentially uncorrelated with Turtle's, weakly correlated with Minervini's, and only moderately correlated with the two other momentum strategies (52-Week High, Cross-Sectional Momentum) — meaningfully lower than what pure directional overlap alone would suggest for two strategies that are both cross-sectional, decile-based, and reasonably highly diversified across the same 457-symbol universe. This is consistent with the underlying mechanism being genuinely different (buying recent losers vs. buying recent winners), not just a relabeled version of the same trade.

**Holdings/entry-timing overlap**: sector exposure overlaps meaningfully with 52-Week High Momentum and Cross-Sectional Momentum on the *winning* side (Financial Services, Capital Goods, Healthcare are strong for all three), but Short-Term Reversal's worst sector (Power, -₹21,116) is not a notably weak sector for the other two — suggesting the specific stock-level entries, not just sector exposure, differ meaningfully. Entry timing is inherently different by construction: this strategy enters on a 21-day formation window (buying stocks that just fell), while the momentum strategies enter on 126–252-day formation windows (buying stocks that have been rising for months) — these are close to structurally incompatible signals to fire simultaneously on the same stock.

**Diversification benefit**: real, moderate-to-strong. Not one of the near-zero-correlation "true diversifiers" like Turtle, but meaningfully less correlated with the momentum cluster than the momentum strategies are with each other (52-Week High vs. Cross-Sectional Momentum would be expected to correlate more strongly, both being long-only cross-sectional momentum signals on the same universe).

**Incremental expected return**: positive and substantial (CAGR 21.98% base, second only to 52-Week High Momentum's 22.8% among PASS-verdict strategies) — this is not a diversifier bought at the cost of returns.

**Incremental expected drawdown**: this is the real cost. 45.58% is meaningfully deeper than 52-Week High Momentum's 30.8% — adding this strategy alongside SW-003 would likely deepen combined portfolio drawdowns during the periods when both strategies are struggling simultaneously (their 0.392 correlation means "simultaneously struggling" is a real, non-trivial scenario, not a rare one).

**Incremental capital utilization**: modest — 4-7% exposure means running this strategy alongside SW-003 (also single-digit exposure) would still leave the large majority of paper capital uncommitted at any time; capital utilization is not the binding constraint for running both together.

**Does it improve or reduce overall portfolio efficiency?** On the evidence available: likely improves it, for a user willing to accept the drawdown profile. The moderate (not high) correlation with existing momentum strategies plus a genuinely different entry mechanism (losers vs. winners, 1-month vs. 6-month horizon) means combining Short-Term Reversal with SW-003 would probably smooth SOME return paths that a pure-momentum portfolio would experience together, even though it doesn't eliminate co-drawdown risk entirely.

### Answers to the required questions

1. **Standalone vs. diversifier**: Both — it is strong enough standalone (best Sharpe and win rate in the program) that it doesn't need to be justified purely as a diversifier, but its real portfolio value is likely higher as a moderate diversifier alongside the momentum cluster than as an isolated holding, given the drawdown profile.
2. **Which strategies should run together**: Short-Term Reversal alongside 52-Week High Momentum (SW-003) is a reasonable pairing — moderate (not high) correlation, genuinely different mechanism and horizon. Running it alongside Cross-Sectional Momentum (SW-006, currently HOLD) would be more correlated (0.356) and less additive, if SW-006 is ever reconsidered.
3. **Recommended capital split**: given the drawdown difference (45.58% vs. 30.8%), a smaller allocation to Short-Term Reversal than to 52-Week High Momentum — e.g. roughly 60/40 or 65/35 in SW-003's favor, not an equal split, would be a reasonable starting point if both are run together. This is a suggestion, not an automated allocation — actual capital-split configuration remains a manual, config-driven decision per the standing Pilot Live design.
4. **Improve or duplicate?** Improves — the correlation evidence and mechanistic difference both point away from duplication, even though there is real sector-level overlap on the winning side.

---

## 11. Final Deployment Recommendation

**Recommendation: candidate for PAPER_TRADING alongside SW-003**, with an explicit caveat about the drawdown profile — this is a recommendation only, not an automatic registration. Basis: genuine diversification (moderate, not high, monthly-return correlation with the existing momentum strategies; mechanistically the opposite signal — losers vs. winners), the cleanest and most consistent validation result of any strategy in this program (PASS on base, recent-period, AND the supplementary post-COVID run, with zero conflicting evidence anywhere), and a strong standalone risk-adjusted return profile (highest Sharpe and win rate in the program). The counterweight is real: the deepest drawdown (45.58%) and highest trade frequency (1,254 trades) of any strategy tested — this is not a "safe" complement in the way a low-correlation, low-drawdown strategy would be, and should be sized accordingly (see the suggested capital split above) rather than treated as a like-for-like swap with SW-003.

Research Verdict and Deployment Status remain fully independent — this recommendation does not itself change Deployment Status; that remains a separate, explicit decision.
