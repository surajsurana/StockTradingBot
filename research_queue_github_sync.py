"""
Replaces the unattended research routine's old design of POSTing its progress
back to this VPS over the internet (research_queue.mark_in_progress()/
resolve(), previously called via dashboard/server.py's /api/research/started
and /api/research/resolve endpoints). Two real scheduled runs (2026-09-27 and
2026-09-28) both confirmed the routine's cloud sandbox cannot reach this VPS
at all -- a TCP-level connection timeout on every attempt, on both plain HTTP
and HTTPS, while a plain connectivity check against https://github.com
succeeded instantly both times. Rather than keep trying to make the sandbox
reach this VPS, this flips the direction: this VPS (which has never had
trouble reaching GitHub) now polls GitHub for the routine's progress, since
the routine's only reliably-working write channel is a git push + PR open
(which it already does today, for its own PR).

Two responsibilities, run together every 6 hours by advance_research_queue.py
(piggybacking on its existing cron, cheap and idempotent), once PER LANE
(research_queue.LANES -- added 2026-10-03, one independent routine/queue per
lane so a glut of India candidates can never starve crypto/US of cadence):

1. publish_snapshot(): writes each lane's own snapshot file
   (research_queue.build_snapshot()) and commits+pushes it ONLY if its
   content actually changed -- this is each lane's routine's read channel,
   replacing the old GET /api/state (it reads this file from its own git
   clone, no network call to this VPS needed at all).
2. sync_from_github(): checks whether a branch (LANE_BRANCH_PREFIX[lane] +
   key) exists for the lane's current queued candidate (-> mark_in_progress(),
   replacing the old POST .../research/started) and, once it does, whether a
   PR now exists for that branch (-> resolve(), replacing the old POST
   .../research/resolve, reading the outcome from the PR title and an
   "Experiment-ID: EXP-NNN" line in the PR body each lane's routine prompt
   writes explicitly for this purpose).

Each lane gets its own branch prefix (so e.g. a crypto and an India candidate
that happened to share a key could never collide on one branch) and its own
snapshot file. "india" keeps the exact unprefixed names the original single-
queue design used (research/<key>, swing_research/research_queue_snapshot.json)
-- that's the lane an in-flight research run already knows about; renaming it
would break it.

Both use GitHub's public REST API, unauthenticated (this repo is public,
and polling a few times a day per lane is nowhere near the 60 req/hour
unauthenticated rate limit) -- no token needed, no new credential to manage
for the READ side. Publishing a snapshot needs a PUSH-capable git remote,
which the VPS's deploy key did not have until this fix (it was read-only) --
see the deploy key's GitHub settings; it needs "Allow write access" for
publish_snapshot() to actually push (silently reports failure if it can't,
never raises, so a missing write grant degrades to "the routine can't see
the latest queue state yet", not a crash of this cron job).
"""

import json
import os
import re
import subprocess
from typing import Optional

import requests

import research_queue

GITHUB_REPO = "surajsurana/StockTradingBot"
GITHUB_API = "https://api.github.com"
_TIMEOUT = 15

# lane -> (branch prefix, snapshot path). "india" is unprefixed/unlabeled --
# the original design, kept exactly as-is for backward compatibility.
LANE_BRANCH_PREFIX = {"india": "research/", "crypto": "research-crypto/", "us": "research-us/"}
LANE_SNAPSHOT_PATH = {
    "india": os.path.join("swing_research", "research_queue_snapshot.json"),
    "crypto": os.path.join("swing_research", "research_queue_snapshot_crypto.json"),
    "us": os.path.join("swing_research", "research_queue_snapshot_us.json"),
}


