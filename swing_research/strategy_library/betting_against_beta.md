# Strategy Library: Betting Against Beta (SW-009)

**Status: Research complete. REJECTED for paper trading (temporal robustness failure, corroborated by an independent robustness check).**

---

## 1. GENERAL

| | |
|---|---|
| Strategy ID | SW-009 |
| Strategy Name | Betting Against Beta (Low-Beta Anomaly) |
| Research Source | Frazzini, A. and Pedersen, L.H. (2014), "Betting Against Beta," *The Journal of Financial Economics*, Vol. 111, No. 1 |
| Research Verdict | **REJECT** (base EXP-024 PASS + recent-period EXP-025 REJECT, no conflicting evidence — the supplementary robustness run EXP-026 corroborates rather than conflicts) |
| Evidence Quality | HIGH (90.0/100) base, HIGH (78.2/100) recent-period, HIGH (90.0/100) robustness |
| Deployment Status | RESEARCH (Research Verdict and Deployment Status are always independent — this REJECT does not itself archive the strategy; that remains a separate, explicit decision) |

## Original Publication

Frazzini, A. and Pedersen, L.H. (2014), "Betting Against Beta," *The Journal of Financial Economics*, Vol. 111, No. 1. First **risk-based** (not price-pattern-based) mechanism tested in this program: leverage-constrained investors overpay for high-beta stocks to get leveraged-like market exposure without borrowing, compressing high-beta expected returns and leaving low-beta stocks underpriced relative to their risk.

## Documented Rules vs. Implementation Assumptions

**Documented:** the BAB factor is a beta-neutral long-short construction — long `(1/β_L) × [low-beta portfolio]`, short `(1/β_H) × [high-beta portfolio]`, each leg rescaled to its own beta of exactly 1 at formation, funded at the risk-free rate. Beta itself is the paper's own specific estimator (not a plain OLS regression beta): `β = ρ̂ × (σ̂ᵢ/σ̂ₘ)`, ρ̂ from overlapping 3-day log returns over a 1-year window, σ̂ from 1-day log returns over a 5-year (minimum 3-year) window, shrunk 0.6/0.4 toward the cross-sectional mean of 1.0. Rebalanced monthly.

