"""
Is research actually moving? One green/amber/red verdict per lane, per stage, every day.

WHY THIS EXISTS (2026-10-22, per explicit direction: "we need an agent or a system who makes sure
research pipeline is working smoothly"). The unattended research pipeline stalled for about eight
days and nothing said so where anyone would see it:

  * India: the queued candidate (intraday_momentum_half_hour) is really an intraday strategy -- it
    needs Kite intraday bars -- but was labelled horizon_lane="swing", so it was queued for the
    weekly cloud routine, which is not allowed to do intraday. The routine correctly refused on
    three Saturday-night fires and said so only in a Claude-app push nobody sees. The queue never
    moved.
  * US and crypto: the routine DID implement both candidates, but research_merge_gate.py runs the
    test suite in a throwaway worktree that has no config/settings.py (git-ignored), so the suite
    failed with ~121 errors even on clean main and every merge was refused. Its Telegram had been
    muted three days earlier.
  * check_research_queue.py noticed India was stuck, and its Telegram was muted too.

Every individual piece did its job. Nothing watched the pipeline as a whole. This does.

THE STAGES, per lane (research_queue.LANES), each green / amber / red with one plain sentence:

  queue     a candidate is queued, the routine is ALLOWED to do it (judged from the candidate's real
            data needs and horizon, not just its horizon_lane label -- this week's mislabel is red),
            and it hasn't sat unclaimed for longer than the weekly cadence explains.
  routine   once the lane's weekly fire has passed (plus a day for the six-hourly GitHub sync to see
            the branch), the routine claimed what was queued.
  merge     the merge gate merged the delivered branch, or has nothing waiting; red if it refused the
            same branch two runs running, or if the gate's own test setup is broken (--self-test).
  backtest  the nightly 02:30 run_queued_backtest.py ran recently and didn't crash.
  outcome   the lane produced a research result recently.

A lane's overall status is its worst stage. The latest evaluation is written to
deployment/state/research_pipeline_health.json (atomically), which the dashboard's Research tab reads;
the same file remembers the date each lane turned red and whether that has been reported, so a lane
that stays red is announced once, and its recovery once.

WHAT IT REPLACES. check_research_queue.py's three checks (stuck queued, stuck in progress, nothing
queued) are folded in here -- queue/routine stages -- and its Telegram stays muted where it is. Its
cron line can be removed once this one is running.

WHAT IT NEVER DOES. It reads state and logs, and (--self-test) builds and removes a throwaway worktree.
It never changes the queue, the registry, a branch, a service or anything that trades.

THE LOGS HAVE NO TIMESTAMPS. auto_merge_research.py and run_queued_backtest.py print plain lines, so
"when did it last run" is the log file's mtime and "what happened" is parsed from the lines
themselves. auto_merge_research.py has no structured record of its verdicts and is not this file's to
change, so the parsed merge history is stored here, in the health JSON, not by the gate.

    python research_pipeline_health.py                  # evaluate and print, write the JSON
    python research_pipeline_health.py --send           # ...and Telegram a lane that turned red/recovered
    python research_pipeline_health.py --self-test      # ...first run the clean-main merge-gate self-test

Cron (IST). The self-test costs ~60-90 s and a few hundred MB on a 458 MB box, so it runs once a day,
niced, in a quiet slot well clear of the 02:30 backtest -- and it skips itself if that backtest is
still running:
    30 3 * * *  ... research_pipeline_health.py --self-test >> logs/research_pipeline_health.log 2>&1
    20 9 * * *  ... research_pipeline_health.py --send      >> logs/research_pipeline_health.log 2>&1
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import research_queue

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
HEALTH_FILE = "research_pipeline_health.json"
LOG_NAME = "research_pipeline_health.log"

STAGES = ("queue", "routine", "merge", "backtest", "outcome")
STAGE_LABELS = {"queue": "Queue", "routine": "Routine", "merge": "Merge", "backtest": "Backtest",
                "outcome": "Outcome"}
LANE_SHORT = {"india": "India", "crypto": "Crypto", "us": "US"}
_RANK = {"green": 0, "amber": 1, "red": 2}

# When each lane's cloud routine fires: 00:00 IST at the start of this weekday (Python weekday()),
# with follow-up fires at 02:00 and 04:00. The routines are claude.ai triggers, not anything in this
# repo; the one place the repo records their schedule is dashboard/state_view.RESEARCH_RUN_WEEKDAY,
# copied here so this script doesn't import the whole dashboard (a test keeps the two equal).
ROUTINE_FIRE_WEEKDAY = {"india": 6, "crypto": 1, "us": 3}     # Sunday / Tuesday / Thursday
CLAIM_GRACE = timedelta(days=1)       # fire -> branch pushed -> six-hourly sync locks it in

QUEUE_AMBER_DAYS, QUEUE_RED_DAYS = 8, 15            # unclaimed, against a weekly cadence
NOTHING_QUEUED_HOURS = 18                            # advance_research_queue.py refills every 6 h
OUTCOME_AMBER_DAYS, OUTCOME_RED_DAYS = 10, 17
BACKTEST_AMBER_HOURS, BACKTEST_RED_HOURS = 26, 50   # nightly at 02:30
MERGE_STALE_HOURS = 50
REFUSALS_FOR_RED = 2
SELF_TEST_VALID_DAYS = 3             # an old self-test result says nothing about today's main

# Where the nightly jobs write. These are cron redirections on the VPS, not anything the scripts
# choose, so the first existing name wins. Both jobs share one cron line ("merge first, then
# back-test"), so their output may be in one file -- the parsers below only look at their own lines.
MERGE_LOGS = ("research_merge.log", "queued_backtest.log", "research_backtest.log")
BACKTEST_LOGS = ("queued_backtest.log", "research_backtest.log", "run_queued_backtest.log",
                 "research_merge.log")

# What the weekly cloud routine cannot do: it has no live broker session (so no Kite intraday bars)
# and its sandbox cannot reach this VPS. A candidate needing any of these is not routine work,
# whatever its horizon_lane says.
ROUTINE_BLOCKED_DATA = {"intraday_bar_history": "Kite intraday bars",
                        "intraday_tick_data": "tick data", "order_book_data": "order-book data"}


def _stage(status: str, reason: str, days: Optional[int] = None) -> dict:
    out = {"status": status, "reason": reason}
    if days is not None:
        out["days"] = int(days)
    return out


def _worst(stages: dict) -> str:
    return max((s["status"] for s in stages.values()), key=lambda s: _RANK.get(s, 1), default="green")


def _parse_day(stamp) -> Optional[date]:
    if not stamp:
        return None
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


def _days_since(stamp, now: datetime) -> Optional[int]:
    d = _parse_day(stamp)
    return None if d is None else (now.date() - d).days


# ---------------------------------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------------------------------

def find_candidate(key: str):
    """The roadmap's CandidateProfile for `key`, or None. Never raises."""
    try:
        from swing_research.research_roadmap import CANDIDATES
        return next((c for c in CANDIDATES if c.key == key), None)
    except Exception:
        return None


