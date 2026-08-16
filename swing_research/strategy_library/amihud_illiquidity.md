# Strategy Library: Amihud Illiquidity Premium (SW-010)

**Status: Research complete. Official Research Verdict: PASS (base + recent-period, no conflict between the two official checks). BUT a supplementary, HIGH-evidence-quality robustness run REJECTs — conflicting evidence exists that the official two-check verdict, by the frozen framework's own design, does not see. Deployment Status: RESEARCH — no promotion recommendation follows automatically from this PASS. See Section 6.**

---

## 1. GENERAL

| | |
|---|---|
| Strategy ID | SW-010 |
| Strategy Name | Amihud Illiquidity Premium |
| Research Source | Amihud, Y. (2002), "Illiquidity and Stock Returns: Cross-Section and Time-Series Effects," *Journal of Financial Markets*, Vol. 5, No. 1 |
| Research Verdict | **PASS** (`determine_acceptance_verdict("PASS", "PASS")`, frozen, unmodified — base EXP-029 + recent-period EXP-030, no conflict between the two) |
| Supplementary evidence | Second-half 5-year robustness run (EXP-031): **REJECT**, HIGH evidence quality (82.4/100) — does not alter the official verdict, per the same rule already established for Cross-Sectional Momentum (SW-006) |
| Evidence Quality | HIGH (90.0/100) base, MODERATE (52.9/100) recent-period, HIGH (82.4/100) robustness |
| Deployment Status | RESEARCH (no auto-promotion; this doc does not recommend PAPER_TRADING) |

## The central methodological difference from every prior strategy

**This is the first strategy in this program whose ACCEPTANCE VERDICT was computed from execution-realism-adjusted trades, not a zero-cost, same-day-close backtest.** Every number in this document is reported TWICE: as the **execution-realistic** figure (used for the verdict) and as a **diagnostic zero-cost baseline** (computed and saved for transparency, explicitly NOT used for any decision). Configuration, fixed before any run and never tuned to this strategy's own results:
- **5%** position-sizing cap relative to each stock's own trailing 20-day average volume.
- **ILLIQ-derived slippage cost**, calibrated once via a disclosed anchor (10bps one-way cost for a median-ILLIQ universe stock at a representative ₹100,000 trade), not fit to real spread data (none exists in this platform).
- **Next-day-open fills**, promoting `execution_realism_study.md`'s (2026-08-05) own already-validated methodology into a reusable engine parameter.

See `swing_research/execution_realism_framework_proposal.md` (approved 2026-08-15) and `swing_research/execution_realism_engine.py` for the full design and its own validation against SW-003/SW-008 before ever being applied here.

## Documented Rules vs. Implementation Assumptions

**Documented:** ILLIQ = mean(|daily return| / daily rupee volume) over a formation period; the paper's headline measure uses the prior year, re-estimated annually; cross-sectional decile sort; long the top decile (most illiquid) in the paper's own long-side framing (the underlying test is a return-predictability regression, not a literal decile-sort rule).

**Disclosed implementation assumptions:**
- **Long only**: standard, disclosed reduction.
- **252-trading-day formation** — the paper's OWN preferred window, kept UNCHANGED (unlike Betting Against Beta's beta lookback, this window fits the frozen 3-year recent-period check without shortening).
- **Close × Volume rupee-volume proxy** — no intraday VWAP available; standard practice in the empirical liquidity literature itself.
- **Single-vintage monthly reformation** (21-day hold) — same structural deviation as every prior cross-sectional strategy.
- **Top-decile threshold (percentile ≥90)**, 8% stop-loss, 1% risk-per-unit sizing — not in the original methodology.
- **Regression-to-decile-sort translation** — a genuine interpretive step beyond the paper's own literal (regression) test design, a bigger fidelity gap than strategies whose source paper already IS a portfolio-construction design.

---

## 2. Results — Execution-Realistic (used for the verdict) vs. Diagnostic Zero-Cost (NOT used)

### Base run (full 457-symbol universe, 2016-08-16 to 2026-08-14, EXP-029)

