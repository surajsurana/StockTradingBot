# Strategy Library: Turn-of-the-Month Effect (SW-013)

**Status: Research complete. PASS on both checks performed — base run and recent-period, under the generic candidate-ranking architecture fix. Deployment Status: RESEARCH — not promoted to PAPER_TRADING (per explicit direction; promotion requires a separate, later decision).**

---

## 1. GENERAL

| | |
|---|---|
| Strategy ID | SW-013 |
| Strategy Name | Turn-of-the-Month Effect |
| Research Source | Ariel, R.A. (1987), "A Monthly Effect in Stock Returns," *Journal of Financial Economics*, Vol. 18, No. 1 |
| Research Verdict | **PASS** (base EXP-050 + recent-period EXP-051, clean, no conflict) |
| Evidence Quality | **HIGH (90.0/100)** on both runs |
| Deployment Status | RESEARCH (not promoted) |

## A Real Implementation Bug, Fixed at the Architecture Level, Not the Strategy Level

This strategy exposed a defect that took four attempts to actually fix — a genuinely important part of its own research history.

**Attempt 0 (discarded)**: a first backtest used no tie-breaker among the many symbols qualifying on the same calendar day. It confirmed empirically that the shared engine's `max_units_total=10` cap, combined with the frozen universe's purely **alphabetical** ticker ordering — zero economic meaning — collapsed onto the **same ~10 alphabetically-early symbols filling every month for the entire 10-year history**, touching only 10 of 20 sectors.

**Attempt 1 (discarded)**: a SW-013-specific fix restricting entry eligibility to a **static** rotating 1/4 slice of the universe improved coverage to 17/20 — but every symbol in the sectors still missing had 12-125 other symbols in its same static bucket that always came alphabetically before it. A persistent, near-permanent structural exclusion, not residual noise.

