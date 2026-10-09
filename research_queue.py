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
scheduled Claude Code cloud agent, not Python in this repo) claims a
candidate by pushing its own branch (research_queue_github_sync.py watches
for it and calls mark_in_progress()); from that moment `current` is locked
until resolve() clears it. A bumped candidate's history row is closed out
with outcome "superseded", same append-only audit trail as a real
"researched"/"skipped" resolution.

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

LANES (added 2026-10-03, per explicit direction: "shall we also build this same auto research for
crypto and us equity? and each section runs 1 or 2 strategies research per week at different times"):
three INDEPENDENT queues -- "india" (the original, unchanged), "crypto" and "us" -- each with its own
`current`/`history`, its own state file, and (research_queue_github_sync.py) its own snapshot/branch-
prefix on GitHub, so a glut of India candidates can never starve crypto or US of research cadence the
way a single pooled queue would. research_roadmap.lane_of(candidate) decides which lane a candidate
belongs to (horizon_lane == "crypto" -> "crypto"; market == "US" -> "us"; everything else -> "india",
which is every pre-existing candidate, unchanged). Every function below defaults to lane="india" for
full backward compatibility with every caller that predates this -- the dashboard, the Telegram
watchdog, and the "india" lane's own cron all keep working completely unmodified. start_now() is the
one exception: it takes the FULL unfiltered roadmap (not a lane-filtered one) and determines the
target candidate's lane itself via lane_of(), so the dashboard's single "Start research" button keeps
working for a candidate in ANY lane without the caller needing to know which.

This module is deliberately dumb -- it only tracks state, in
deployment/state/research_queue*.json, the same atomic-write-over-a-tmp-file
convention advice/tasks.py already uses for advice_done.json. It never
scores anything itself (that's research_roadmap.build_roadmap(), unchanged)
and never writes strategy code or runs a backtest.
"""

import json
import os
from datetime import date, datetime
from typing import Optional

LANES = ("india", "crypto", "us")


def _queue_filename(lane: str) -> str:
    """"india" keeps the original, unlabeled filename -- this is the lane an in-flight research run
    and every pre-2026-10-03 caller already knows about; renaming it would break them."""
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}, must be one of {LANES}")
    return "research_queue.json" if lane == "india" else f"research_queue_{lane}.json"


def _empty() -> dict:
    return {"current": None, "history": []}


def load(state_dir: Optional[str], lane: str = "india") -> dict:
    if not state_dir:
        return _empty()
    path = os.path.join(state_dir, _queue_filename(lane))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "current" not in data or "history" not in data:
            return _empty()
        return data
    except (OSError, ValueError):
        return _empty()


def _save(state_dir: str, data: dict, lane: str = "india") -> None:
    os.makedirs(state_dir, exist_ok=True)
    filename = _queue_filename(lane)
    tmp = os.path.join(state_dir, filename + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, os.path.join(state_dir, filename))


# Outcomes that close a history row WITHOUT any research having happened, so the candidate goes back
# in the pool to be picked again later (2026-10-09, per explicit direction: "we want the superseded
# one to be researched if that was the case").
#
# WHY THIS EXISTS. "Resolved" was read as "finished with", and these two are not. A candidate bumped
# for a better-ranked one has had nothing done to it at all, and a candidate whose routine died and
# got abandoned has had less than that -- yet both were being retired permanently. Three real
# candidates had already been lost this way: nifty_momentum_30_style and turnover_liquidity in the
# India lane, crypto_long_horizon_reversal in crypto, none of them ever researched, none of them
# ever eligible to be again.
#
# Listed by what REOPENS rather than by what concludes, deliberately: an outcome this file has never
# heard of keeps the old, conservative behaviour of counting as done, instead of a typo quietly
# putting finished work back in the queue.
REOPENING_OUTCOMES = frozenset({"superseded", "abandoned"})


def _resolved_keys(data: dict) -> set:
    """Keys whose research actually CONCLUDED -- researched, skipped, or proposed straight for paper
    trading. These are done and never picked again.

    A row closed as superseded or abandoned is not one of them: no research was done, so the key
    stays eligible and will be picked again once the candidates that outranked it are out of the way.
    The current pick's own key is deliberately not in here either -- it's still open to being
    compared against, and swapped out for, a better-ranked candidate until research starts on it."""
    return {r["key"] for r in data["history"]
            if r.get("resolved") is not None and r.get("outcome") not in REOPENING_OUTCOMES}


def _abandoned_keys(data: dict) -> set:
    """Keys a routine actually CLAIMED and then died on. Eligible again, but LAST.

    Being bumped costs nothing, so a superseded candidate rejoins at its own rank. An abandoned one
    already consumed a slot, and the reason the routine died may well be the candidate itself -- data
    it can never fetch, an API that always times out. Letting it rejoin at full rank means the same
    advance() call that abandons it picks it straight back up, and a lane can retry one dead
    candidate for ever and never research anything else. Behind everything untried, it gets its
    retry without being able to block the queue."""
    return {r["key"] for r in data["history"] if r.get("outcome") == "abandoned"}


def _set_current(state_dir: str, data: dict, key: str, name: str, started_by: str, mode: str,
                 now: datetime, lane: str = "india") -> dict:
    entry = {"key": key, "started": now.date().isoformat(), "started_by": started_by, "in_progress": False, "mode": mode}
    data["current"] = entry
    data["history"] = list(data["history"]) + [{"key": key, "name": name, "queued": now.date().isoformat(),
                                                 "started": entry["started"], "started_by": started_by, "mode": mode,
                                                 "resolved": None, "outcome": None, "experiment_id": None, "branch": None}]
    _save(state_dir, data, lane)
    return entry


def _close_open_row(data: dict, key: str, outcome: str, now: datetime,
                     experiment_id: Optional[str] = None, branch: Optional[str] = None) -> None:
    for row in reversed(data["history"]):
        if row["key"] == key and row["resolved"] is None:
            row["resolved"], row["outcome"] = now.date().isoformat(), outcome
            row["experiment_id"], row["branch"] = experiment_id, branch
            return


def advance(state_dir: str, roadmap: dict, now: Optional[datetime] = None, lane: str = "india",
            exclude: Optional[set] = None) -> Optional[dict]:
    """Keeps `current` pointed at the best available candidate: picks the top-ranked one if nothing is
    queued, swaps it for a better-ranked one if the current pick was itself auto-picked and hasn't
    started yet, and does nothing once research is in_progress (locked until resolve()) -- "research
    already ongoing" is the only thing this refuses to interrupt (2026-09-22, per explicit direction).
    A candidate a human chose by hand (start_now, started_by="manual") is left alone here; only a
    human picking something else, via start_now, moves it off a manual pick. Safe to call as often as
    you like -- every no-op path just returns None.

    `roadmap` should already be scoped to `lane` (research_roadmap.build_roadmap(lane=lane)) -- this
    function doesn't filter it itself, so passing an unfiltered roadmap would let any lane's queue
    pick a candidate that actually belongs to a different lane.

    `exclude` is keys already in the strategy registry (2026-10-05). The roadmap's candidate list
    lags promotions -- a candidate stays in CANDIDATES after it has been built and judged -- so
    without this the queue re-proposes finished work: it picked turnover_liquidity, already REJECTed
    as SW-031, the first time the queue advanced past a stuck candidate. The dashboard has always
    dropped these (roadmap_view's `taken`); the queue simply never did."""
    now = now or datetime.now()
    data = load(state_dir, lane)
    if data["current"] and data["current"].get("in_progress") and not lock_is_stale(data["current"], now):
        return None
    if data["current"] and data["current"].get("in_progress"):
        # The routine that claimed this is gone. Record it as abandoned rather than silently
        # dropping it, so the lane's history says what happened to the week it cost.
        _abandon(state_dir, data, lane, now)
        data = load(state_dir, lane)
    if data["current"] and data["current"].get("started_by") == "manual":
        return None
    resolved = _resolved_keys(data) | set(exclude or ())
    pool = ([(s, "backtest") for s in roadmap.get("researchable_now", [])]
            + [(s, "paper_direct") for s in roadmap.get("paper_direct_eligible", [])])
    pool.sort(key=lambda pair: -pair[0].total_score)
    retry_last = _abandoned_keys(data)
    eligible = [(s, mode) for s, mode in pool if s.candidate.key not in resolved]
    picked = (next(((s, m) for s, m in eligible if s.candidate.key not in retry_last), None)
              or next(iter(eligible), None))
    if picked is None:
        return None
    top, mode = picked[0].candidate, picked[1]
    if data["current"] is not None and data["current"]["key"] == top.key:
        return None   # already the best available pick
    if data["current"] is not None:
        _close_open_row(data, data["current"]["key"], "superseded", now)
    return _set_current(state_dir, data, top.key, top.name, "auto", mode, now, lane)


def start_now(state_dir: str, key: str, roadmap: dict, now: Optional[datetime] = None) -> dict:
    """The manual "start research" button: jump the queue to `key` regardless of rank, any time --
    including bumping whatever's currently queued, auto-picked or manual, as long as research hasn't
    actually started on it yet. Refuses only once research is in_progress (in `key`'s own lane -- a
    different lane being in-progress doesn't block this), or for an unknown/already resolved key, same
    400-on-bad-input convention as advice.tasks.mark_done.

    `roadmap` must be the FULL, unfiltered roadmap (research_roadmap.build_roadmap() with no lane
    argument) -- this looks `key` up in it and determines which lane's queue to write to itself
    (research_roadmap.lane_of()), so the one dashboard button works for a candidate in any lane
    without the caller needing to know which."""
    from swing_research.research_roadmap import lane_of
    now = now or datetime.now()
    match = next((s for s in roadmap.get("all_scored", []) if s.candidate.key == key), None)
    if match is None:
        raise ValueError(f"unknown candidate {key!r}")
    lane = lane_of(match.candidate)
    data = load(state_dir, lane)
    if data["current"] and data["current"].get("in_progress"):
        if not lock_is_stale(data["current"], now):
            raise ValueError(f"already researching {data['current']['key']}")
        _abandon(state_dir, data, lane, now)
        data = load(state_dir, lane)
    if key in _resolved_keys(data):
        raise ValueError(f"{key!r} was already resolved")
    if data["current"] is not None and data["current"]["key"] == key:
        return data["current"]   # already this one
    if data["current"] is not None:
        _close_open_row(data, data["current"]["key"], "superseded", now)
    # A human choosing a blocked candidate by hand is that human's own judgement call, independent of
    # whether it clears the automatic paper_direct_eligible floor.
    mode = "paper_direct" if match.feasibility_classification == "NOT_CURRENTLY_IMPLEMENTABLE" else "backtest"
    return _set_current(state_dir, data, match.candidate.key, match.candidate.name, "manual", mode, now, lane)


# How long a claimed candidate may stay locked before the lock is treated as abandoned.
#
# WHY THERE IS A LIMIT AT ALL. The lock exists so a run in progress is not interrupted, and it is
# released by resolve() when the routine finishes. But a routine that dies -- a sandbox that loses
# the network, a crash, a cloud run that never reports back -- never calls resolve(), and the lock
# has no other way out. The crypto lane sat locked on crypto_illiquidity_premium from 2026-10-04 to
# 2026-10-07 for exactly that reason: every later fire saw in_progress and did nothing, and the
# dashboard said "Researching now" for three days about a run that had long since died.
#
# Two days is comfortably longer than any real run (each lane fires three times in one night) and
# short enough that one dead routine costs one week, not every week after it.
STALE_LOCK_DAYS = 2


def lock_age_days(current: Optional[dict], now: Optional[datetime] = None) -> Optional[float]:
    """How long `current` has been claimed, in days, or None if it is not claimed or has no date."""
    if not current or not current.get("in_progress"):
        return None
    stamp = current.get("in_progress_since") or current.get("started")
    if not stamp:
        return None
    try:
        started = datetime.fromisoformat(str(stamp))
    except ValueError:
        try:
            started = datetime.combine(date.fromisoformat(str(stamp)[:10]), datetime.min.time())
        except ValueError:
            return None
    return max(0.0, ((now or datetime.now()) - started).total_seconds() / 86400.0)


def lock_is_stale(current: Optional[dict], now: Optional[datetime] = None) -> bool:
    """True when a claim has been held so long that the routine holding it must be gone."""
    age = lock_age_days(current, now)
    return age is not None and age >= STALE_LOCK_DAYS


def mark_in_progress(state_dir: str, key: str, now: Optional[datetime] = None, lane: str = "india") -> None:
    """Called the instant a candidate is claimed (research_queue_github_sync.py, on seeing its
    `research/<key>` branch appear on GitHub) -- locks `current` so advance()/start_now() can no
    longer bump it. Raises if `key` is no longer the current pick (it may have been superseded, or
    resolved, before the claim was noticed)."""
    now = now or datetime.now()
    data = load(state_dir, lane)
    if not data["current"] or data["current"]["key"] != key:
        raise ValueError(f"{key!r} is not the current candidate")
    data["current"]["in_progress"] = True
    # stamped so staleness is measured from the CLAIM, not from when the candidate was picked
    data["current"]["in_progress_since"] = now.isoformat(timespec="seconds")
    _save(state_dir, data, lane)


def _abandon(state_dir: str, data: dict, lane: str, now: Optional[datetime] = None) -> None:
    """Moves a stale claim into history as `abandoned` and clears `current`, so the lane can move on.

    Recorded, never silently dropped: a week of research was lost and the history is the only place
    that will ever say so."""
    now = now or datetime.now()
    current = data.get("current") or {}
    entry = dict(current)
    entry.update({"resolved": now.date().isoformat(), "outcome": "abandoned",
                  "experiment_id": None, "branch": None,
                  "note": f"claimed {lock_age_days(current, now):.1f} days ago and never reported back"})
    data.setdefault("history", []).append(entry)
    data["current"] = None
    _save(state_dir, data, lane)


def build_snapshot(state_dir: str, roadmap: dict, lane: str = "india") -> dict:
    """A small, public-safe snapshot of just `lane`'s queue's `current` pick
    -- published to GitHub by research_queue_github_sync.py so the
    unattended research routine's cloud sandbox can read it from its own
    git clone of this repo instead of reaching this VPS directly (added
    2026-10-03, replacing the routine's old direct-HTTP GET of /api/state:
    two real runs, 2026-09-27 and 2026-09-28, both confirmed the sandbox
    cannot reach this VPS on any port/protocol at all -- a TCP-level
    connection timeout, not a cert or TLS problem -- while GitHub access
    from the same sandbox has never failed). Deliberately tiny: just what
    step 1 of the routine's prompt actually extracts, nothing about any
    other candidate, no capital/P&L.

    `roadmap` should be scoped to `lane`, same requirement as advance()."""
    data = load(state_dir, lane)
    current = data.get("current")
    if not current:
        return {"current": None}
    match = next((s.candidate for s in roadmap.get("all_scored", []) if s.candidate.key == current["key"]), None)
    return {"current": {
        "key": current["key"],
        "name": match.name if match else current["key"],
        "mode": current.get("mode", "backtest"),
        "in_progress": bool(current.get("in_progress")),
        "stale": lock_is_stale(current),
        "claimed_days_ago": lock_age_days(current),
        "horizon_lane": match.horizon_lane if match else None,
    }}


def resolve(state_dir: str, key: str, outcome: str, experiment_id: Optional[str] = None,
            branch: Optional[str] = None, now: Optional[datetime] = None, lane: str = "india") -> None:
    """Called once the research routine finishes with `key` (whatever the result): clears `current`
    and fills in its history row. outcome is "researched" (mode="backtest" -- a real backtest ran,
    PASS or REJECT, experiment_id/branch identify it), "paper_trading_proposed" (mode="paper_direct" --
    no backtest was possible, but a paper-trading pool was implemented and proposed in a PR, branch
    identifies it) or "skipped" (nobody got to it / it was abandoned, either mode)."""
    if outcome not in ("researched", "paper_trading_proposed", "skipped"):
        raise ValueError("outcome must be 'researched', 'paper_trading_proposed' or 'skipped'")
    now = now or datetime.now()
    data = load(state_dir, lane)
    if not data["current"] or data["current"]["key"] != key:
        raise ValueError(f"{key!r} is not the current candidate")
    data["current"] = None
    _close_open_row(data, key, outcome, now, experiment_id, branch)
    _save(state_dir, data, lane)
