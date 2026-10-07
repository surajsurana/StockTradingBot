"""
CLI for the US equity research lane (Pool I) -- sibling to
run_swing_experiment.py (NSE) and run_crypto_experiment.py (Binance), but
fetching US daily data for the frozen S&P 500 universe
(swing_research/universe_us.py).

    python run_us_experiment.py --strategy=us_short_term_reversal [--years=10] [--limit=N] [--windows=3]

WHY THIS EXISTS (2026-10-05). The india and crypto lanes each had a CLI the
VPS could drive; the US lane had neither a CLI nor a catalog -- only two
hand-written Pool I wrappers in research_director.py, invoked by hand when
Pool I was first built. That gap meant run_queued_backtest.py had nothing to
call for a US candidate, so a US strategy the weekly routine implemented could
never actually be backtested, and its queue would have locked exactly the way
the india lane's did. RUNNERS below is the catalog that closes it: registering
a strategy here is what makes the VPS able to run it.

Nothing about a Strategy class changes between lanes -- the two existing US
entries reuse their India classes completely unmodified, which is the whole
reason a cross-market port is cheap. Only the data and universe differ, and
both are passed in by this file.

Costs and tax are NOT applied here. This is the research verdict on gross
price behaviour, judged by the same unmodified walk-forward -> statistical
audit -> acceptance-criteria pipeline as every other lane. The real
India-resident US-equity tax treatment lives in swing_research/us_equity_costs.py
and is applied by reporting/pool_i.py at the paper-trading stage, not here --
the same split the India lane uses, so verdicts stay comparable across lanes.
"""

import argparse
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from data.fetch_historical import fetch_all
from swing_research.manifest import build_run_manifest, save_run_manifest
from swing_research.universe_us import get_swing_universe_us

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def _director():
    return __import__("swing_research.research_director", fromlist=["x"])


# strategy key -> (runner getter, variant description for the run manifest).
# Add an entry here when a new US strategy is implemented -- that registration is what lets
# run_queued_backtest.py find and run it after a human merges the implementation.
RUNNERS = {
    "minervini_trend_template_filter_us": (
        lambda: _director().run_minervini_us_experiment,
        "Minervini Trend Template Filter, US variant (the unmodified India Strategy class against "
        "the frozen S&P 500 universe)"),
    "cross_sectional_momentum_us": (
        lambda: _director().run_cross_sectional_momentum_us_experiment,
        "Cross-Sectional Momentum, US variant (the unmodified India Strategy class against the "
        "frozen S&P 500 universe)"),
    "us_short_term_reversal": (
        lambda: _director().run_short_term_reversal_us_experiment,
        "Short-Term Reversal, US variant (the unmodified India Strategy class against the frozen "
        "S&P 500 universe)"),
}


def run_strategy(strategy_key: str, years: int, limit: int, windows: int) -> str:
    if strategy_key not in RUNNERS:
        raise ValueError(f"Unknown US strategy: {strategy_key}")
    runner_getter, variant = RUNNERS[strategy_key]

    symbols = get_swing_universe_us()
    if limit:
        symbols = symbols[:limit]
    period = f"{years}y" if years <= 10 else "max"

    # Manifest saved BEFORE the run, same as run_swing_experiment.py: it exists even if the run dies
    # partway, and is copied into the experiment folder once the id is known.
    manifest = build_run_manifest(
        strategy_name=strategy_key, strategy_variant=variant,
        data_period_years=years, universe_limit=limit, n_walk_forward_windows=windows,
        benchmark_config={"benchmarks": ["buy_and_hold"],
                          "starting_capital": 100_000},
        repo_dir=REPO_DIR,
    )
    manifest_path = save_run_manifest(manifest)
    print(f"Run manifest saved: {manifest_path}")
    print(f"  engine_version={manifest['engine_version']} git_commit={manifest['git']['commit_hash']} "
          f"dirty={manifest['git']['working_tree_dirty']}")
    if manifest["git"]["working_tree_dirty"]:
        print(f"  WARNING: working tree has uncommitted changes -- this run is NOT reproducible from "
              f"the commit hash alone: {manifest['git']['dirty_files']}")

    print(f"\nFetching {period} of daily data for {len(symbols)} US symbol(s) via yfinance "
          f"(bare tickers, no suffix)...")
    data = fetch_all(symbols, period=period)
    if not data:
        print("No data fetched -- aborting.")
        sys.exit(1)
    print(f"Data available for {len(data)} symbol(s)")

    start_date = min(df.index.date.min() for df in data.values())
    end_date = max(df.index.date.max() for df in data.values())
    print(f"Backtest period: {start_date} to {end_date}")

    exp_id = runner_getter()(
        data=data, start_date=start_date, end_date=end_date,
        n_walk_forward_windows=windows, narrative_api_key=settings.ANTHROPIC_API_KEY,
    )

    from research_lab.experiment_manager import load_experiment
    from swing_research.research_director import SWING_EXPERIMENTS_DIR
    shutil.copy(manifest_path, os.path.join(SWING_EXPERIMENTS_DIR, exp_id, "run_manifest.json"))

    loaded = load_experiment(exp_id, SWING_EXPERIMENTS_DIR)
    m = loaded["metrics"]
    # "Saved as <EXP-id>" is parsed by run_queued_backtest.py -- keep this line's wording.
    print(f"\n{'=' * 70}\nSaved as {exp_id}\n{'=' * 70}")
    print(loaded["verdict"])
    eq = m.get("evidence_quality", {})
    if eq:
        print(f"Evidence quality: {eq.get('label')} ({eq.get('score')}/100)")
    print(f"\nFull period, {m.get('total_trades')} trades (USD, gross of India tax -- "
          f"reporting/pool_i.py applies that at the paper-trading stage):")
    print(f"  P&L {m.get('total_pnl')} | CAGR {m.get('cagr')}% | Sharpe {m.get('sharpe_ratio')} "
          f"| MaxDD {m.get('max_drawdown_pct')}%")
    for name, b in (m.get("comparison_vs_benchmarks") or {}).items():
        print(f"  {name}: trades={b.get('total_trades')} CAGR={b.get('cagr')}% "
              f"Sharpe={b.get('sharpe_ratio')} MaxDD={b.get('max_drawdown_pct')}%")
    return exp_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=str, choices=list(RUNNERS),
                        default="cross_sectional_momentum_us")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="0 = the full frozen S&P 500 universe")
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args()
    run_strategy(args.strategy, args.years, args.limit, args.windows)


if __name__ == "__main__":
    main()