def routine_blocker(candidate) -> Optional[str]:
    """Why the weekly cloud routine can't research this candidate, or None if it can.

    Judged from what the candidate NEEDS, not only its label: this week's stall was a candidate
    labelled "swing" whose data requirements and holding period were plainly intraday."""
    if candidate is None:
        return None
    needs = [ROUTINE_BLOCKED_DATA[t] for t in (getattr(candidate, "data_requirements", None) or [])
             if t in ROUTINE_BLOCKED_DATA]
    lane = str(getattr(candidate, "horizon_lane", "") or "")
    text = " ".join(str(getattr(candidate, f, "") or "") for f in ("asset_class", "typical_holding_period")).lower()
    same_day = (getattr(candidate, "holding_days_max", None) or 99) <= 1 and (
        "intraday" in text or "same trading session" in text or "same session" in text)
    if lane == "intraday" or needs or same_day:
        what = f"needs {needs[0]}" if needs else "is same-session"
        label = "" if lane == "intraday" else f" (labelled {lane})"
        return f"queued candidate is intraday{label} and {what} -- the routine can't do it"
    return None


# ---------------------------------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------------------------------

def _pick_log(logs_dir: str, names) -> Optional[str]:
    for name in names:
        path = os.path.join(logs_dir, name)
        if os.path.exists(path):
            return path
    return None


