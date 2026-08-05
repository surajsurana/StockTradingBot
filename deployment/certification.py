"""
Production Strategy Certification (Phase 3) -- runs MA Crossover and Mean
Reversion (strategies/ma_crossover.py, strategies/mean_reversion.py) THE
STRATEGIES THEMSELVES, completely unmodified -- through the SAME frozen
acceptance/evidence-quality pipeline every published research strategy
goes through, so they exist as first-class, audited entries in the
Strategy Library rather than only ever appearing as benchmark rows.

This is NEW ORCHESTRATION CODE, confirmed within the frozen-framework
boundary (2026-08-04 direction): swing_research/acceptance_criteria.py,
evidence_quality.py, and cross_strategy_review.py are reused BY IMPORT,
UNMODIFIED. What's new here is a walk-forward wrapper around
swing_research.backtesting_engine.simulate_portfolio_single_unit() (MA
Crossover / Mean Reversion's own native engine -- they don't use the
swing_research.base.Strategy precompute interface Turtle/Minervini/52-Week
High use, so run_walk_forward_generic() in swing_research/research_director.py
doesn't fit them; a parallel wrapper is required).

GOVERNANCE (explicit, 2026-08-04): a REJECT or INCONCLUSIVE certification
verdict for a strategy that is ALREADY LIVE in production NEVER
automatically changes anything about production. Research Verdict
(recorded here) and Deployment Status (deployment_manager.py, set only by
an explicit separate call) are permanently independent. This module
NEVER imports deployment_manager and never sets a deployment status --
that stays a deliberate, separate human action.

ISOLATION: imports strategies/ma_crossover.py and strategies/mean_reversion.py
ONLY to read their (unmodified) Strategy classes and pass them to
simulate_portfolio_single_unit() -- exactly the same read-only import
research_director.py already does for benchmarking. Never imports
execution/, cio/, risk/, portfolio/, or config/settings.py. Never modifies
the two strategy files.
"""

from datetime import date
from typing import Optional

from research_lab import experiment_manager, statistical_auditor

from swing_research.backtesting_engine import simulate_portfolio_single_unit
from swing_research.evidence_quality import compute_evidence_quality
from swing_research.metrics import compute_metrics

from deployment.settings import CERTIFICATION_EXPERIMENTS_DIR, CERTIFICATION_KNOWLEDGE_BASE_PATH


def run_walk_forward_single_unit(strategy_factory, data: dict, starting_capital: float,
                                  start_date: date, end_date: date, n_walk_forward_windows: int = 3,
                                  regime_series=None, min_trades_total: int = 15,
                                  min_out_of_sample_trades: int = 3,
                                  min_consistent_window_fraction: float = 0.5) -> dict:
    """
    Walk-forward + audit for a strategy_factory-based (simulate_portfolio_single_unit)
    strategy -- the parallel of swing_research.research_director.run_walk_forward_generic(),
    for strategies that don't use the precompute-based Strategy interface.
    Same date-windowing (research_lab.backtesting_engineer.walk_forward_split(),
    imported unmodified) and same Statistical Auditor (research_lab.statistical_auditor.audit(),
    imported unmodified) as every other walk-forward check in this program.
    """
    from research_lab.backtesting_engineer import walk_forward_split

    windows = walk_forward_split(start_date, end_date, n_walk_forward_windows)
    walk_forward_metrics = []
    all_trades_by_window = []

    for w_start, w_end in windows:
        windowed_data = {
            sym: df[(df.index.date >= w_start) & (df.index.date <= w_end)]
            for sym, df in data.items()
        }
        result = simulate_portfolio_single_unit(windowed_data, strategy_factory, starting_capital,
                                                  regime_series=regime_series)
        metrics = compute_metrics(result["trades"], starting_capital, result["trading_calendar"],
                                   daily_equity=result["daily_equity"])
        walk_forward_metrics.append(metrics)
        all_trades_by_window.append(result["trades"])

    out_of_sample_metrics = walk_forward_metrics[-1] if walk_forward_metrics else {}
    consistency_metrics = walk_forward_metrics[:-1]

    verdict = statistical_auditor.audit(
        consistency_metrics, out_of_sample_metrics,
        min_trades_total=min_trades_total, min_out_of_sample_trades=min_out_of_sample_trades,
        min_consistent_window_fraction=min_consistent_window_fraction,
    )

    return {"verdict": verdict, "walk_forward_metrics": consistency_metrics,
            "out_of_sample_metrics": out_of_sample_metrics, "windows": windows}


