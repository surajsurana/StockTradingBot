"""
Robustness analysis for the Swing Research Program -- requested 2026-08-03,
after the Research Audit confirmed the benchmark comparison is now fair.
Tests whether Turtle System 2's apparent edge survives reasonable changes
to assumptions the base run made, WITHOUT touching the published strategy's
own rules (no parameter tuning of Turtle itself -- that would violate the
"implement exactly as documented, no optimization" mandate). What IS varied
here are backtest/modeling assumptions and data slices:

    1. Sub-period robustness -- first half vs second half of the same
       10-year window, so the edge isn't resting on one historical stretch.
    2. Universe robustness -- a smaller subset of the frozen universe, so
       the edge isn't an artifact of exactly which 457 symbols were tested.
    3. Walk-forward window count -- 3 vs 5 windows, so the audit's PASS
       isn't sensitive to an arbitrary windowing choice.

Fetches the full 10-year, full-universe dataset ONCE and reuses it (via
slicing) for every variant below -- avoids re-fetching the same yfinance
data 4 times over.

    python run_swing_robustness.py
"""

from datetime import date, timedelta

from config import settings
from data.fetch_historical import fetch_all
from swing_research.manifest import build_run_manifest, save_run_manifest
from swing_research.research_director import run_turtle_experiment, SWING_EXPERIMENTS_DIR
from swing_research.universe import get_swing_universe
from research_lab.experiment_manager import load_experiment

import os
import shutil

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
STARTING_CAPITAL = getattr(settings, "RESEARCH_LAB_VIRTUAL_CAPITAL", 1_000_000)


def _run_variant(label: str, data: dict, start_date: date, end_date: date, windows: int, universe_limit: int):
    print(f"\n{'=' * 70}\nVARIANT: {label}\n{'=' * 70}")
    print(f"Symbols: {len(data)}  Period: {start_date} to {end_date}  Windows: {windows}")

    manifest = build_run_manifest(
        strategy_name="turtle_system2", strategy_variant=f"System 2 -- robustness variant: {label}",
        data_period_years=(end_date - start_date).days / 365.25, universe_limit=universe_limit,
        n_walk_forward_windows=windows,
        benchmark_config={"benchmarks": ["ma_crossover_production", "mean_reversion_production",
                                          "buy_and_hold", "nifty_500_index"],
                           "starting_capital": STARTING_CAPITAL, "robustness_label": label},
        repo_dir=REPO_DIR,
    )
    manifest_path = save_run_manifest(manifest)

    exp_id = run_turtle_experiment(
        data=data, start_date=start_date, end_date=end_date, starting_capital=STARTING_CAPITAL,
        n_walk_forward_windows=windows, narrative_api_key=settings.ANTHROPIC_API_KEY,
    )
    exp_dir = os.path.join(SWING_EXPERIMENTS_DIR, exp_id)
    shutil.copy(manifest_path, os.path.join(exp_dir, "run_manifest.json"))

    loaded = load_experiment(exp_id, SWING_EXPERIMENTS_DIR)
    comparison = loaded["metrics"].get("comparison_vs_benchmarks", {})
    print(f"Saved as {exp_id}")
    print(loaded["verdict"].splitlines()[0])
    for name, m in comparison.items():
        print(f"  {name}: trades={m.get('total_trades')} CAGR={m.get('cagr')}% "
              f"Sharpe={m.get('sharpe_ratio')} MaxDD={m.get('max_drawdown_pct')}%")
    return exp_id, loaded


def main():
    symbols = get_swing_universe()
    print(f"Fetching 10y of daily data for {len(symbols)} symbol(s) via yfinance (ONE fetch, reused for "
          f"every robustness variant below)...")
    data = fetch_all(symbols, period="10y")
    if not data:
        print("No data fetched -- aborting.")
        return
    print(f"Data available for {len(data)} symbol(s)")

    full_start = min(df.index.date.min() for df in data.values())
    full_end = max(df.index.date.max() for df in data.values())
    midpoint = full_start + (full_end - full_start) / 2

    results = {}

    # 1a. First half of the 10-year window, full universe
    first_half_data = {sym: df[(df.index.date >= full_start) & (df.index.date <= midpoint)]
                        for sym, df in data.items()}
    results["first_half_2016-2021"] = _run_variant(
        "First half of 10y window (2016-2021ish)", first_half_data, full_start, midpoint,
        windows=2, universe_limit=0,
    )

    # 1b. Second half of the 10-year window, full universe
    second_half_data = {sym: df[(df.index.date > midpoint) & (df.index.date <= full_end)]
                         for sym, df in data.items()}
    results["second_half_2021-2026"] = _run_variant(
        "Second half of 10y window (2021-2026ish)", second_half_data, midpoint, full_end,
        windows=2, universe_limit=0,
    )

    # 2. Smaller universe subset (first 150 of the frozen 457, alphabetical
    # -- NOT a liquidity ranking, just a different, smaller symbol set for
    # sensitivity testing), full 10-year period.
    subset_symbols = sorted(data.keys())[:150]
    subset_data = {sym: data[sym] for sym in subset_symbols}
    results["universe_subset_150"] = _run_variant(
        "150-symbol subset of frozen universe, full 10y period", subset_data, full_start, full_end,
        windows=3, universe_limit=150,
    )

    # 3. Different walk-forward window count (5 instead of 3), full
    # universe, full period -- audit-sensitivity check, not a strategy change.
    results["five_walk_forward_windows"] = _run_variant(
        "Full universe, full 10y period, 5 walk-forward windows (vs base run's 3)",
        data, full_start, full_end, windows=5, universe_limit=0,
    )

    print(f"\n{'=' * 70}\nROBUSTNESS SUMMARY\n{'=' * 70}")
    for label, (exp_id, loaded) in results.items():
        verdict_line = loaded["verdict"].splitlines()[0]
        turtle_m = loaded["metrics"].get("comparison_vs_benchmarks", {}).get("turtle_system2", {})
        print(f"{label}: {exp_id} -- {verdict_line} -- "
              f"trades={turtle_m.get('total_trades')} CAGR={turtle_m.get('cagr')}% "
              f"Sharpe={turtle_m.get('sharpe_ratio')} MaxDD={turtle_m.get('max_drawdown_pct')}%")


if __name__ == "__main__":
    main()
