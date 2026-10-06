"""
Deployment status, kept where a deploy cannot overwrite it.

THE BUG THIS EXISTS TO FIX, which cost a real promotion. deployment/state/strategy_registry.json is
TRACKED BY GIT -- deliberately, because registering a strategy and recording a research verdict are
facts that belong in the repo's history, and the cloud research routines commit them. But the same
file also holds `deployment_status`, which is not a repo fact at all: it is what the machine is doing
right now. Every deploy ran `git reset --hard origin/main`, which reset the file, which silently
reverted every promotion made from the dashboard. Pool G was promoted and then found to be
PAPER_TRADING again after the next deploy, more than once, with nothing in any log to say why.

So status lives here instead: a small untracked file beside the registry, applied over it on read.
Git cannot touch it, because git does not know about it.

WHAT GOES IN THE REGISTRY AND WHAT GOES HERE. The registry keeps what is true about a strategy as
research -- its id, name, family, verdict, experiment. This keeps only what is true about it as a
deployment: its current status and the history of how it got there. A reader sees one merged record
and does not need to know the difference; a deploy sees only the first half.

PRECEDENCE IS ONE-WAY. The overlay always wins when it has an entry, because it is the only one of
the two that is written by the running system. A strategy with no overlay entry keeps whatever the
registry says, so nothing had to be migrated and a fresh checkout still works.
"""

import os
import time
from typing import Optional

OVERLAY_FILENAME = "deployment_status.json"


def overlay_path(state_dir: str) -> str:
    return os.path.join(state_dir, OVERLAY_FILENAME)


def load(state_dir: str) -> dict:
    """{strategy_key: {"status": str, "history": list}}.

    An unreadable overlay is EMPTY, which falls back to the registry rather than raising -- a corrupt
    status file must not stop the program knowing what its strategies are."""
    import json
    try:
        with open(overlay_path(state_dir), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict) and v.get("status")}
    except (OSError, ValueError, TypeError):
        return {}


def record_status(state_dir: str, strategy_key: str, from_status: str, to_status: str,
                  reason: str = "", when: Optional[float] = None) -> dict:
    """Writes `strategy_key`'s new status and appends to its history. Returns the entry written.

    The history is kept HERE as well as the status, because the two must not drift: a promotion whose
    status survived a deploy but whose reason did not would leave `[MANUAL OVERRIDE]` unreadable, and
    the per-order guard reads that marker to decide whether to honour the override."""
    from deployment.atomic_write import write_json

    current = load(state_dir)
    entry = current.get(strategy_key) or {"status": from_status, "history": []}
    history = list(entry.get("history") or [])
    history.append({"from_status": from_status, "to_status": to_status,
                    "timestamp": when if when is not None else time.time(), "reason": reason})
    current[strategy_key] = {"status": to_status, "history": history}
    write_json(overlay_path(state_dir), current, sort_keys=True)
    return current[strategy_key]


def apply_to(registry: dict, state_dir: str) -> dict:
    """Overlays stored statuses onto records loaded from the registry, in place.

    An unknown status string is ignored rather than guessed at: a typo or a value from a newer
    version of the program must leave the strategy at its registry status, not crash the dashboard
    and not silently land it somewhere it was never put."""
    from deployment.base import DeploymentStatus

    stored = load(state_dir)
    for key, entry in stored.items():
        record = registry.get(key)
        if record is None:
            continue                      # a strategy that no longer exists
        # Case-tolerant on purpose. The values written are DeploymentStatus.value (upper case), but
        # silently ignoring a differently-cased status would revert a promotion with no error
        # anywhere -- the exact failure mode this whole file exists to end.
        try:
            raw = str(entry["status"]).strip()
            record.deployment_status = DeploymentStatus(raw.upper() if raw.upper() in
                                                        DeploymentStatus.__members__ else raw)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        history = entry.get("history")
        if isinstance(history, list) and history:
            # The overlay's history is the live one: it holds the promotions that happened on this
            # machine, which is exactly what the registry in git does not know about.
            record.deployment_status_history = list(history)
    return registry