**Attempt 2 (discarded)**: a **per-month combined-rank** hash improved coverage to 18/20 — better, but a full diagnostic (correlating alphabetical position against win rate across the ENTIRE universe, not just Turn-of-Month's missing sectors) found the deeper truth: **291 of 457 symbols (64% of the whole universe) had zero wins despite firing legitimately**, correlation -0.72 with alphabetical position. Reshuffling *which* symbols were eligible each month never fixed *who wins the fill* among them — the shared engine still walked its eligible-today candidates in fixed alphabetical order and filled the first few it reached.

**The actual fix (kept)**: a generic, program-wide architecture change — `swing_research/candidate_ranking.py` — applied to the shared backtesting engine and the paper-trading engine, not to this strategy alone. Every strategy's candidates are now collected first (order-independent), then ranked by the strategy's own already-computed signal strength where one exists, with ties (Turn-of-Month has no natural per-stock ranking at all — Ariel's own finding provides none) broken by an independent, per-(symbol, date) pseudo-random key, verified at scale to have near-zero correlation with alphabetical position (r < 0.15 in a 457-symbol synthetic test, versus the original bug's -0.72). Under this fix: **20 of 20 sectors, on both the base run and the recent-period run.** See `swing_research/candidate_ranking.py`'s module docstring and `test_candidate_ranking.py` for the full technical account, including the specific bug in an even-earlier version of the fix itself (a list shuffle that was, incorrectly, still input-order-dependent) caught by this module's own test suite before being applied to any strategy.

---

## Documented Rules vs. Implementation Assumptions

**Documented:** returns are disproportionately concentrated in the turn-of-month window — the LAST trading day of the month through the THIRD trading day of the following month (a 4-trading-day window, Ariel's own "-1 to +3" definition). Essentially all of the market's cumulative return over the paper's sample period was concentrated here; the rest of the month was flat on average.

**Disclosed implementation assumptions:**
- **Applied per-symbol, universe-wide, not as a market-index timing signal**: DIRECTIONALLY UNKNOWN — Ariel's own test is the aggregate market (index) return; this platform's `Strategy` interface is per-symbol, so every symbol is calendar-eligible on the same day, with capital going to whichever candidates the shared, generic ranking mechanism selects (an unbiased, reproducible tie-break, since there is no economic basis to prefer one qualifying stock over another here).
- **Holding period computed by row position** (exactly 3 trading days after entry), not the generic trading-day-to-calendar-day approximation every other strategy in this program uses — more precise, not less, at this strategy's much shorter, weekend-sensitive horizon.
- **8% protective stop-loss and 1% position sizing**: not part of the original methodology at all, same standing convention as every prior strategy.
- **No cross-sectional percentile of any kind** — the first strategy in this program with zero indicator-based ranking, which is exactly why it was the strategy that finally exposed the ordering defect: every other strategy's own percentile partially masked the same underlying bug.

No parameters were tuned based on backtest results — the 4-trading-day window is a direct restatement of the source paper; the candidate-ranking architecture itself was designed and tested (via `test_candidate_ranking.py`, entirely on synthetic data) before ever being applied to this or any other strategy's real backtest.

---

## 2. EXPECTED PERFORMANCE (base run, EXP-050, full 457-symbol universe, 2016-08-29 to 2026-08-28)

| Metric | Value |
|---|---|
| CAGR | 6.75% |
| Profit Factor | 1.448 |
| Sharpe | 0.752 |
| Sortino | 0.372 |
| Maximum Drawdown | 33.57% |
| Win Rate | 53.15% |
| Expectancy | ₹95.05/trade |
| Average Holding Period | 4.8 calendar days (≈3 trading days — consistent with the design) |
| Exposure | 0.49% |

**Plain-English summary**: a high win rate (53.15%) and materially different risk shape from every prior strategy — short (~3 trading day), frequent, high-hit-rate trades instead of continuous multi-week/month exposure, and the lowest capital exposure of any strategy tested in this program (a natural consequence of genuinely spreading fills across the whole universe rather than concentrating in a lucky alphabetical subset). CAGR (6.75%) is modest, the expected trade-off of spending most of the month in cash.

---

## 3. RECENT-PERIOD CHECK (EXP-051, most recent 3 years, 2023-08-28 to 2026-08-28, full 3 walk-forward windows used)

**PASS, cleanly — 100% window consistency** (window 1: +₹26.97/trade, CAGR 2.83%; window 2: +₹46.02/trade, CAGR 4.94%; both positive, unlike every prior version of this strategy's recent-period check, which landed at exactly the 50% threshold). The out-of-sample holdout (106 trades) was strongly positive at +₹100.53/trade — the strongest out-of-sample figure this strategy has produced across every attempt. Continuous period metrics: CAGR 5.33%, Sharpe 0.733, Max Drawdown 9.84% — the shallowest drawdown of any version of this strategy's recent-period result. Evidence quality HIGH (90.0/100).

**Official acceptance verdict: PASS** (`determine_acceptance_verdict("PASS", "PASS")`) — both runs independently PASS, no INCONCLUSIVE resolution needed, no supplementary robustness sub-period run required.

---

## 4. TRADING CHARACTERISTICS

| Metric | Value |
|---|---|
| Total Trades (base, 10y) | 969 |
| Average Trades per Year | ~97 |
| Average Holding Period | 4.8 calendar days |
| Exposure (base) | 0.49% |
| Exposure (recent-period) | 0.85% |

---

## 5. MARKET BEHAVIOUR

- **Sector coverage — the definitive result**: **20 of 20 sectors** appear in both the base-run and recent-period trade logs. Every prior version of this strategy (10/20, then 17/20, then 18/20) undercounted the universe's true diversification because the underlying allocation mechanism, not the strategy, was broken.
- **Holding-period P&L**: concentrated in the `<=5d`/`<=10d` buckets — mechanically guaranteed by the fixed ~3-trading-day hold, not a finding.

---

## 6. RISK PROFILE

- **Risk Rating: LOW-to-MODERATE** relative to the other strategies in this program — a high win rate and the lowest capital exposure of any strategy tested, though CAGR and Sortino remain modest, reflecting the trade-off of a very short average time-in-market.
- Full, genuine universe-wide diversification is now confirmed, not approximated — the strongest diversification result of any strategy's own fix history in this program.
- No allocation or paper-trading capital recommendation is made here — this strategy is not being promoted at this time.

---

## 7. Validation Summary

- **Base run** (EXP-050, 2016–2026, full 457-symbol universe, generic candidate-ranking architecture applied): PASS. 981 walk-forward trades, 50% window consistency, OOS expectancy +₹62.52/trade. Evidence quality HIGH (90.0/100).
- **Recent-period check** (EXP-051, most recent 3 years, full 3 walk-forward windows used): PASS. 318 trades, **100% window consistency**, OOS expectancy +₹100.53/trade. Evidence quality HIGH (90.0/100).
- **Official acceptance verdict: PASS** (`determine_acceptance_verdict("PASS", "PASS")`).
- Three earlier attempts (no tie-breaker; static rotation bucket; per-month combined-rank hash) were each discarded before being reported as this strategy's result — see "A Real Implementation Bug" section above. The architecture that finally fixed this is generic, applied identically to every Swing Research strategy and the paper-trading engine, not specific to Turn-of-the-Month.

---

## 8. Comparison Against Every Strategy Researched So Far

| Metric | SW-002 Minervini | SW-003 52-Week High | SW-006 Cross-Sectional Mom. | SW-008 Short-Term Reversal | SW-011 MAX Effect | **SW-013 Turn-of-Month** |
|---|---|---|---|---|---|---|
| Research Verdict | INCONCLUSIVE | PASS | PASS | PASS | PASS | **PASS** |
| CAGR | 24.7% | 22.8% | 20.67% | 21.98% | 11.49% | **6.75%** |
| Sharpe | 1.038 | 1.058 | 1.046 | 1.135 | 0.829 | **0.752** |
| Max Drawdown | 32.85% | 30.8% | 32.79% | 45.58% | 41.95% | **33.57%** |
| Profit Factor | 2.13 | 2.30 | 1.70 | 1.37 | 1.267 | **1.448** |
| Win Rate | 28.3% | 36.0% | 28.7% | 46.2% | 47.92% | **53.15%** |
| Expectancy/trade | ₹1,231 | ₹2,220 | ₹1,594 | ₹511 | ₹174.07 | **₹95.05** |
| Avg Holding Period | 30.8d | 89.1d | 79.3d | 23.1d | 25.7d | **4.8d** |
| Trade Count (base) | 657 | 306 | 348 | 1,254 | 1,149 | **969** |
| Evidence Quality | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | **HIGH (90)** |

**Where Turn-of-the-Month fits**: lowest CAGR and expectancy-per-trade of any PASS-verdict strategy so far, but the highest win rate and the lowest capital exposure in the program — a genuinely different risk/return shape (short, frequent, high-hit-rate trades, minimal average exposure) rather than a weaker version of the others. Its case rests on being a mechanistically distinct signal family (pure calendar seasonality, the first in this program with zero cross-sectional ranking) with a clean PASS on both required checks — and, notably, on being the strategy whose own research process forced a real, program-wide architecture fix that every other Swing Research strategy now benefits from too.

---

## 9. Final Deployment Recommendation

**No promotion recommendation is being made at this time, per explicit direction.** This strategy has a clean, valid PASS research verdict (both required checks passed, HIGH evidence quality on both, 100% window consistency on the recent-period check) and is a genuinely distinct addition to the research pipeline — the first pure calendar/seasonality mechanism tested in this program. Its own investigation surfaced and fixed a real defect in the shared research and paper-trading infrastructure — not a defect in the strategy itself — now corrected generically for every strategy in the program, past and future. Research Verdict and Deployment Status remain fully independent — this record does not itself change Deployment Status; any future promotion remains a separate, explicit decision.
