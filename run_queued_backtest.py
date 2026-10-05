"""
Runs the real backtest for a research candidate the unattended routine has
already implemented -- on this VPS, not in the routine's cloud sandbox.

WHY THIS EXISTS (2026-10-05). The weekly research routine used to do the whole
job itself: claim a candidate, implement it, backtest it, and report a verdict
in its PR title. The backtest step is impossible where it runs. Its sandbox's
egress proxy returns a hard, persistent 403 for Yahoo Finance
(query2.finance.yahoo.com, guce.yahoo.com), which is yfinance's data source --
confirmed from a real run's proxy log on 2026-10-03, not inferred. The routine
did the right thing when it hit that: it refused to invent a verdict, pushed
the finished implementation, and opened no PR. But `current` stays locked from
the moment a candidate is claimed until resolve() clears it, so the India queue
then sat in_progress from 2026-09-22 until a human cleared it on 2026-10-05,
no-opping every scheduled fire in between. The same wall would stop every
future run, because nothing about that sandbox's network policy is going to
change.

This VPS can reach Yahoo Finance (verified) and already fetches from it daily
for live paper trading, so the backtest belongs here. Division of labour now:
the routine writes the strategy, this runs it.

MERGE-GATED ON PURPOSE. This never checks out or executes a research branch.
It only runs a candidate whose implementation is already merged into main --
i.e. a human has read the code before this machine executes it. That matters:
the same box runs live paper trading, and an unattended agent's unreviewed code
should not execute next to it. The cost is a human merge between implementation
and verdict; the alternative was running agent-written code on the production
box sight-unseen, which is not a trade worth making. "Is it merged?" is asked
by looking for the candidate's key in its lane's experiment catalog -- the
routine registers it there as part of implementing it, so presence IS the
signal, with no extra marker file to keep in sync.

One candidate at a time, guarded by a lock file: a full walk-forward over the
457-symbol universe takes a long while, and the cron that calls this fires
every 6 hours.

The US lane has no experiment CLI yet (only two hand-written Pool I wrappers in
research_director.py), so it is deliberately absent from LANE_RUNNERS below and
is skipped with a clear message rather than failing.
"""

import argparse
import os
import re
import subprocess
import sys

import research_queue

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
EXPERIMENTS_DIR = os.path.join(REPO_DIR, "swing_research", "experiments")
LOCK_PATH = os.path.join(REPO_DIR, "deployment", "state", "queued_backtest.lock")

# lane -> (CLI script, how to list the strategy keys that CLI can actually run)
LANE_RUNNERS = {
    "india": ("run_swing_experiment.py",
              lambda: {s.strategy_key for s in
                       __import__("swing_research.strategy_catalog",
                                  fromlist=["RESEARCH_EXPERIMENT_SPECS"]).RESEARCH_EXPERIMENT_SPECS}),
    "crypto": ("run_crypto_experiment.py",
               lambda: set(__import__("run_crypto_experiment", fromlist=["RUNNERS"]).RUNNERS)),
}


def implemented_keys(lane: str) -> set:
    """Which candidates this lane can actually backtest right now -- i.e. whose implementation has
    been merged to main. Returns an empty set (never raises) for a lane with no CLI yet."""
    runner = LANE_RUNNERS.get(lane)
    if not runner:
        return set()
    try:
        return runner[1]()
    except Exception as e:                                    # a half-merged catalog shouldn't kill the cron
        print(f"[{lane}] could not read the experiment catalog ({type(e).__name__}: {e})", flush=True)
        return set()


def pending_candidate(state_dir: str, lane: str) -> tuple:
    """(key, reason_it_is_not_runnable). key is None when there is nothing to do."""
    data = research_queue.load(state_dir, lane)
    current = data.get("current")
    if not current:
        return None, "nothing queued"
    if not current.get("in_progress"):
        return None, f"{current['key']} is queued but not claimed yet"
    if current.get("mode") != "backtest":
        return None, f"{current['key']} is mode={current.get('mode')} -- no backtest is possible for it"
    row = next((r for r in reversed(data["history"])
                if r["key"] == current["key"] and r.get("resolved") is None), None)
    if row and row.get("experiment_id"):
        return None, f"{current['key']} already has {row['experiment_id']}"
    if current["key"] not in implemented_keys(lane):
        return None, f"{current['key']} is claimed but its implementation is not merged to main yet"
    return current["key"], ""


