"""
Is a finished backtest trustworthy enough to act on? A broken test must never quietly reject (or pass)
a strategy -- it gets re-tested instead.

WHY (2026-10-29). Three strategies were rejected on test periods that had silently stopped trading:
a Rs1,00,000 book fell under the minimum trade size after ~8 losing trades and sat idle for 3+ years,
and the auditor scored that as a loss. Two unrelated strategies, in different markets, showed the
SAME 8-trade, -8,334 window -- which is what gave it away, by eye, weeks later. Suraj: "fix the
problem once and for all". The minimum-trade cause is fixed in the engine; this catches the CLASS of
problem -- a test that broke, whatever broke it -- from the numbers alone.

Three signs, each read from the experiment's own metrics.json:
  * stalled   -- a window stopped trading under the minimum trade size (research_director flags it)
  * collapse  -- one window traded far less than the others over the same length of time
                 (each walk-forward window is an equal slice of the period)
  * duplicate -- a window identical, trade for trade and rupee for rupee, to one from a DIFFERENT
                 strategy's experiment: two strategies do not lose the same amount on the same count

A flagged result is not acted on. The strategy goes on deployment/state/research_retests.json, the
nightly backtest re-runs it before new research, and Telegram says so. ONE re-test: if the second run
is flagged too, its verdict stands and an alert asks for a human look -- a test that breaks the same
way twice is a bug to find, not a reason to loop.
"""

import json
import os
import re
from datetime import datetime
from statistics import median
from typing import Optional

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
EXPERIMENTS_DIRS = [os.path.join(REPO_DIR, sub, "experiments") for sub in ("swing_research", "research_lab")]
RETESTS_FILE = "research_retests.json"
MAX_ATTEMPTS = 2                      # the original run and one re-test

COLLAPSE_FRACTION = 0.15              # a window with under 15% of the others' typical trade count...
COLLAPSE_MIN_OTHERS = 20              # ...when the others traded at least this many
DUPLICATE_MIN_TRADES = 3
DUPLICATE_PNL_TOLERANCE = 0.005       # within 0.5%
WINDOW_NAMES = ["first test period", "second test period", "third test period", "fourth test period"]


def _folder(exp_id: str, roots=None) -> Optional[str]:
    for root in roots or EXPERIMENTS_DIRS:
        path = os.path.join(root, exp_id)
        if os.path.exists(os.path.join(path, "metrics.json")):
            return path
    return None


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _windows(metrics: dict) -> list:
    """[(label, window_metrics)] in time order, the holdout last."""
    wf = list(metrics.get("walk_forward_metrics") or [])
    out = [(WINDOW_NAMES[i] if i < len(WINDOW_NAMES) else f"test period {i + 1}", m) for i, m in enumerate(wf)]
    if metrics.get("out_of_sample_metrics"):
        out.append(("most recent unseen period", metrics["out_of_sample_metrics"]))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def _strategy_of(folder: str) -> set:
    """Every name this experiment goes by -- its manifest key and its hypothesis title. Two runs of
    one strategy share at least one (an early run may have only the title)."""
    names = set()
    manifest = _read_json(os.path.join(folder, "run_manifest.json")) or {}
    if manifest.get("strategy_name"):
        names.add(_norm(manifest["strategy_name"]))
    try:
        with open(os.path.join(folder, "hypothesis.md"), encoding="utf-8") as f:
            title = _norm(f.readline())
        if title:
            names.add(title)
    except OSError:
        pass
    return names or {os.path.basename(folder)}


