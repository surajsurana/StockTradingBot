"""
The research queue: turns swing_research.research_roadmap's ranked candidate
list into "one strategy in research at a time, a new one picked up
automatically once a week, or a specific one started early by hand"
(2026-09-22, per explicit direction).

This module is deliberately dumb -- it only tracks WHICH candidate is
current and keeps an append-only history, in deployment/state/research_queue.json,
the same atomic-write-over-a-tmp-file convention advice/tasks.py already uses
for advice_done.json. It never scores anything itself (that's
research_roadmap.build_roadmap(), unchanged) and never writes strategy code
or runs a backtest -- advance_research_queue.py (the weekly, fully
deterministic cron) and the unattended research routine (Phase 2, a
scheduled Claude Code cloud agent, not Python in this repo) are what act on
what this file records.
"""

import json
import os
from datetime import date, datetime
from typing import Optional

QUEUE_FILE = "research_queue.json"


def _empty() -> dict:
    return {"current": None, "history": []}


def load(state_dir: Optional[str]) -> dict:
    if not state_dir:
        return _empty()
    path = os.path.join(state_dir, QUEUE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "current" not in data or "history" not in data:
            return _empty()
        return data
    except (OSError, ValueError):
        return _empty()


def _save(state_dir: str, data: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, QUEUE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, os.path.join(state_dir, QUEUE_FILE))


def _attempted_keys(data: dict) -> set:
    """Candidates already current or somewhere in history -- never picked twice by advance()."""
    keys = {r["key"] for r in data["history"]}
    if data["current"]:
        keys.add(data["current"]["key"])
    return keys


def _set_current(state_dir: str, data: dict, key: str, name: str, started_by: str, now: datetime) -> dict:
    entry = {"key": key, "started": now.date().isoformat(), "started_by": started_by}
    data["current"] = entry
    data["history"] = list(data["history"]) + [{"key": key, "name": name, "queued": now.date().isoformat(),
                                                 "started": entry["started"], "started_by": started_by,
                                                 "resolved": None, "outcome": None, "experiment_id": None, "branch": None}]
    _save(state_dir, data)
    return entry


def advance(state_dir: str, roadmap: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """The weekly, automatic path: if nothing is currently in research, pick the top-ranked
    researchable_now candidate never attempted before. Does nothing (returns None) if something
    is already current, or nothing eligible remains -- "one at a time" is enforced here, not by the caller."""
    now = now or datetime.now()
    data = load(state_dir)
    if data["current"] is not None:
        return None
    attempted = _attempted_keys(data)
    for scored in roadmap.get("researchable_now", []):
        c = scored.candidate
        if c.key not in attempted:
            return _set_current(state_dir, data, c.key, c.name, "auto", now)
    return None


def start_now(state_dir: str, key: str, roadmap: dict, now: Optional[datetime] = None) -> dict:
    """The manual "start research" button: jump the queue to `key` regardless of rank.
    Raises ValueError for an unknown key or one already current/resolved, same 400-on-bad-input
    convention as advice.tasks.mark_done, so dashboard/server.py's POST handler can reuse it as-is."""
    now = now or datetime.now()
    data = load(state_dir)
    if data["current"] is not None:
        raise ValueError(f"already researching {data['current']['key']}")
    match = next((s.candidate for s in roadmap.get("all_scored", []) if s.candidate.key == key), None)
    if match is None:
        raise ValueError(f"unknown candidate {key!r}")
    if key in _attempted_keys(data):
        raise ValueError(f"{key!r} was already queued or resolved")
    return _set_current(state_dir, data, match.key, match.name, "manual", now)


def resolve(state_dir: str, key: str, outcome: str, experiment_id: Optional[str] = None,
            branch: Optional[str] = None, now: Optional[datetime] = None) -> None:
    """Called once the research routine finishes with `key` (whatever its verdict): clears `current`
    and fills in its history row. outcome is "researched" (a real backtest ran, PASS or REJECT --
    experiment_id/branch identify it) or "skipped" (nobody got to it / it was abandoned)."""
    if outcome not in ("researched", "skipped"):
        raise ValueError("outcome must be 'researched' or 'skipped'")
    now = now or datetime.now()
    data = load(state_dir)
    if not data["current"] or data["current"]["key"] != key:
        raise ValueError(f"{key!r} is not the current candidate")
    data["current"] = None
    for row in reversed(data["history"]):
        if row["key"] == key and row["resolved"] is None:
            row["resolved"] = now.date().isoformat()
            row["outcome"] = outcome
            row["experiment_id"] = experiment_id
            row["branch"] = branch
            break
    _save(state_dir, data)
