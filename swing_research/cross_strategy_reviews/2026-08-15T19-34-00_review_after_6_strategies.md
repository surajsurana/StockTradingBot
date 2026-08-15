# Cross-Strategy Research Review

Generated: 2026-08-15T19-34-00

Strategies covered by this review: CROSS-SECTIONAL MOMENTUM, POST-EARNINGS ANNOUNCEMENT DRIFT (PEAD, SW-007), SHORT-TERM REVERSAL (SW-008)

---

# Cross-Strategy Research Review

*Scope note: this review covers Cross-Sectional Momentum (SW-006-equivalent), PEAD (SW-007, deferred), and Short-Term Reversal (SW-008). No cross-strategy review is formally due yet per the 3-strategy cadence, but the three conclusions are synthesized here as requested.*

## 1. Common Patterns Across Strategies Tested

**Post-COVID regime sensitivity is a recurring fault line, not a universal one.** Cross-Sectional Momentum passes on base and recent-period runs but REJECTs specifically on the 2020-onward supplementary window (EXP-018, -INR54.02/trade). Short-Term Reversal is the counter-example: it PASSes cleanly on the identical supplementary check (EXP-022, +INR151.55/trade). This shows the post-COVID robustness run is doing genuine discriminating work — it is not a rubber-stamp, and momentum-style (winner-buying) signals appear more exposed to this regime than mean-reversion (loser-buying) signals, at least in this pairwise comparison.

**Momentum-family strategies keep coming back weaker than the incumbent, but not redundant.** Cross-Sectional Momentum underperforms 52-Week High Momentum on every headline metric (CAGR, Sharpe, MaxDD, profit factor, win rate, expectancy) at both horizons, yet sector-level analysis shows genuine behavioral divergence (Financial Services and Realty behave oppositely across the two). The pattern recurring here: raw-metric comparison alone would over-state redundancy between same-family strategies; sector/correlation breakdown is necessary to catch true diversification value (or lack of it).

**Data availability, not signal quality, is now the binding constraint for the next tier of strategies.** PEAD's deferral is a pure infrastructure gap (yfinance caps earnings history at ~4-5 quarters vs. ~8-10 years needed) rather than a research finding. This is the first time in the program a strategy has been blocked before backtesting rather than failed/passed after it.

**Single-vintage and lookback-length disclosed deviations continue to hold up mechanically.** Both Cross-Sectional Momentum (126-day) and Short-Term Reversal (21-day) used single-vintage entry per prior precedent (52-Week High Momentum), and in both cases window consistency stayed at 100%, with the 21-day lookback so light it never triggered the strategy-aware windowing reduction that longer-lookback strategies (Minervini) have needed. This suggests the reduction mechanism is lookback-length-sensitive and behaving as designed rather than being a fixed artifact.

## 2. Recurring Strengths / Weaknesses

**Strength of the program's methodology:** the base/recent-period/post-COVID three-tier structure is now demonstrably capable of producing divergent, non-rubber-stamped outcomes (PASS/PASS/REJECT for Cross-Sectional Momentum vs. PASS/PASS/PASS for Short-Term Reversal), and the acceptance-verdict logic explicitly treats supplementary REJECTs as informative-but-non-overriding rather than forcing an artificial INCONCLUSIVE — this is a designed, disclosed behavior, not an inconsistency.

**Weakness/tension:** a strategy can now be "Research Verdict: PASS" while carrying a real, evidence-quality-HIGH REJECT finding (Cross-Sectional Momentum) and simultaneously be paper-trading-paused pending human diversification judgment — meaning "PASS" alone is no longer sufficient information for a deployment reader; the underlying supplementary evidence and sector comparison have to be read in full. This is a direct consequence of the 2026-08-04 governance change decoupling PASS from automatic registration, and it is working as intended (Short-Term Reversal likewise gets only a deployment *recommendation*, not automatic registration).

**Strength:** correlation/diversification analysis is becoming more rigorous over time — Short-Term Reversal's conclusion goes beyond a correlation number and ties it back to mechanism (1-month loser-buying vs. 6-month winner-buying), matching the sector-level reasoning used for Cross-Sectional Momentum. This is a recurring good practice, not a one-off.

**Recurring data ceiling:** yfinance is now confirmed (via PEAD) to be insufficient for any strategy requiring long fundamental/earnings history, whereas it has been adequate for all price-based signals tested so far (momentum, reversal). This is a structural limitation of the current single-data-source setup, evidenced concretely rather than assumed.

## 3. Implications for the Research Framework

**Supported by evidence — no change needed:** The acceptance-criteria logic (`determine_acceptance_verdict`) and the independence of Research Verdict from Deployment Status are functioning as designed across both strategies (Cross-Sectional Momentum's PASS-despite-REJECT and Short-Term Reversal's PASS-with-recommendation-only). No framework change is indicated here; the mechanism is producing differentiated, defensible outcomes.

**Supported by evidence — one concrete implication:** Since a same-family strategy (Cross-Sectional Momentum) can lose on every raw headline metric against an existing paper-traded strategy (52-Week High Momentum) yet still show genuine sector-level diversification, benchmark comparison in this program should continue to require the sector/correlation breakdown as a mandatory component whenever a new strategy shares a source family or mechanism with an already-registered strategy — a raw-metrics-only comparison would have understated Cross-Sectional Momentum's differentiation and could similarly mislead for future same-family strategies (e.g. any additional momentum or reversal variant added later).

**Supported by evidence — data-source scoping:** PEAD's deferral demonstrates the program's data pipeline (yfinance-only) has a documented hard ceiling for fundamentals-dependent strategies (~1 year of history vs. ~8-10 years required). This is not a framework flaw but a scoping fact worth carrying forward explicitly: any future strategy proposal resting on fundamental/analyst-consensus history should be pre-screened for data availability before entering the experiment pipeline, the same way PEAD was, rather than discovered mid-backtest.

**Not yet supported — flagged only:** With only one REJECT-carrying PASS (Cross-Sectional Momentum) and one clean PASS (Short-Term Reversal) among momentum/reversal strategies, there isn't yet enough volume to conclude that post-COVID robustness checks systematically penalize winner-momentum vs. loser-reversal signals as a class; this is a pattern worth re-examining at the next due cross-strategy review (after 3 more completed strategies) rather than acting on now.