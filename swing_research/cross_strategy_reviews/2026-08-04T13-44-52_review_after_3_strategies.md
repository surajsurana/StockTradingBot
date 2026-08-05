# Cross-Strategy Research Review

Generated: 2026-08-04T13-44-52

Strategies covered by this review: TURTLE TRADING -- SYSTEM 2 (DONCHIAN CHANNEL BREAKOUT), MINERVINI TREND TEMPLATE FILTER, 52-WEEK HIGH MOMENTUM

---

# Cross-Strategy Research Review
### (Covering EXP-001 through EXP-014: Turtle System 2, Minervini Trend Template, 52-Week High Momentum)

---

## 1. Common Patterns Across Strategies Tested So Far

**All three strategies pass on the full-period base run, and outperform live production strategies on that basis.** Turtle (CAGR 28.87%, Sharpe 0.73), Minervini (CAGR 24.7%, Sharpe 1.038), and 52-Week High (CAGR 22.8%, Sharpe 1.058) all beat both live strategies and Buy & Hold on the full 2016-2026 universe. This is a recurring pattern, not an isolated result — a decade-long full-period backtest on this universe appears to reward these rule-based, long-only trend/momentum approaches fairly consistently.

**Recency is the recurring fault line.** In every strategy where full-period and recent-period results have been compared, they diverge or come under strain:
- Turtle: full-period PASS but 2021-2026 REJECT (-Rs.113.67/trade OOS expectancy), corroborated by an independent robustness check (EXP-003–006).
- Minervini: full-period PASS but official recent-period REJECT (EXP-009), directly contradicted by an independent 2021-2026 robustness PASS (EXP-011) — the strengths conflict rather than agree, but the *tension* is still concentrated in the same recent window.
- 52-Week High: the only strategy so far where the recent-period check is a clean, uncontested PASS (EXP-014) — but even here the recent-period evidence quality (62.6/100) is markedly weaker than the base run (90.0/100), and the strategy is the *first* to avoid a recency problem, implying the other two are not idiosyncratic outliers.

This establishes recency sensitivity as the single most important recurring axis of failure/uncertainty in the program so far — three-for-three strategies have had their headline full-period PASS meaningfully qualified or contradicted by what happens in 2021-2026.

**Single-window fragility recurs as a methodological artifact, not just a Minervini quirk.** Minervini's official REJECT rested on the sign of one non-holdout window (-INR141.90/trade, EXP-009), a direct consequence of its 252-day lookback starving the 3-year recent slice down to 2 windows before the fix, and then just 1 usable consistency window after it. This is the same class of problem the Turtle cycle flagged generically (recent-period checks needing to be mandatory) and is a distinct, separate fragility: *how many windows survive* a fixed-length recent check, not just *what the aggregate expectancy is*.

**Disclosed, deliberate scope deviations from source methodology are the norm, not the exception.** Turtle disclosed long-only reduction from a long/short futures system; Minervini's own construction was implemented as specified without deviation issues raised in its summary; 52-Week High disclosed four deviations from George & Hwang (2004), including a MODERATE-to-MATERIAL single-vintage-vs-overlapping-portfolio change. Every strategy so far has required the reviewer to explicitly weigh disclosed implementation gaps against the source methodology — this is a structural feature of the program, not a one-off.

---

## 2. Recurring Strengths / Weaknesses

**Strength — the mandatory recent-period check is doing real work.** It was the direct cause of catching Turtle's fragility and Minervini's conflict; without it both would have been base-run PASSes straight to paper trading eligibility. Its value is now demonstrated three-for-three (it fired substantively in Turtle and Minervini, and even in the 52-Week High "clean" case it surfaced a real evidence-quality drop). This is a strength of the *framework*, not the *strategies*.

**Strength — the program catches and fixes its own bugs before they contaminate verdicts.** Three distinct framework bugs were found and fixed across these cycles: the shared-capital-pool/benchmark-fairness bug and symbol-format sector-mapping bug (Turtle cycle), and the windowing-granularity bug tied to lookback length (Minervini cycle, EXP-009), fixed generically via `Strategy.min_lookback_days` before it could produce a false REJECT for future long-lookback strategies. In both cases, fixes were verified against the full test suite before being applied to the strategy's own result — no cycle has yet let framework debt bleed into a verdict silently.

**Weakness — recent-period checks are structurally starved of evidence relative to full-period runs.** Minervini's recent check dropped to 117 trades / effectively 1 consistency window versus 574 walk-forward trades in the base run; 52-Week High's recent check dropped to 74 trades and 62.6/100 evidence quality versus 311 trades and 90.0/100 in the base run. Every recent-period check so far has been evidence-thinner than its corresponding base run — an inherent tension between "test the period that matters most for deployment" and "have enough data to trust the test."

**Weakness/strength (framework maturity) — the program has needed a third verdict category to describe reality honestly.** The introduction of INCONCLUSIVE (Minervini) shows the original binary PASS/REJECT framework was insufficiently expressive for a case where two valid, independent robustness studies disagree. That this was needed on only the second strategy evaluated suggests conflicting recent-period evidence is not a rare edge case but something the framework should expect to encounter again.

---

## 3. Implications for the Research Framework

- **The mandatory recent-period check (introduced after Turtle) is validated by subsequent use and should remain a permanent, non-optional acceptance-criteria gate.** It changed or qualified the verdict for both subsequent strategies (Minervini: PASS→INCONCLUSIVE; 52-Week High: confirmed as genuinely clean rather than assumed). No evidence here suggests loosening it.

- **`Strategy.min_lookback_days` (introduced after Minervini/EXP-009) should be checked against Turtle and any other already-completed strategy for retroactive windowing adequacy**, since the bug it fixed was generic (any strategy with a heavy lookback could silently lose windows in a fixed-length recent slice) and Turtle's own recent-period check (EXP-005/006) predates this fix.

- **Evidence-quality scoring is behaving informatively, not just decoratively** — it correctly flagged the base-run-vs-recent-period quality gap in 52-Week High (90.0 vs 62.6) even where both checks PASSed, and this gap tracks the same window-count/trade-count thinness that caused Minervini's REJECT. This supports continuing to report evidence quality alongside verdicts even on clean PASSes, since a PASS with low evidence quality (52-Week High's recent check) is informationally different from a PASS with high evidence quality (its base run), and the framework already distinguishes them.

- **The INCONCLUSIVE verdict's decision rule (base PASS + recent REJECT + disclosed conflicting independent robustness evidence) has only been exercised once; its boundary conditions remain untested** — e.g., it is not yet established how the framework should treat a case with conflicting evidence but no independent robustness study run at all (52-Week High's plan explicitly reserved that check for "close/ambiguous or REJECT results" and skipped it here since none applied). This is not a flaw shown by the evidence, but the evidence to date only exercises one path through the three-verdict logic — the other paths (e.g., recent-period PASS contradicted by an independent REJECT) remain unexercised.