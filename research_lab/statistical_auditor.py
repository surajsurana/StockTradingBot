"""
Statistical Auditor -- final, deterministic authority on every experiment.
No hypothesis reaches a PASS verdict without going through audit() here,
and nothing any LLM call in the pipeline produces (Quant Researcher's
enthusiasm, the Research Director's ranking, Performance Analyst's
narrative) can override this. Pure rule-based code, no Claude calls.

Three checks, any one failing is enough to REJECT:
1. Sample size -- too few trades and none of the other numbers can be
   trusted, no matter how good they look.
2. Walk-forward consistency -- a real edge should show up as positive
   expectancy across MOST sequential time windows, not just one lucky one.
   This is the exact discipline missing from the earlier ad hoc ORB
   tuning, where a single 6-month window's "improvement" (target_multiple
   0.8: +0.16%) didn't hold on the most recent 60 days (-2.64%).
3. Genuine out-of-sample performance -- the final holdout window (never
   touched while picking/tuning anything) must itself show positive
   expectancy. This is the one check that most directly answers "does
   this actually work on data the hypothesis wasn't shaped around."
"""

from dataclasses import dataclass, field


@dataclass
class AuditVerdict:
    decision: str   # "PASS" or "REJECT"
    reasoning: str
    checks: dict = field(default_factory=dict)


def audit(walk_forward_metrics: list, out_of_sample_metrics: dict,
          min_trades_total: int = 30, min_out_of_sample_trades: int = 5,
          min_consistent_window_fraction: float = 0.6) -> AuditVerdict:
    """
    walk_forward_metrics: list of compute_metrics()-shaped dicts, one per
      walk-forward window EXCLUDING the final out-of-sample holdout.
    out_of_sample_metrics: compute_metrics()-shaped dict for the holdout
      window alone, never touched during hypothesis selection or any
      parameter choice.
    """
    checks = {}
    reasons = []

    total_trades = sum(m.get("total_trades", 0) for m in walk_forward_metrics)
    total_trades += out_of_sample_metrics.get("total_trades", 0)
    checks["total_trades"] = total_trades
    if total_trades < min_trades_total:
        reasons.append(f"Only {total_trades} total trades across all windows -- below the "
                        f"minimum of {min_trades_total} needed for any of these numbers to be "
                        f"statistically meaningful.")

    oos_trades = out_of_sample_metrics.get("total_trades", 0)
    checks["out_of_sample_trades"] = oos_trades
    if oos_trades < min_out_of_sample_trades:
        reasons.append(f"Out-of-sample holdout only has {oos_trades} trade(s) -- below the "
                        f"minimum of {min_out_of_sample_trades}, too few to validate anything on.")

    windows_with_data = [m for m in walk_forward_metrics if m.get("total_trades", 0) > 0]
    positive_windows = [m for m in windows_with_data if m.get("expectancy", 0) > 0]
    consistency_fraction = (len(positive_windows) / len(windows_with_data)) if windows_with_data else 0.0
    checks["walk_forward_consistency_fraction"] = round(consistency_fraction, 2)
    if windows_with_data and consistency_fraction < min_consistent_window_fraction:
        reasons.append(f"Only {len(positive_windows)}/{len(windows_with_data)} walk-forward "
                        f"windows ({consistency_fraction:.0%}) showed positive expectancy -- "
                        f"below the {min_consistent_window_fraction:.0%} needed to call this a "
                        f"consistent edge rather than one lucky window (the exact trap the "
                        f"earlier ORB target-multiple tuning fell into: SEED-ORB-2).")

    oos_expectancy = out_of_sample_metrics.get("expectancy", 0)
    checks["out_of_sample_expectancy"] = oos_expectancy
    if oos_trades > 0 and oos_expectancy <= 0:
        reasons.append(f"Out-of-sample expectancy is {oos_expectancy:+.2f} per trade -- not "
                        f"positive on genuinely unseen data, which is the most important single "
                        f"check. Good in-sample/walk-forward numbers do not override this.")

    if reasons:
        return AuditVerdict(decision="REJECT", reasoning=" ".join(reasons), checks=checks)

    return AuditVerdict(
        decision="PASS",
        reasoning=(f"Passed all checks: {total_trades} total trades, {consistency_fraction:.0%} "
                   f"of walk-forward windows showed positive expectancy, and the out-of-sample "
                   f"holdout ({oos_trades} trades) had positive expectancy of "
                   f"{oos_expectancy:+.2f} per trade."),
        checks=checks,
    )