**Disclosed implementation assumptions** (approved 2026-08-15 — see `swing_research/published_research_analyst.py`'s `BETTING_AGAINST_BETA` record for full reasoning on each):
- **Long only, unlevered**: a larger interpretive gap than every prior strategy's long-only reduction — this measures only the long, unlevered, low-beta stock-selection return, not the paper's own leverage-scaled, beta-neutral factor. LIKELY UNDERSTATES the paper's own headline result.
- **Volatility/correlation lookback shortened from the paper's 5-year (min 3-year) window to 1 year** — the paper's preferred window is structurally incompatible with this program's frozen 3-year recent-period check (`acceptance_criteria.py`, `RECENT_PERIOD_YEARS=3`, never modified); a 3-5yr warm-up would consume the entire recent-period slice with zero days left to trade. MODERATE, DIRECTIONALLY UNKNOWN impact — a real fidelity cost accepted specifically to make the strategy testable under the mandatory recency gate.
- **Correlation estimator kept faithful** to the paper's overlapping 3-day log returns (not simplified) — relevant given NSE's wide liquidity range.
- **Raw daily returns** (not excess-of-risk-free) for beta estimation — no risk-free-rate time series integrated in this platform. Negligible impact at daily frequency.
- **Single-vintage holding**, not continuous monthly rebalancing — same structural deviation as every prior cross-sectional strategy.
- **8% protective stop-loss and 1% position sizing**: not part of the original methodology at all.
- **Bottom-decile threshold (percentile ≤10)** — lowest shrunk beta.

---

## 2. NSE Results

**Base run** (full 457-symbol frozen universe, 2016-08-16 to 2026-08-14, EXP-024): **PASS**. 898 walk-forward trades, 100% window consistency, out-of-sample holdout (338 trades) expectancy +₹207.53/trade. Continuous full-period run: 894 trades, CAGR 10.77%, Sharpe 0.806, Sortino 0.881, Max Drawdown 36.17%, Profit Factor 1.32, Win Rate 46.98%, Expectancy ₹199.15/trade, average holding period 24.8 days, exposure 3.69%. Evidence quality HIGH (90.0/100).

**Recent-period check** (most recent 3 years, 2/3 walk-forward windows used — window count reduced by the strategy's own 255-day `min_lookback_days` via the frozen `_feasible_window_count()`, EXP-025): **REJECT**. Only 0/1 consistency windows (0%) showed positive expectancy — below the 50% threshold needed to call this a consistent edge rather than one lucky window. 209 trades, out-of-sample expectancy +₹33.78/trade (barely positive, but the consistency-window check failed independently of that). CAGR 2.48%, Sharpe 0.308, Max Drawdown 16.92%, Profit Factor 1.10, Win Rate 44.02%. Evidence quality HIGH (78.2/100) — lower than the base run's, reflecting the smaller sample and reduced window count, but still comfortably in the HIGH band.

**Supplementary robustness run** (second-half 5-year sub-period, 2021-08-15 to 2026-08-14, 3 windows, EXP-026 — additional evidence only, does not itself alter the official acceptance verdict): **REJECT**. Out-of-sample expectancy **-₹4.88/trade** — negative on the true out-of-sample holdout despite a healthy-looking overall period (399 trades, 100% walk-forward consistency, CAGR 11.21%, Sharpe 1.17, Profit Factor 1.36). Evidence quality HIGH (90.0/100). This is the same pattern the base run's own strength was resting on: good numbers across most of the window, but the true unseen final slice specifically fails.

**Official acceptance verdict**: `determine_acceptance_verdict("PASS", "REJECT")` = **REJECT** (frozen, unmodified function). Unlike Minervini's case, the robustness run here **corroborates** the recent-period REJECT rather than conflicting with it — no disclosed conflicting evidence exists, so this is a clean REJECT, not INCONCLUSIVE.

---

## 3. Why This Happened (interpretation, not part of the frozen verdict logic)

The pattern mirrors Turtle System 2's own REJECT almost exactly: a full 10-year backtest that looks genuinely strong (HIGH evidence quality, clean window consistency, solid Sharpe/Sortino) is substantially carried by earlier years, and the strategy has measurably stopped producing a reliable edge in the period that matters most for a forward-looking decision. Two independent tests — the recent-period check (last 3 years) and a differently-constructed second-half robustness run (last ~5 years) — both show the true out-of-sample slice turning negative or failing the consistency check, despite reasonable-looking aggregate metrics over their own broader windows. This is exactly the two-part acceptance criteria (`acceptance_criteria.py`, written after the Turtle finding) working as designed: a good full-history PASS alone is not sufficient.

One candidate explanation specific to this strategy: the 1-year beta-estimation lookback (itself an approved deviation from the paper's preferred 5-year window, made to fit this platform's recency gate) is inherently noisier and more regime-reactive than a longer, more stable estimate would be — plausibly making the strategy more sensitive to exactly the kind of short-term regime shift that would degrade its recent performance. This is a genuine, disclosed cost of that adaptation, not a claim that the underlying academic anomaly itself is false — see Assumptions Impact in `published_research_analyst.py`'s record.

---

## 4. Comparison Against Every Strategy Researched So Far

| Metric | SW-001 Turtle | SW-002 Minervini | SW-003 52-Week High | SW-006 Cross-Sectional Mom. | SW-008 Short-Term Reversal | **SW-009 Betting Against Beta** |
|---|---|---|---|---|---|---|
| Research Verdict | REJECT | INCONCLUSIVE | PASS | PASS | PASS | **REJECT** |
| CAGR | 28.87% | 24.7% | 22.8% | 20.67% | 21.98% | **10.77%** |
| Sharpe | 0.726 | 1.038 | 1.058 | 1.046 | 1.135 | **0.806** |
| Sortino | 1.972 | 2.449 | 1.655 | 1.794 | 1.326 | **0.881** |
| Max Drawdown | 31.01% | 32.85% | 30.8% | 32.79% | 45.58% | **36.17%** |
| Profit Factor | 2.13 | 2.13 | 2.30 | 1.70 | 1.37 | **1.32** |
| Win Rate | 38.0% | 28.3% | 36.0% | 28.7% | 46.2% | **46.98%** |
| Expectancy/trade | ₹4,968 | ₹1,231 | ₹2,220 | ₹1,594 | ₹511 | **₹199** |
| Avg Holding Period | 51.7d | 30.8d | 89.1d | 79.3d | 23.1d | **24.8d** |
| Trade Count (base) | 234 | 657 | 306 | 348 | 1,254 | **894** |
| Evidence Quality | — | HIGH (90) | HIGH (90) | HIGH (90) | HIGH (90) | **HIGH (90)** |

**Where Betting Against Beta fits**: highest win rate of any strategy in the program (46.98%), consistent with a low-volatility selection tilt producing more frequent, smaller wins rather than large trend-following payoffs — but the lowest CAGR, Sharpe, and expectancy per trade of any strategy tested, PASS or REJECT. Its REJECT is structurally the same story as Turtle's (full-history PASS undone by a recent-period failure, corroborated by a robustness check), not a weak result across the board — the strategy has genuine merit in its earlier years but no demonstrated edge in the current market regime.

---

## 5. Final Verdict

**REJECTED for paper trading.** Per the Swing Research Program's standing acceptance criteria: no strategy is eligible for paper trading unless it demonstrates positive out-of-sample performance **and** remains robust in the most recent market period specifically. Betting Against Beta satisfies the first condition (base run PASS) but fails the second (recent-period REJECT), and a supplementary robustness check found no conflicting evidence that would justify an INCONCLUSIVE verdict instead — both the recent-period check and an independently-constructed second-half sub-period test show the true out-of-sample slice failing.

No parameter tuning or optimization was attempted or will be attempted on this result, per the research program's mandate. The beta-estimation lookback shortening (5yr/3yr → 1yr) was a disclosed, approved adaptation made *before* any run, to fit this platform's frozen recency gate — not a post-hoc adjustment made in response to this result.

Research Verdict and Deployment Status remain fully independent — this verdict does not itself change Deployment Status; that remains a separate, explicit decision (see Turtle System 2's own precedent, where a REJECT verdict led to an explicit, separate ARCHIVED decision).

## Lessons Learned

1. **A risk-based (not price-pattern-based) signal is not automatically more robust than a momentum/reversal one.** This is the first strategy in the program selecting on estimated systematic risk rather than any price pattern, and it failed the same recency check that price-pattern strategies fail — mechanism family alone doesn't predict robustness.
2. **A necessary methodology adaptation (shortening the beta lookback to fit the platform's recency gate) has a real, disclosed cost, not just an implementation convenience.** The shortened, noisier beta estimate is a plausible contributor to the strategy's recent-period weakness, worth remembering if a future data/infrastructure change ever makes the paper's full 5-year window testable.
3. **The frozen acceptance criteria caught a genuinely different failure mode than Turtle's.** Turtle's REJECT was about a single continuous edge fading over time; this strategy's recent-period check failed a window-*consistency* threshold (0/1 windows positive) even though the raw out-of-sample expectancy for that check was technically still positive (+₹33.78/trade) — a reminder that the Statistical Auditor's checks are genuinely independent of each other, not just restatements of the same signal.