def read_verdict(exp_id: str) -> str:
    """PASS / REJECT / INCONCLUSIVE, read from the experiment's own verdict.md -- never inferred from
    the runner's stdout, which interleaves warnings and benchmark tables."""
    path = os.path.join(EXPERIMENTS_DIR, exp_id, "verdict.md")
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(400)
    except OSError:
        return ""
    m = re.search(r"\b(PASS|REJECT|INCONCLUSIVE)\b", head)
    return m.group(1) if m else ""


def run_backtest(lane: str, key: str, windows: int = 3) -> tuple:
    """Runs the lane's experiment CLI as a subprocess (its own process, so a strategy that leaks
    state or crashes can't take this runner or the 6-hourly cron down with it).
    Returns (experiment_id, verdict, tail_of_output)."""
    script = LANE_RUNNERS[lane][0]
    cmd = [sys.executable, os.path.join(REPO_DIR, script), f"--strategy={key}", f"--windows={windows}"]
    print(f"[{lane}] running {' '.join(cmd[1:])} ...", flush=True)
    proc = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-25:])
    if proc.returncode != 0:
        print(f"[{lane}] {key}: the experiment CLI exited {proc.returncode}\n{tail}", flush=True)
        return "", "", tail
    m = re.search(r"Saved as (EXP-\d+)", out)
    if not m:
        print(f"[{lane}] {key}: the run finished but printed no experiment id\n{tail}", flush=True)
        return "", "", tail
    exp_id = m.group(1)
    return exp_id, read_verdict(exp_id), tail


def run_lane(lane: str, state_dir: str, windows: int, send: bool, token: str, chat_id: str) -> bool:
    """True when a backtest actually ran (so main() can stop after one -- these are long)."""
    key, reason = pending_candidate(state_dir, lane)
    if key is None:
        print(f"[{lane}] nothing to back-test: {reason}", flush=True)
        return False
    exp_id, verdict, tail = run_backtest(lane, key, windows)
    if not exp_id:
        return False   # left in_progress on purpose: a failed run is retried, not silently resolved
    research_queue.resolve(state_dir, key, "researched", experiment_id=exp_id,
                           branch=f"research/{key}" if lane == "india" else f"research-{lane}/{key}",
                           lane=lane)
    msg = (f"*Research complete* ({lane})\n{key}\nVerdict: *{verdict or 'see ' + exp_id}*  ({exp_id})\n\n"
           f"Backtested on the VPS against real data. See the Research tab's Results view.")
    print(msg, flush=True)
    if send:
        from reporting.telegram_notifier import send_telegram_message
        send_telegram_message(msg, token, chat_id)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default=None, choices=research_queue.LANES)
    ap.add_argument("--windows", type=int, default=3)
    ap.add_argument("--send", action="store_true", help="send the verdict to Telegram")
    args = ap.parse_args()

    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    state_dir = os.path.join(REPO_DIR, "deployment", "state")

    # One at a time. A stale lock from a killed run would block this forever, so the lock carries the
    # pid and is ignored once that process is gone.
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH, encoding="utf-8") as f:
                pid = int(f.read().strip() or 0)
            os.kill(pid, 0)
            print(f"another backtest is already running (pid {pid}) -- nothing to do", flush=True)
            return
        except (OSError, ValueError):
            print("ignoring a stale backtest lock (its process is gone)", flush=True)

    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    try:
        for lane in ([args.lane] if args.lane else list(research_queue.LANES)):
            if run_lane(lane, state_dir, args.windows, args.send, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
                break   # these are long -- the next lane waits for the next cycle
    finally:
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