def _branch_exists(branch: str) -> bool:
    try:
        r = requests.get(f"{GITHUB_API}/repos/{GITHUB_REPO}/branches/{branch}", timeout=_TIMEOUT)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _find_pr_for_branch(branch: str) -> Optional[dict]:
    owner = GITHUB_REPO.split("/")[0]
    try:
        r = requests.get(f"{GITHUB_API}/repos/{GITHUB_REPO}/pulls",
                         params={"head": f"{owner}:{branch}", "state": "all"}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        prs = r.json()
        return prs[0] if prs else None
    except (requests.RequestException, ValueError):
        return None


def _outcome_from_pr(pr: dict) -> tuple:
    """Returns (outcome, experiment_id) or (None, None) if the title doesn't
    match either convention the routine's prompt uses (steps 6a/6b)."""
    title = pr.get("title", "")
    if re.search(r"\(proposed for paper trading\)\s*$", title):
        return "paper_trading_proposed", None
    if re.search(r"\((PASS|REJECT|INCONCLUSIVE)\)\s*$", title):
        m = re.search(r"Experiment-ID:\s*(EXP-\d+)", pr.get("body") or "")
        return "researched", (m.group(1) if m else None)
    return None, None


def sync_from_github(state_dir: str, lane: str = "india", now=None) -> Optional[str]:
    """Checks GitHub for progress on `lane`'s current queued candidate and
    advances that lane's queue state (research_queue.py) to match. Returns a
    short human-readable description of what changed, or None if nothing
    did -- the normal, quiet case, same convention as advance_research_queue.py's
    own advance()."""
    prefix = LANE_BRANCH_PREFIX[lane]
    data = research_queue.load(state_dir, lane)
    current = data.get("current")
    if not current:
        return None
    key = current["key"]
    branch = f"{prefix}{key}"
    if not current.get("in_progress"):
        if _branch_exists(branch):
            research_queue.mark_in_progress(state_dir, key, now, lane)
            return f"[{lane}] {key}: research branch found on GitHub ({branch}) -- locked in"
        return None
    pr = _find_pr_for_branch(branch)
    if pr is None:
        return None
    outcome, experiment_id = _outcome_from_pr(pr)
    if outcome is None:
        return None
    research_queue.resolve(state_dir, key, outcome, experiment_id=experiment_id, branch=branch, now=now, lane=lane)
    return f"[{lane}] {key}: PR #{pr.get('number')} found ({outcome}) -- resolved"


def publish_snapshot(repo_dir: str, state_dir: str, roadmap: dict, lane: str = "india") -> bool:
    """Writes `lane`'s snapshot and commits+pushes it ONLY if the content
    actually changed FROM WHAT'S COMMITTED (checked via `git diff --cached`,
    not by comparing against the working tree -- a prior run that wrote the
    file but then failed to commit/push, e.g. because git had no configured
    user identity, left the working tree ahead of HEAD; comparing against
    the working tree would wrongly treat that as "already published" and
    silently never retry it). `roadmap` should already be scoped to `lane`
    (research_roadmap.build_roadmap(lane=lane)), same requirement as
    research_queue.advance()/build_snapshot(). Returns whether it pushed
    anything. Never raises -- a git/push failure (e.g. the deploy key still
    being read-only, or a merge conflict from a concurrent manual deploy) is
    printed and swallowed, since this is a best-effort sync, not something
    that should ever break the cron job that calls it; the next cycle
    retries."""
    snapshot_path = LANE_SNAPSHOT_PATH[lane]
    snapshot = research_queue.build_snapshot(state_dir, roadmap, lane)
    new_content = json.dumps(snapshot, indent=2) + "\n"
    full_path = os.path.join(repo_dir, snapshot_path)
    try:
        # Defensive pull first -- this cron shares the branch with manual deploys
        # (git stash / pull --ff-only / stash pop, same as every other deploy in
        # this project); staying fast-forward-able avoids ever needing to resolve
        # a conflict unattended.
        subprocess.run(["git", "pull", "--ff-only"], cwd=repo_dir, check=True, capture_output=True, text=True)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        subprocess.run(["git", "add", snapshot_path], cwd=repo_dir, check=True, capture_output=True, text=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", snapshot_path], cwd=repo_dir)
        if staged.returncode == 0:
            return False   # identical to what's already committed -- nothing to do
        subprocess.run(["git", "commit", "-m", f"Research queue snapshot ({lane}): automated update"],
                       cwd=repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, check=True, capture_output=True, text=True)
        return True
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"publish_snapshot[{lane}]: could not commit/push ({type(e).__name__}: {e}) -- "
              f"will retry next cycle; check the deploy key has write access", flush=True)
        return False