| Metric | Execution-Realistic | Diagnostic Zero-Cost | Delta |
|---|---|---|---|
| Trades | 269 | 269 | — |
| CAGR | 4.31% | 5.36% | -1.05pp |
| Sharpe | 0.44 | 0.601 | -0.161 |
| Sortino | 0.292 | 0.79 | **-0.498** |
| Max Drawdown | **25.28%** | 18.11% | **+7.17pp worse** |
| Profit Factor | 1.30 | 1.44 | -0.14 |
| Expectancy | ₹195.04 | ₹254.76 | -₹59.72 |

**Walk-forward audit**: 278 total trades, out-of-sample holdout 101 trades, out-of-sample expectancy +₹522.76/trade (positive). **Window consistency: exactly 50%** — the weakest of any strategy in this program (every prior PASS-verdict strategy hit 100%). The frozen Auditor's threshold is ≥50%, so this technically passes, but it is a marginal pass, not a comfortable one — worth stating plainly rather than folding into "PASS" without qualification.

### Recent-period check (most recent 3 years, 2/3 windows used, EXP-030)

| Metric | Execution-Realistic | Diagnostic Zero-Cost | Delta |
|---|---|---|---|
| Trades | 46 | 46 | — |
| CAGR | 4.24% | 3.60% | +0.64pp |
| Sharpe | 0.474 | 0.415 | +0.059 |
| Sortino | 0.488 | 0.49 | -0.002 |
| Max Drawdown | 12.5% | 14.0% | -1.5pp (improved) |
| Profit Factor | 1.61 | 1.50 | +0.11 |

Here execution realism nets out **slightly favorable** — a reminder from the SW-003/SW-008 validation that these three adjustments don't move uniformly in one direction; the net effect depends on the specific trades in the specific period. Window consistency: 100% (1/1 non-OOS window positive). Evidence quality only MODERATE (52.9/100) — a smaller sample than the base run, expected given the recency window.

**Official verdict**: `determine_acceptance_verdict("PASS", "PASS")` = **PASS**.

### Supplementary robustness (second-half 5-year sub-period, 2021-08-15 to 2026-08-14, 3 windows, EXP-031 — additional evidence only, does not itself alter the verdict above)

| Metric | Execution-Realistic | Diagnostic Zero-Cost | Delta |
|---|---|---|---|
| Trades | 125 | 125 | — |
| CAGR | 10.37% | 11.23% | -0.86pp |
| Sharpe | 0.858 | 1.029 | -0.171 |
| Sortino | 0.531 | 1.517 | **-0.986** |
| Max Drawdown | 13.24% | 13.81% | -0.57pp |
| Profit Factor | 1.74 | 1.94 | -0.20 |

**Verdict: REJECT.** Out-of-sample expectancy is **-₹148.31/trade** (execution-realistic) — negative on the true holdout, despite good-looking aggregate numbers over the sub-period (window consistency 100%, both non-OOS windows positive). Evidence quality HIGH (82.4/100) — this is not a thin, low-confidence result.

---

## 3. The Central Tension (read this before any deployment decision)

The official Research Verdict is PASS, computed correctly per the frozen, unmodified `determine_acceptance_verdict()` — base PASS and recent-period PASS never even look at supplementary robustness evidence when they already agree, by design (the INCONCLUSIVE mechanism only activates when the two OFFICIAL checks conflict). That design is sound and was not violated here.

But this is functionally the same situation Cross-Sectional Momentum (SW-006) already established a precedent for: **an official PASS coexisting with a real, HIGH-evidence-quality supplementary REJECT** — and it did NOT get auto-promoted to paper trading; a human decision held it at RESEARCH pending further judgment. Three additional factors make Amihud's case, if anything, warrant more caution than CSM's:

1. **The base run's own window consistency (50%) is the weakest of any strategy tested in this program** — even before considering the robustness REJECT, the base PASS itself is the least comfortable PASS on record.
2. **The robustness REJECT is driven by negative out-of-sample expectancy on 125 execution-realistic trades, HIGH evidence quality** — not a thin, easily-dismissed sample.
3. **This is the strategy where execution-realism modeling matters most by construction** (it selects directly on illiquidity) — and even under the fixed, disclosed, non-tuned configuration, the execution-realistic numbers are consistently weaker than the zero-cost diagnostic across all three runs' Sortino and (in two of three) Max Drawdown. A stricter, more realistic cost model (something this platform still cannot build — no real spread/order-book data exists) would very plausibly weaken this further, not strengthen it.

**This document does not recommend PAPER_TRADING.** Deployment Status remains RESEARCH, unchanged, pending an explicit decision — the same discipline already applied to CSM.

---

## 4. Comparison Against Every Strategy Researched So Far (execution-realistic figures for SW-010)

| Metric | SW-001 Turtle | SW-003 52-Week High | SW-006 Cross-Sectional Mom. | SW-008 Short-Term Reversal | SW-009 Betting Against Beta | **SW-010 Amihud (exec-realistic)** |
|---|---|---|---|---|---|---|
| Research Verdict | REJECT | PASS | PASS (w/ conflicting robustness) | PASS | REJECT | **PASS (w/ conflicting robustness)** |
| CAGR | 28.87% | 22.8% | 20.67% | 21.98% | 10.77% | **4.31%** |
| Sharpe | 0.726 | 1.058 | 1.046 | 1.135 | 0.806 | **0.44** |
| Max Drawdown | 31.01% | 30.8% | 32.79% | 45.58% | 36.17% | **25.28%** |
| Profit Factor | 2.13 | 2.30 | 1.70 | 1.37 | 1.32 | **1.30** |
| Evidence Quality | — | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | **HIGH (90)** |

**Where Amihud fits**: the lowest CAGR, Sharpe, and profit factor of any strategy tested in this program under its execution-realistic (verdict-basis) numbers — but also the shallowest Max Drawdown of any PASS-verdict strategy. Its diagnostic zero-cost numbers (CAGR 5.36%, Sharpe 0.601) would still be the weakest headline returns in the program even before the execution-realism discount. This is structurally similar to CSM: an official PASS carrying real, disclosed evidentiary tension, not a clean result like SW-003 or SW-008.

---

## 5. Sector, Regime, and Holding-Period Notes (base run, EXP-029, execution-realistic)

- **Best sectors**: Capital Goods (+₹27,461), Financial Services (+₹11,446), Healthcare (+₹8,216).
- **Worst sectors**: Telecommunication (-₹8,220), Automobile and Auto Components (-₹4,818), Diversified (-₹2,523).
- **Regime**: works in both bullish (+₹42,752) and bearish (+₹9,714) conditions, more productive in bullish periods — consistent with every other strategy in this program.
- **Average holding period**: 22.3 days, close to the 21-day time-stop by design (single-vintage, monthly reformation).
- **Exposure**: 2.04% of capital (base run) — the lowest of any strategy in this program, consistent with the 5% ADV cap and the relatively rare occurrence of genuinely top-decile-illiquid, otherwise-tradeable Nifty 500 names.

---

## 6. Final Assessment

**Research Verdict: PASS**, correctly computed by the frozen, unmodified acceptance framework. This document does not dispute that computation — it is accurate to what `acceptance_criteria.py` is designed to measure (two independent checks, both positive, no conflict between them).

**Recommendation: hold at RESEARCH, do not promote to PAPER_TRADING without further explicit review**, for the reasons in Section 3. This mirrors Cross-Sectional Momentum's own precedent (PASS + conflicting supplementary evidence → human HOLD decision, not automatic promotion), reinforced here by a weaker base-run window consistency than any prior PASS and by this being the one strategy where execution-realism modeling — still necessarily approximate, given no real market-impact data exists in this platform — has the most direct bearing on whether the edge is real.

Research Verdict and Deployment Status remain fully independent — this PASS does not itself change Deployment Status; that remains a separate, explicit decision, still pending.