def _read_lines(path: Optional[str], limit: int = 4000) -> list:
    if not path:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-limit:]
    except OSError:
        return []


def _mtime(path: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)) if path else None
    except OSError:
        return None


_NEEDS_YOU = re.compile(r"^\*?Research needs you\*?\s*\((\w+)\)\s*$")
_MERGED = re.compile(r"^\*?Research merged automatically\*?\s*\((\w+)\)\s*$")
_NOTHING = re.compile(r"^\[(\w+)\] nothing waiting to merge")
_GATE_FAILED = re.compile(r"^\[(\w+)\] merge gate failed: (.*)$")


def parse_merge_log(lines: list) -> dict:
    """lane -> its merge-gate events in order: {"event": refused|merged|nothing|error, "key", "reason"}.

    Each nightly run prints exactly one result per lane, so a lane's events ARE its runs -- which is
    what "refused N runs in a row" needs, with no timestamps to go on."""
    events = {lane: [] for lane in research_queue.LANES}

    def nxt(i):
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        return j

    for i, raw in enumerate(lines):
        line = raw.strip()
        m = _NEEDS_YOU.match(line)
        if m:
            j = nxt(i)
            key = lines[j].strip() if j < len(lines) else ""
            reason = ""
            for k in range(j + 1, min(len(lines), j + 40)):
                if lines[k].strip().startswith("It was NOT merged:"):
                    reason = lines[k].strip()[len("It was NOT merged:"):].strip()
                    break
            events.setdefault(m.group(1), []).append({"event": "refused", "key": key, "reason": reason})
            continue
        m = _MERGED.match(line)
        if m:
            j = nxt(i)
            events.setdefault(m.group(1), []).append(
                {"event": "merged", "key": lines[j].strip() if j < len(lines) else "", "reason": ""})
            continue
        m = _NOTHING.match(line)
        if m:
            events.setdefault(m.group(1), []).append({"event": "nothing", "key": "", "reason": ""})
            continue
        m = _GATE_FAILED.match(line)
        if m:
            events.setdefault(m.group(1), []).append({"event": "error", "key": "", "reason": m.group(2)})
    return events


def refusal_streak(events: list, key: str) -> tuple:
    """(how many of the lane's most recent runs in a row refused `key`, the latest reason)."""
    n, reason = 0, ""
    for e in reversed(events):
        if e["event"] == "refused" and e["key"] == key:
            n += 1
            reason = reason or e["reason"]
        else:
            break
    return n, reason


_BT_START = re.compile(r"^(\[india\] (nothing to back-test|running )|another backtest is already running)")
_CLI_FAILED = re.compile(r"^\[(\w+)\] (\S+): the experiment CLI exited (-?\d+)")


def parse_backtest_log(lines: list) -> dict:
    """The last nightly run's story: {"crashed": last traceback line or "", "cli_failed": {lane: (key, code)},
    "ran": [lanes that printed anything]}. The run starts at its India line (every run without --lane
    prints one first) -- or, if it crashed before getting that far, the traceback sits after the
    previous run's lines and is still in the tail."""
    start = 0
    for i, line in enumerate(lines):
        if _BT_START.match(line.strip()):
            start = i
    tail = lines[start:]
    out = {"crashed": "", "cli_failed": {}, "ran": []}
    saw_cli_failure = False
    for i, raw in enumerate(tail):
        line = raw.strip()
        m = _CLI_FAILED.match(line)
        if m:
            saw_cli_failure = True
            out["cli_failed"][m.group(1)] = (m.group(2), int(m.group(3)))
            continue
        lm = re.match(r"^\[(\w+)\] ", line)
        if lm and lm.group(1) not in out["ran"]:
            out["ran"].append(lm.group(1))
        if line.startswith("Traceback") and not saw_cli_failure:
            last = next((l.strip() for l in reversed(tail[i:]) if l.strip() and not l.startswith(" ")), "Traceback")
            out["crashed"] = last[:120]
    return out


# ---------------------------------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------------------------------

def last_fire(lane: str, now: datetime) -> datetime:
    """The most recent 00:00 IST fire of `lane`'s weekly routine at or before `now`."""
    days = (now.weekday() - ROUTINE_FIRE_WEEKDAY[lane]) % 7
    return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def _open_row(queue: dict, key: str) -> dict:
    return next((h for h in reversed(queue.get("history", []))
                 if h.get("key") == key and h.get("resolved") is None), {})


