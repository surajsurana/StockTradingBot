"""
CLI entry point for the Swing Research Program -- sibling to
run_experiment.py (the intraday research lab's CLI), but for swing/
position-horizon strategies. Fully isolated: imports only from
swing_research/, plus READ-ONLY reuse of research_lab/'s generic pieces
and the production data-fetching modules (data/fetch_historical.py,
research_lab/performance_analyst.py's load_sector_map). Never imports
from or writes to strategies/, risk/, portfolio/, execution/, or cio/.

    python run_swing_experiment.py --strategy=turtle_system2 [--years=10] [--limit=N] [--windows=3]
    python run_swing_experiment.py --strategy=minervini_trend_template_filter [--years=10] [--limit=N] [--windows=3]

--limit=N restricts to the first N symbols of the frozen universe (see
swing_research/universe.py) -- for a fast smoke-test run before committing
to a full 457-symbol x multi-year fetch.
"""

import argparse
import os
import shutil
import sys

from config import settings
from data.fetch_historical import fetch_all
from swing_research.manifest import build_run_manifest, save_run_manifest
from swing_research.universe import get_swing_universe

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

_STRATEGY_VARIANTS = {
    "turtle_system2": "System 2 (55-day entry / 20-day exit, long-only)",
    "minervini_trend_template_filter": "Trend Template Filter (8-criterion screen, disclosed mechanical entry trigger, no pyramiding)",
    "52_week_high_momentum": "52-Week High Momentum (top-decile nearness percentile, K=6mo single-vintage, no percentile-based early exit)",
    "cross_sectional_momentum": "Cross-Sectional Momentum (J=6mo formation, top-decile percentile, K=6mo single-vintage, no percentile-based early exit)",
}


def run_strategy(strategy_key: str, years: int, limit: int, windows: int):
    if strategy_key == "turtle_system2":
        from swing_research.research_director import run_turtle_experiment as run_experiment_fn
    elif strategy_key == "minervini_trend_template_filter":
        from swing_research.research_director import run_minervini_experiment as run_experiment_fn
    elif strategy_key == "52_week_high_momentum":
        from swing_research.research_director import run_52_week_high_momentum_experiment as run_experiment_fn
    elif strategy_key == "cross_sectional_momentum":
        from swing_research.research_director import run_cross_sectional_momentum_experiment as run_experiment_fn
    else:
        raise ValueError(f"Unknown strategy: {strategy_key}")

    symbols = get_swing_universe()
    if limit:
        symbols = symbols[:limit]

    period = f"{years}y" if years <= 10 else "max"

    # Manifest generated and saved BEFORE the run starts -- exists even if
    # the run itself fails partway, and gets copied into the experiment's
    # own folder below once exp_id is known.
    manifest = build_run_manifest(
        strategy_name=strategy_key, strategy_variant=_STRATEGY_VARIANTS[strategy_key],
        data_period_years=years, universe_limit=limit, n_walk_forward_windows=windows,
        benchmark_config={
            "benchmarks": ["ma_crossover_production", "mean_reversion_production",
                           "buy_and_hold", "nifty_500_index"],
            "starting_capital": getattr(settings, "RESEARCH_LAB_VIRTUAL_CAPITAL", 1_000_000),
        },
        repo_dir=REPO_DIR,
    )
    manifest_path = save_run_manifest(manifest)
    print(f"Run manifest saved: {manifest_path}")
    print(f"  engine_version={manifest['engine_version']} universe={manifest['universe']['version']} "
          f"git_commit={manifest['git']['commit_hash']} "
          f"dirty={manifest['git']['working_tree_dirty']}")
    if manifest["git"]["working_tree_dirty"]:
        print(f"  WARNING: working tree has uncommitted changes -- this run is NOT reproducible from "
              f"the commit hash alone: {manifest['git']['dirty_files']}")

    print(f"\nFetching {period} of daily data for {len(symbols)} symbol(s) via yfinance "
          f"(data/fetch_historical.py, read-only reuse)...")
    data = fetch_all(symbols, period=period)
    if not data:
        print("No data fetched -- aborting.")
        sys.exit(1)
    print(f"Data available for {len(data)} symbol(s)")

    start_date = min(df.index.date.min() for df in data.values())
    end_date = max(df.index.date.max() for df in data.values())
    print(f"Backtest period: {start_date} to {end_date}")

    exp_id = run_experiment_fn(
        data=data, start_date=start_date, end_date=end_date,
        starting_capital=manifest["benchmark_config"]["starting_capital"],
        n_walk_forward_windows=windows, narrative_api_key=settings.ANTHROPIC_API_KEY,
    )

    from swing_research.research_director import SWING_EXPERIMENTS_DIR
    from research_lab.experiment_manager import load_experiment

    # Permanently link the manifest to the experiment folder it produced.
    exp_dir = os.path.join(SWING_EXPERIMENTS_DIR, exp_id)
    shutil.copy(manifest_path, os.path.join(exp_dir, "run_manifest.json"))

    loaded = load_experiment(exp_id, SWING_EXPERIMENTS_DIR)
    print(f"\n{'=' * 70}\nSaved as {exp_id}\n{'=' * 70}")
    print(loaded["verdict"])
    eq = loaded["metrics"].get("evidence_quality", {})
    if eq:
        print(f"Evidence quality: {eq.get('label')} ({eq.get('score')}/100) -- {eq.get('components')}")
    print("\nComparison vs. benchmarks (see metrics.json's comparison_vs_benchmarks for full detail):")
    comparison = loaded["metrics"].get("comparison_vs_benchmarks", {})
    for name, m in comparison.items():
        print(f"  {name}: trades={m.get('total_trades')} CAGR={m.get('cagr')}% "
              f"Sharpe={m.get('sharpe_ratio')} MaxDD={m.get('max_drawdown_pct')}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=str, default="turtle_system2",
                         choices=["turtle_system2", "minervini_trend_template_filter", "52_week_high_momentum",
                                  "cross_sectional_momentum"])
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="0 = full frozen universe")
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args()

    run_strategy(args.strategy, args.years, args.limit, args.windows)


if __name__ == "__main__":
    main()