def run_certification_experiment(strategy_key: str, display_name: str, strategy_factory, data: dict,
                                  start_date: date, end_date: date, starting_capital: float = 1_000_000,
                                  n_walk_forward_windows: int = 3, min_lookback_days: int = 0,
                                  experiments_dir: str = CERTIFICATION_EXPERIMENTS_DIR) -> dict:
    """
    Full certification pipeline for one production strategy: walk-forward +
    audit, evidence quality, saved as a permanent experiment record (SAME
    research_lab.experiment_manager.save_experiment() every other
    experiment in this program uses, unmodified, just a dedicated
    directory to keep certification runs visually distinct from published-
    research experiments).

    Returns {"exp_id":..., "verdict": AuditVerdict, "evidence_quality": {...}}.
    Does NOT call determine_acceptance_verdict() itself (that needs a
    SEPARATE recent-period-only run, same two-run requirement as every
    published strategy -- call this function twice, once for the full
    period and once for a recent-period slice, exactly like
    swing_research.acceptance_criteria.run_recent_period_check()'s pattern,
    then combine with swing_research.acceptance_criteria.determine_acceptance_verdict()
    at the call site).
    """
    result = run_walk_forward_single_unit(strategy_factory, data, starting_capital, start_date, end_date,
                                           n_walk_forward_windows)
    verdict = result["verdict"]
    calendar_all = sorted({d for df in data.values() for d in df.index.date})

    evidence_quality = compute_evidence_quality(
        total_trades=verdict.checks.get("total_trades", 0),
        out_of_sample_trades=verdict.checks.get("out_of_sample_trades", 0),
        windows_used=n_walk_forward_windows, available_trading_days=len(calendar_all),
        min_lookback_days=min_lookback_days,
    )

    exp_id = experiment_manager.next_experiment_id(experiments_dir)
    experiment_manager.save_experiment(
        exp_id=exp_id,
        hypothesis={
            "name": f"{display_name} (Production Strategy Certification)",
            "mechanism": "Existing live production strategy, unmodified -- certified for the first "
                         "time under the same frozen research pipeline every published strategy uses.",
            "rationale": "Phase 3 of the Research Deployment program (2026-08-04): production "
                         "strategies previously only appeared as benchmark rows, never independently "
                         "audited on their own terms.",
            "rules": "See strategies/ma_crossover.py or strategies/mean_reversion.py (unmodified, "
                     "read-only) for the actual live production logic being certified here.",
            "selection_reasoning": "Already-live production strategy, certified per explicit direction.",
        },
        parameters={
            "starting_capital": starting_capital, "n_walk_forward_windows": n_walk_forward_windows,
            "min_lookback_days": min_lookback_days,
            "engine": "swing_research.backtesting_engine.simulate_portfolio_single_unit "
                      "(shared-capital-pool, production-risk-parameter-based -- the same engine "
                      "already used to benchmark this strategy against published research strategies)",
            "transaction_costs_modeled": False, "slippage_modeled": False,
        },
        data_period=f"{start_date} to {end_date}",
        metrics={
            "walk_forward_metrics": result["walk_forward_metrics"],
            "out_of_sample_metrics": result["out_of_sample_metrics"], "audit_checks": verdict.checks,
            "evidence_quality": evidence_quality,
        },
        observations=(
            f"Certification run for {display_name}, an existing live production strategy. "
            f"Verdict: {verdict.decision}. {verdict.reasoning} "
            f"This verdict is research evidence only -- per the Research Deployment program's "
            f"standing governance rule, it NEVER automatically changes this strategy's production "
            f"deployment status. Any deployment action requires a separate, explicit human decision."
        ),
        verdict={"decision": verdict.decision, "reasoning": verdict.reasoning},
        experiments_dir=experiments_dir,
        knowledge_base_path=CERTIFICATION_KNOWLEDGE_BASE_PATH,
    )

    return {"exp_id": exp_id, "verdict": verdict, "evidence_quality": evidence_quality}