def problems(exp_id: str, roots=None) -> list:
    """Plain-words reasons this experiment's test looks broken; [] when it looks sound."""
    folder = _folder(exp_id, roots)
    if folder is None:
        return []
    metrics = _read_json(os.path.join(folder, "metrics.json")) or {}
    windows = _windows(metrics)
    found = []

    for label, m in windows:
        if m.get("stalled"):
            found.append((label, f"the {label} stopped trading partway (every later trade was under the minimum size)"))

    counts = [int(m.get("total_trades") or 0) for _, m in windows]
    for i, (label, _) in enumerate(windows):
        # A window with NO trades is left to the auditor, which already ignores it: it is a long
        # indicator warm-up eating the first slice (EXP-066/067/070), not a test that broke midway.
        if counts[i] == 0:
            continue
        others = counts[:i] + counts[i + 1:]
        if len(others) >= 1 and max(others) >= COLLAPSE_MIN_OTHERS:
            typical = median(others)
            if counts[i] < COLLAPSE_FRACTION * typical:
                found.append((label, f"the {label} had only {counts[i]} trades against "
                                     f"{', '.join(map(str, others))} in the other periods of the same length"))

    mine = _strategy_of(folder)
    fingerprints = {(int(m.get("total_trades") or 0), float(m.get("total_pnl") or 0.0)): label
                    for label, m in windows if int(m.get("total_trades") or 0) >= DUPLICATE_MIN_TRADES}
    if fingerprints:
        for root in roots or EXPERIMENTS_DIRS:
            if not os.path.isdir(root):
                continue
            for other in sorted(os.listdir(root)):
                other_folder = os.path.join(root, other)
                if other == exp_id or not os.path.exists(os.path.join(other_folder, "metrics.json")):
                    continue
                other_metrics = _read_json(os.path.join(other_folder, "metrics.json")) or {}
                for _, om in _windows(other_metrics):
                    key_trades = int(om.get("total_trades") or 0)
                    for (trades, pnl), label in fingerprints.items():
                        if trades != key_trades or pnl == 0:
                            continue
                        if abs(float(om.get("total_pnl") or 0.0) - pnl) <= abs(pnl) * DUPLICATE_PNL_TOLERANCE \
                                and not (_strategy_of(other_folder) & mine):
                            found.append((label, f"the {label} is identical to one in {other} ({trades} "
                                                 f"trades, {pnl:,.0f}), a different strategy"))
                            fingerprints = {k: v for k, v in fingerprints.items() if v != label}
                            break
    # One reason per period is enough -- a stalled window usually also collapses.
    seen, unique = set(), []
    for label, text in found:
        if label not in seen:
            seen.add(label)
            unique.append(text)
    return unique


# ---- the re-test list --------------------------------------------------------------------------

def _retests_path(state_dir: str) -> str:
    return os.path.join(state_dir, RETESTS_FILE)


def load_retests(state_dir: str) -> list:
    data = _read_json(_retests_path(state_dir))
    return data if isinstance(data, list) else []


def _save_retests(state_dir: str, rows: list) -> None:
    from deployment.atomic_write import write_json
    write_json(_retests_path(state_dir), rows)


def attempts_so_far(state_dir: str, lane: str, key: str) -> int:
    for row in load_retests(state_dir):
        if row.get("lane") == lane and row.get("key") == key:
            return int(row.get("attempts") or 1)
    return 1


def add_retest(state_dir: str, lane: str, key: str, exp_id: str, reasons: list,
               attempts: int = 1, now: Optional[datetime] = None) -> None:
    """Puts `key` on the list (once). attempts counts the runs so far, the flagged one included."""
    rows = [r for r in load_retests(state_dir) if not (r.get("lane") == lane and r.get("key") == key)]
    rows.append({"lane": lane, "key": key, "after": exp_id, "reasons": list(reasons),
                 "attempts": int(attempts),
                 "added": (now or datetime.now()).isoformat(timespec="seconds")})
    _save_retests(state_dir, rows)


def remove_retest(state_dir: str, lane: str, key: str) -> None:
    rows = load_retests(state_dir)
    kept = [r for r in rows if not (r.get("lane") == lane and r.get("key") == key)]
    if len(kept) != len(rows):
        _save_retests(state_dir, kept)


def next_retest(state_dir: str) -> Optional[dict]:
    rows = load_retests(state_dir)
    return rows[0] if rows else None
