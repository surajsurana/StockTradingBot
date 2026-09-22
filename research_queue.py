"""
The research queue: turns swing_research.research_roadmap's ranked candidate
list into "one strategy in research at a time, a new one picked up
automatically, or a specific one started early by hand" (2026-09-22, per
explicit direction).

The current pick is free to change right up until research actually starts
on it (2026-09-22, per explicit direction: "interrupting and changing mid
week or anytime is ok, only if a research is already ongoing then it should
not interrupt"): a candidate sits as `current` with `in_progress=False` from
the moment it's queued, and can be bumped for a better-ranked one, or a
specific one picked by hand, any number of times -- reconsider() and
start_now() both do this. The unattended research routine (Phase 2, a
scheduled Claude Code cloud agent, not Python in this repo) calls
mark_in_progress() as the very first thing it does once it commits to a
candidate; from that moment `current` is locked until resolve() clears it.
A bumped candidate's history row is closed out with outcome "superseded",
same append-only audit trail as a real "researched"/"skipped" resolution.

A candidate that can never get a real historical backtest (a genuine data gap, not a scope choice)
but scores as well as the worst candidate that CAN is still queued -- just with mode="paper_direct"
instead of mode="backtest" (2026-09-22, per explicit direction: "paper trading is also part of
research and not real money... with the results of paper trading we can decide whether to give real
money" -- a good-but-untestable candidate shouldn't just sit inert in "waiting on data we don't have"
forever). Which candidates qualify is decided by research_roadmap.build_roadmap()'s
paper_direct_eligible list (deterministic, reviewable code, not the research routine's own
judgement) -- this module just carries the mode through. In paper_direct mode the research routine
implements the strategy's decision logic from today's data and proposes registering it straight as a
new paper-trading pool (no backtest verdict possible), same PR-only, human-merges-it governance as
every other candidate; a human manually starting a blocked candidate via start_now() also gets
paper_direct mode, on their own judgement, regardless of whether it clears the automatic floor.

This module is deliberately dumb -- it only tracks state, in
deployment/state/research_queue.json, the same atomic-write-over-a-tmp-file
convention advice/tasks.py already uses for advice_done.json. It never
scores anything itself (that's research_roadmap.build_roadmap(), unchanged)
and never writes strategy code or runs a backtest.
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


def _resolved_keys(data: dict) -> set:
    """Keys with a CLOSED history row (researched, skipped or superseded) -- these are done, never
    picked again. The current pick's own key is deliberately not in here -- it's still open to being
    compared against, and swapped out for, a better-ranked candidate until research starts on it."""
    return {r["key"] for r in data["history"] if r.get("resolved") is not None}


def _set_current(state_dir: str, data: dict, key: str, name: str, started_by: str, mode: str, now: datetime) -> dict:
    entry = {"key": key, "started": now.date().isoformat(), "started_by": started_by, "in_progress": False, "mode": mode}
    data["current"] = entry
    data["history"] = list(data["history"]) + [{"key": key, "name": name, "queued": now.date().isoformat(),
                                                 "started": entry["started"], "started_by": started_by, "mode": mode,
                                                 "resolved": None, "outcome": None, "experiment_id": None, "branch": None}]
    _save(state_dir, data)
    return entry


def _close_open_row(data: dict, key: str, outcome: str, now: datetime,
                     experiment_id: Optional[str] = None, branch: Optional[str] = None) -> None:
    for row in reversed(data["history"]):
        if row["key"] == key and row["resolved"] is None:
            row["resolved"], row["outcome"] = now.date().isoformat(), outcome
            row["experiment_id"], row["branch"] = experiment_id, branch
            return


def advance(state_dir: str, roadmap: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Keeps `current` pointed at the best available candidate: picks the top-ranked one if nothing is
    queued, swaps it for a better-ranked one if the current pick was itself auto-picked and hasn't
    started yet, and does nothing once research is in_progress (locked until resolve()) -- "research
    already ongoing" is the only thing this refuses to interrupt (2026-09-22, per explicit direction).
    A candidate a human chose by hand (start_now, started_by="manual") is left alone here; only a
    human picking something else, via start_now, moves it off a manual pick. Safe to call as often as
    you like -- every no-op path just returns None."""
    now = now or datetime.now()
    data = load(state_dir)
    if data["current"] and data["current"].get("in_progress"):
        return None
    if data["current"] and data["current"].get("started_by") == "manual":
        return None
    resolved = _resolved_keys(data)
    pool = ([(s, "backtest") for s in roadmap.get("researchable_now", [])]
            + [(s, "paper_direct") for s in roadmap.get("paper_direct_eligible", [])])
    pool.sort(key=lambda pair: -pair[0].total_score)
    picked = next(((s, mode) for s, mode in pool if s.candidate.key not in resolved), None)
    if picked is None:
        return None
    top, mode = picked[0].candidate, picked[1]
    if data["current"] is not None and data["current"]["key"] == top.key:
        return None   # already the best available pick
    if data["current"] is not None:
        _close_open_row(data, data["current"]["key"], "superseded", now)
    return _set_current(state_dir, data, top.key, top.name, "auto", mode, now)


