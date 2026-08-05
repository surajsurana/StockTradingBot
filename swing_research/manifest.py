"""
Run manifest -- generated and saved BEFORE every real Swing Research
Program run, so every experiment is fully reproducible after the fact:
which universe snapshot, which strategy variant, which data period, which
benchmarks, which engine version, and which exact git commit (plus whether
the working tree was clean) produced it.

Saved to swing_research/run_manifests/<timestamp>_<strategy>.json BEFORE
the run starts (so it exists even if the run itself fails partway), and a
copy is written into the experiment's own folder once save_experiment()
creates it, permanently linking the two.
"""

import json
import os
import subprocess
from datetime import datetime, date

from swing_research.universe import get_universe_metadata

RUN_MANIFESTS_DIR = os.path.join(os.path.dirname(__file__), "run_manifests")

# Bump on any change to backtesting_engine.py's simulation semantics
# (position sizing, pyramiding, portfolio caps, equity tracking) -- a
# manifest's engine_version is what lets a future re-read of an old
# experiment know whether its numbers are reproducible with the CURRENT
# code or reflect an earlier engine behavior.
SWING_ENGINE_VERSION = "v1"


def _git_info(repo_dir: str) -> dict:
    """Best-effort git commit hash + dirty-working-tree flag -- never
    raises (a manifest missing git info is far better than a run that
    can't start because git isn't on PATH in some environment)."""
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        commit_hash = None
    try:
        dirty_output = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_dir, stderr=subprocess.DEVNULL,
        ).decode().strip()
        is_dirty = bool(dirty_output)
        dirty_files = dirty_output.splitlines() if is_dirty else []
    except Exception:
        is_dirty = None
        dirty_files = []
    return {"commit_hash": commit_hash, "working_tree_dirty": is_dirty, "dirty_files": dirty_files}


def build_run_manifest(strategy_name: str, strategy_variant: str, data_period_years: float,
                        universe_limit: int, n_walk_forward_windows: int,
                        benchmark_config: dict, repo_dir: str) -> dict:
    universe_meta = get_universe_metadata()
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "strategy_name": strategy_name,
        "strategy_variant": strategy_variant,
        "engine_version": SWING_ENGINE_VERSION,
        "universe": {
            "version": universe_meta["version"], "frozen_on": universe_meta["frozen_on"],
            "full_symbol_count": universe_meta["symbol_count"],
            "limit_applied_this_run": universe_limit or universe_meta["symbol_count"],
        },
        "data_period_years_requested": data_period_years,
        "n_walk_forward_windows": n_walk_forward_windows,
        "benchmark_config": benchmark_config,
        "git": _git_info(repo_dir),
    }
    return manifest


def save_run_manifest(manifest: dict, manifests_dir: str = RUN_MANIFESTS_DIR) -> str:
    os.makedirs(manifests_dir, exist_ok=True)
    timestamp = manifest["generated_at"].replace(":", "-").replace(".", "-")
    filename = f"{timestamp}_{manifest['strategy_name']}.json"
    path = os.path.join(manifests_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    return path