def queue_stage(lane: str, queue: dict, now: datetime, candidate=None) -> dict:
    current = queue.get("current")
    if not current:
        stamps = [d for h in queue.get("history", [])
                  for d in (_parse_day(h.get("resolved")), _parse_day(h.get("started"))) if d]
        hours = None
        if stamps:
            hours = (now - datetime.combine(max(stamps), datetime.min.time())).total_seconds() / 3600
        if hours is not None and hours < NOTHING_QUEUED_HOURS:
            return _stage("green", "nothing queued yet -- the six-hourly queue job picks the next one")
        return _stage("amber", "nothing queued -- the queue job isn't refilling it, or the lane is out of "
                               "candidates", days=None if hours is None else hours // 24)
    key = current["key"]
    blocker = routine_blocker(candidate)
    waited = _days_since(current.get("started"), now) or 0
    if blocker and not current.get("in_progress"):
        return _stage("red", blocker, days=waited)
    if current.get("in_progress"):
        return _stage("green", f"{key} claimed")
    if waited > QUEUE_RED_DAYS:
        return _stage("red", f"{key} unclaimed for {waited} days", days=waited)
    if waited > QUEUE_AMBER_DAYS:
        return _stage("amber", f"{key} unclaimed for {waited} days -- past a weekly cycle", days=waited)
    return _stage("green", f"{key} queued {waited} day{'s' if waited != 1 else ''} ago", days=waited)


def routine_stage(lane: str, queue: dict, now: datetime, implemented: set, delivered_keys: set) -> dict:
    current = queue.get("current")
    fire = last_fire(lane, now)
    if not current:
        return _stage("green", "nothing queued for it to claim")
    key = current["key"]
    if current.get("in_progress"):
        if key in implemented or key in delivered_keys:
            return _stage("green", f"{key} claimed and delivered")
        if research_queue.lock_is_stale(current, now):
            held = int(research_queue.lock_age_days(current, now) or 0)
            return _stage("amber", f"{key} claimed {held} days ago and nothing delivered -- the run may have "
                                   "died; open it on claude.ai", days=held)
        return _stage("green", f"{key} claimed, being written")
    queued = _parse_day(current.get("started"))
    if queued is not None and queued <= fire.date() and now >= fire + CLAIM_GRACE:
        return _stage("amber", f"routine ran {fire:%a %d %b} but didn't claim -- open its run on claude.ai "
                               "to see why", days=(now - fire).days)
    nxt = fire + timedelta(days=7)
    return _stage("green", f"waiting for the {nxt:%a %d %b} 00:00 fire")


def merge_stage(lane: str, queue: dict, now: datetime, implemented: set, events: list,
                log_seen: Optional[datetime], self_test: Optional[dict]) -> dict:
    if self_test and self_test.get("ok") is False:
        ran = _parse_day(self_test.get("ran_at"))
        if ran is not None and (now.date() - ran).days <= SELF_TEST_VALID_DAYS:
            return _stage("red", "merge gate's own test setup is broken (clean main fails)")
    current = queue.get("current") or {}
    key = current.get("key")
    if not key or not current.get("in_progress"):
        return _stage("green", "nothing waiting to merge")
    if key in implemented:
        return _stage("green", f"{key} merged to main")
    streak, reason = refusal_streak(events, key)
    why = (reason or "see logs/research_merge.log").split(" -- ")[0][:80]
    if streak >= REFUSALS_FOR_RED:
        return _stage("red", f"gate refused {key} {streak} runs in a row: {why}", days=streak)
    if streak == 1:
        return _stage("amber", f"gate refused {key} once: {why}")
    if events and events[-1]["event"] == "error":
        return _stage("amber", f"merge gate errored: {events[-1]['reason'][:80]}")
    if log_seen is None:
        return _stage("amber", "no merge-gate log found -- is its nightly cron there?")
    hours = (now - log_seen).total_seconds() / 3600
    if hours > MERGE_STALE_HOURS:
        return _stage("amber", f"merge gate hasn't run since {log_seen:%d %b}", days=int(hours // 24))
    return _stage("green", f"{key} not delivered yet -- nothing to merge")


def backtest_stage(lane: str, parsed: dict, log_seen: Optional[datetime], now: datetime) -> dict:
    if log_seen is None:
        return _stage("amber", "no backtest log found -- is the 02:30 cron there?")
    hours = (now - log_seen).total_seconds() / 3600
    if hours > BACKTEST_RED_HOURS:
        return _stage("red", f"nightly backtest hasn't run since {log_seen:%d %b}", days=int(hours // 24))
    if parsed.get("crashed"):
        return _stage("red", f"last nightly backtest crashed: {parsed['crashed']}")
    failed = (parsed.get("cli_failed") or {}).get(lane)
    if failed:
        return _stage("amber", f"backtest of {failed[0]} failed (exit {failed[1]}) -- it retries tonight")
    if hours > BACKTEST_AMBER_HOURS:
        return _stage("amber", f"nightly backtest last ran {log_seen:%d %b %H:%M}")
    return _stage("green", f"ran {log_seen:%d %b %H:%M}")


def outcome_stage(lane: str, queue: dict, now: datetime) -> dict:
    history = queue.get("history", [])
    done = [h for h in history if h.get("outcome") in ("researched", "paper_trading_proposed", "retest_needed") and h.get("resolved")]
    if done:
        last = max(done, key=lambda h: str(h["resolved"]))
        days = _days_since(last["resolved"], now) or 0
        what = f"last result {days} day{'s' if days != 1 else ''} ago ({last.get('key')})"
    else:
        starts = [h.get("queued") or h.get("started") for h in history if h.get("queued") or h.get("started")]
        if not starts:
            return _stage("amber", "no research result yet")
        days = _days_since(min(starts), now) or 0
        what = f"no result yet in {days} days"
    if days > OUTCOME_RED_DAYS:
        return _stage("red", what, days=days)
    if days > OUTCOME_AMBER_DAYS:
        return _stage("amber", what, days=days)
    return _stage("green", what, days=days)


def _safe(fn, *args) -> dict:
    """A stage that can't be evaluated is amber with the reason -- never a crashed health check."""
    try:
        return fn(*args)
    except Exception as e:                                  # noqa: BLE001
        return _stage("amber", f"check failed: {type(e).__name__}: {str(e)[:80]}")


def _default_implemented(lane: str) -> set:
    try:
        from run_queued_backtest import implemented_keys
        return implemented_keys(lane)
    except Exception:
        return set()


def evaluate(state_dir: str, logs_dir: str, now: Optional[datetime] = None,
             implemented: Optional[Callable[[str], set]] = None, self_test: Optional[dict] = None,
             find: Callable = find_candidate, queues: Optional[dict] = None) -> dict:
    """Every lane's stages and overall status. Pure apart from reading `state_dir`/`logs_dir` (and the
    experiment catalog via `implemented`); writes nothing."""
    now = now or datetime.now()
    implemented = implemented or _default_implemented
    merge_log = _pick_log(logs_dir, MERGE_LOGS)
    backtest_log = _pick_log(logs_dir, BACKTEST_LOGS)
    merge_events = parse_merge_log(_read_lines(merge_log))
    backtest = parse_backtest_log(_read_lines(backtest_log))
    lanes = {}
    for lane in research_queue.LANES:
        queue = (queues or {}).get(lane) or research_queue.load(state_dir, lane)
        current = queue.get("current") or {}
        try:
            impl = set(implemented(lane))
        except Exception:
            impl = set()
        events = merge_events.get(lane, [])
        delivered = {e["key"] for e in events if e["event"] in ("refused", "merged")}
        candidate = find(current["key"]) if current.get("key") else None
        stages = {
            "queue": _safe(queue_stage, lane, queue, now, candidate),
            "routine": _safe(routine_stage, lane, queue, now, impl, delivered),
            "merge": _safe(merge_stage, lane, queue, now, impl, events, _mtime(merge_log), self_test),
            "backtest": _safe(backtest_stage, lane, backtest, _mtime(backtest_log), now),
            "outcome": _safe(outcome_stage, lane, queue, now),
        }
        lanes[lane] = {"status": _worst(stages), "candidate": current.get("key"), "stages": stages}
    return {"checked_at": now.isoformat(timespec="seconds"), "lanes": lanes,
            "logs": {"merge": os.path.basename(merge_log) if merge_log else None,
                     "backtest": os.path.basename(backtest_log) if backtest_log else None},
            "merge_history": {lane: merge_events.get(lane, [])[-10:] for lane in research_queue.LANES}}


# ---------------------------------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------------------------------

MAX_MESSAGE = 160


def red_message(lane: str, lane_eval: dict) -> str:
    worst = next((lane_eval["stages"][s] for s in STAGES if lane_eval["stages"][s]["status"] == "red"), None) \
        or {"reason": "a stage is red", "days": None}
    reason = worst["reason"].replace(" -- ", ", ")
    days = worst.get("days")
    tail = f" ({days} days)" if days and not re.search(r"\bdays?\b", reason) else ""
    msg = f"⚠️ Research stuck — {LANE_SHORT.get(lane, lane)} lane: {reason}{tail}"
    return msg if len(msg) <= MAX_MESSAGE else msg[:MAX_MESSAGE - 1] + "…"


def recovered_message(lane: str) -> str:
    return f"✅ Research moving again — {LANE_SHORT.get(lane, lane)} lane"


def plan_alerts(evaluation: dict, alerts: dict, now: datetime, send: bool) -> tuple:
    """(new alert state, [(lane, message)] due now). `alerts` is lane -> {"red_since", "notified"}.

    red_since is kept whether or not this run sends (the dashboard shows it). "notified" only changes
    when a message actually goes out, so a run without --send (the 03:30 self-test) can never swallow
    the alert the 09:20 run is due to send."""
    alerts = {k: dict(v) for k, v in (alerts or {}).items()}
    due = []
    for lane, ev in evaluation["lanes"].items():
        a = alerts.setdefault(lane, {"red_since": None, "notified": False})
        if ev["status"] == "red":
            a["red_since"] = a.get("red_since") or now.date().isoformat()
            if not a.get("notified"):
                due.append((lane, red_message(lane, ev)))
                if send:
                    a["notified"] = True
        else:
            a["red_since"] = None
            if a.get("notified"):
                due.append((lane, recovered_message(lane)))
                if send:
                    a["notified"] = False
    return alerts, due


# ---------------------------------------------------------------------------------------------------
# Merge-gate self-test
# ---------------------------------------------------------------------------------------------------

def _git(args: list, cwd: Optional[str] = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd or REPO_DIR, capture_output=True, text=True, timeout=timeout)


def prepare_worktree(tree: str) -> None:
    """Whatever the merge gate does to a fresh worktree before running the suite in it.

    If research_merge_gate grows its own prepare step (the fix for the missing config/settings.py,
    2026-10-22), that one is used, so this test can never pass on a setup the gate doesn't have.
    Until then: the git-ignored config/settings.py is copied from the tracked example."""
    try:
        import research_merge_gate
        own = getattr(research_merge_gate, "prepare_worktree", None)
    except Exception:
        own = None
    if callable(own):
        own(tree)
        return
    src = os.path.join(tree, "config", "settings.example.py")
    dst = os.path.join(tree, "config", "settings.py")
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copyfile(src, dst)


def _run_suite(cwd: str, timeout: int = 2400) -> tuple:
    """The merge gate's own runner (same PATH for node, same single re-run of a flaky test), so the
    self-test can never call clean main green or red for a reason the gate would not share. The
    03:30 cron line runs this whole script under nice, which the suite inherits."""
    import research_merge_gate
    passed, tail = research_merge_gate._run_suite(cwd, timeout)
    lines = tail.strip().splitlines()
    return passed, (lines[-1] if lines else "")


def run_self_test(now: Optional[datetime] = None, git: Callable = _git, run_suite: Callable = _run_suite,
                  prepare: Callable = prepare_worktree, busy: Optional[Callable[[], bool]] = None) -> dict:
    """Builds a throwaway worktree of clean origin/main, sets it up the way the merge gate does, runs
    the suite there. If clean main fails, every merge the gate attempts will fail too -- which is
    exactly how US and crypto research sat refused for a week. The worktree is ALWAYS removed."""
    now = now or datetime.now()
    result = {"ran_at": now.isoformat(timespec="seconds"), "ok": None, "summary": ""}
    if busy is not None and busy():
        result["summary"] = "skipped: the nightly backtest is still running"
        return result
    tmp = tempfile.mkdtemp(prefix="research-health-selftest-")
    tree = os.path.join(tmp, "work")
    try:
        git(["fetch", "--quiet", "origin"])
        add = git(["worktree", "add", "--detach", tree, "origin/main"])
        if add.returncode != 0:
            result.update(ok=None, summary=f"could not build a worktree: {(add.stderr or '').strip()[:120]}")
            return result
        prepare(tree)
        passed, last = run_suite(tree)
        result.update(ok=bool(passed), summary=(last or ("suite green" if passed else "suite failed"))[:160])
        return result
    except subprocess.TimeoutExpired:
        result.update(ok=None, summary="timed out")
        return result
    except Exception as e:                                  # noqa: BLE001
        result.update(ok=None, summary=f"{type(e).__name__}: {str(e)[:120]}")
        return result
    finally:
        try:
            git(["worktree", "remove", "--force", tree], timeout=120)
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            git(["worktree", "prune"], timeout=60)
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------------------------------

def load(state_dir: Optional[str]) -> Optional[dict]:
    """The last saved evaluation, or None. Never raises -- the dashboard calls this on every build."""
    if not state_dir:
        return None
    try:
        import json
        with open(os.path.join(state_dir, HEALTH_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def save(state_dir: str, data: dict) -> None:
    from deployment.atomic_write import write_json
    write_json(os.path.join(state_dir, HEALTH_FILE), data)


def run(state_dir: str, logs_dir: str, send: bool = False, self_test: bool = False,
        now: Optional[datetime] = None, sender: Optional[Callable[[str], None]] = None,
        tester: Callable = run_self_test, **evaluate_kwargs) -> dict:
    """One full cycle: (optional self-test) -> evaluate -> alerts -> save. Returns what was saved."""
    now = now or datetime.now()
    previous = load(state_dir) or {}
    st = previous.get("self_test")
    if self_test:
        try:
            from run_queued_backtest import running_claim
            busy = lambda: bool(running_claim())        # noqa: E731
        except Exception:
            busy = None
        st = tester(now=now, busy=busy)
        if st.get("ok") is None and previous.get("self_test", {}).get("ok") is not None:
            st = {**previous["self_test"], "last_attempt": st}   # keep the last real answer
    evaluation = evaluate(state_dir, logs_dir, now=now, self_test=st, **evaluate_kwargs)
    alerts, due = plan_alerts(evaluation, previous.get("alerts", {}), now, send)
    for lane, ev in evaluation["lanes"].items():
        ev["red_since"] = alerts.get(lane, {}).get("red_since")
    for lane, msg in due:
        print(msg, flush=True)
        if send and sender is not None:
            sender(msg)
    data = {**evaluation, "self_test": st, "alerts": alerts}
    save(state_dir, data)
    return data


def _telegram_sender():
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    from reporting.telegram_notifier import send_telegram_message
    return lambda msg: send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                             kind="research_alert", parse_mode=None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="Telegram a lane that turned red, or recovered")
    ap.add_argument("--self-test", action="store_true",
                    help="first run the suite on clean origin/main the way the merge gate would (~90 s)")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--logs-dir", default=None)
    args = ap.parse_args()
    from deployment.settings import STATE_DIR
    state_dir = args.state_dir or STATE_DIR
    logs_dir = args.logs_dir or os.path.join(REPO_DIR, "logs")
    data = run(state_dir, logs_dir, send=args.send, self_test=args.self_test,
               sender=_telegram_sender() if args.send else None)
    if args.self_test:
        st = data.get("self_test") or {}
        print(f"self-test: ok={st.get('ok')} {st.get('summary', '')}", flush=True)
    for lane, ev in data["lanes"].items():
        print(f"[{lane}] {ev['status'].upper()}", flush=True)
        for s in STAGES:
            print(f"    {s:<9} {ev['stages'][s]['status']:<6} {ev['stages'][s]['reason']}", flush=True)


if __name__ == "__main__":
    main()