def start_now(state_dir: str, key: str, roadmap: dict, now: Optional[datetime] = None) -> dict:
    """The manual "start research" button: jump the queue to `key` regardless of rank, any time --
    including bumping whatever's currently queued, auto-picked or manual, as long as research hasn't
    actually started on it yet. Refuses only once research is in_progress, or for an unknown/already
    resolved key, same 400-on-bad-input convention as advice.tasks.mark_done."""
    now = now or datetime.now()
    data = load(state_dir)
    if data["current"] and data["current"].get("in_progress"):
        raise ValueError(f"already researching {data['current']['key']}")
    match = next((s for s in roadmap.get("all_scored", []) if s.candidate.key == key), None)
    if match is None:
        raise ValueError(f"unknown candidate {key!r}")
    if key in _resolved_keys(data):
        raise ValueError(f"{key!r} was already resolved")
    if data["current"] is not None and data["current"]["key"] == key:
        return data["current"]   # already this one
    if data["current"] is not None:
        _close_open_row(data, data["current"]["key"], "superseded", now)
    # A human choosing a blocked candidate by hand is that human's own judgement call, independent of
    # whether it clears the automatic paper_direct_eligible floor.
    mode = "paper_direct" if match.feasibility_classification == "NOT_CURRENTLY_IMPLEMENTABLE" else "backtest"
    return _set_current(state_dir, data, match.candidate.key, match.candidate.name, "manual", mode, now)


def mark_in_progress(state_dir: str, key: str, now: Optional[datetime] = None) -> None:
    """Called by the research routine the instant it commits to actually working on `key` -- locks
    `current` so advance()/start_now() can no longer bump it. Raises if `key` is no longer the current
    pick (it may have been superseded, or resolved, before the routine got to it)."""
    now = now or datetime.now()
    data = load(state_dir)
    if not data["current"] or data["current"]["key"] != key:
        raise ValueError(f"{key!r} is not the current candidate")
    data["current"]["in_progress"] = True
    _save(state_dir, data)


def resolve(state_dir: str, key: str, outcome: str, experiment_id: Optional[str] = None,
            branch: Optional[str] = None, now: Optional[datetime] = None) -> None:
    """Called once the research routine finishes with `key` (whatever the result): clears `current`
    and fills in its history row. outcome is "researched" (mode="backtest" -- a real backtest ran,
    PASS or REJECT, experiment_id/branch identify it), "paper_trading_proposed" (mode="paper_direct" --
    no backtest was possible, but a paper-trading pool was implemented and proposed in a PR, branch
    identifies it) or "skipped" (nobody got to it / it was abandoned, either mode)."""
    if outcome not in ("researched", "paper_trading_proposed", "skipped"):
        raise ValueError("outcome must be 'researched', 'paper_trading_proposed' or 'skipped'")
    now = now or datetime.now()
    data = load(state_dir)
    if not data["current"] or data["current"]["key"] != key:
        raise ValueError(f"{key!r} is not the current candidate")
    data["current"] = None
    _close_open_row(data, key, outcome, now, experiment_id, branch)
    _save(state_dir, data)
