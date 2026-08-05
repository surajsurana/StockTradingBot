"""
CLI entry point for Production Strategy Certification (Phase 3) --
certifies an existing live production strategy (MA Crossover, Mean
Reversion) under the frozen swing_research/ acceptance pipeline, without
modifying the strategy itself or any production path.

    python run_certification.py --strategy=ma_crossover
    python run_certification.py --strategy=mean_reversion

Runs the base (full-history) certification, then the recent-period-only
certification (same RECENT_PERIOD_YEARS window as every published
strategy), combines them via
swing_research.acceptance_criteria.determine_acceptance_verdict()
(frozen, unmodified), and records the resulting Research Verdict via
deployment.deployment_manager.set_research_verdict() -- an explicit,
separate call, never an automatic side effect of the certification
pipeline itself. Deployment Status is NEVER touched by this script.
"""

import argparse
from datetime import timedelta

from data.fetch_historical import fetch_all
from swing_research.acceptance_criteria import RECENT_PERIOD_YEARS, determine_acceptance_verdict
from swing_research.universe import get_swing_universe

from deployment.certification import run_certification_experiment
from deployment.deployment_manager import get_strategy, register_strategy, set_research_verdict
from deployment.base import ResearchVerdict

_STRATEGIES = {
    "ma_crossover": {"display_name": "MA Crossover", "min_lookback_days": 50},
    "mean_reversion": {"display_name": "Mean Reversion", "min_lookback_days": 20},
}


def _strategy_factory(strategy_key: str):
    if strategy_key == "ma_crossover":
        from strategies.ma_crossover import MACrossoverStrategy
        return MACrossoverStrategy
    if strategy_key == "mean_reversion":
        from strategies.mean_reversion import MeanReversionStrategy
        return MeanReversionStrategy
    raise ValueError(strategy_key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=str, required=True, choices=list(_STRATEGIES.keys()))
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args()

    config = _STRATEGIES[args.strategy]
    register_strategy(args.strategy, config["display_name"], "production strategy")

    symbols = get_swing_universe()
    print(f"Fetching {args.years}y of data for {len(symbols)} symbol(s)...")
    data = fetch_all(symbols, period=f"{args.years}y" if args.years <= 10 else "max")
    print(f"Data available for {len(data)} symbol(s)")

    start_date = min(df.index.date.min() for df in data.values())
    end_date = max(df.index.date.max() for df in data.values())
    print(f"Base certification period: {start_date} to {end_date}")

    strategy_factory = _strategy_factory(args.strategy)

    base_result = run_certification_experiment(
        args.strategy, config["display_name"], strategy_factory, data, start_date, end_date,
        n_walk_forward_windows=args.windows, min_lookback_days=config["min_lookback_days"],
    )
    print(f"\nBase certification: {base_result['exp_id']} -- {base_result['verdict'].decision}")
    print(base_result["verdict"].reasoning)
    print(f"Evidence quality: {base_result['evidence_quality']['label']} ({base_result['evidence_quality']['score']}/100)")

    recent_start = end_date - timedelta(days=int(RECENT_PERIOD_YEARS * 365.25))
    recent_data = {sym: df[(df.index.date >= recent_start) & (df.index.date <= end_date)]
                   for sym, df in data.items()}
    recent_result = run_certification_experiment(
        args.strategy, config["display_name"], strategy_factory, recent_data, recent_start, end_date,
        n_walk_forward_windows=args.windows, min_lookback_days=config["min_lookback_days"],
    )
    print(f"\nRecent-period certification: {recent_result['exp_id']} -- {recent_result['verdict'].decision}")
    print(recent_result["verdict"].reasoning)
    print(f"Evidence quality: {recent_result['evidence_quality']['label']} ({recent_result['evidence_quality']['score']}/100)")

    final_decision = determine_acceptance_verdict(base_result["verdict"].decision, recent_result["verdict"].decision)
    print(f"\nFINAL CERTIFICATION VERDICT: {final_decision}")

    set_research_verdict(args.strategy, ResearchVerdict(final_decision),
                          source=f"{base_result['exp_id']}, {recent_result['exp_id']}")
    print(f"Recorded in deployment registry. Deployment status UNCHANGED (per standing governance rule).")


if __name__ == "__main__":
    main()
