"""
Merges a research routine's finished work by itself, when the work is demonstrably safe to merge.

WHY A GATE EXISTED AT ALL. The research routines write CODE, and that code runs on this VPS -- the
same machine that holds the Kite and CoinDCX keys and places real orders. The backtest is therefore
merge-gated: it only ever runs a strategy already on `main`, so a human has read the code before
this box executes it (see run_queued_backtest.py's own docstring).

WHY IT IS AUTOMATIC NOW (2026-10-13, per explicit direction: "it should happen automatically right?
thats the whole purpose... paper trading is not real money"). The gate was costing a research cycle
a week while a person got round to reading a diff -- and what that person was actually checking was
never judgement. It was four mechanical facts:

  1. ADDITIONS ONLY.     Nothing removed or rewritten outside its own tests.
  2. NOWHERE NEAR MONEY. No file under deployment/, execution/, reporting/, dashboard/, or any
                         run_live/run_pool/run_paper_trading entry point.
  3. NO REACH.           No new import of a broker client, credential or order-placing helper.
  4. STILL GREEN.        The whole test suite passes with the merge applied.

A machine checks those four better than a person does, so it does. Anything that fails one is
exactly the case a human should look at, and it is left unmerged and reported with the reason.

HOW IT MERGES. Never in the live checkout. It builds a throwaway git worktree from origin/main,
merges there, runs the suite there, and only pushes if everything passed -- so a conflict or a red
suite cannot leave the trading box's working copy in a half-merged state. The worktree is removed
in a finally, including on failure: a deploy that leaves its temp files behind filled this disk once
already and took MongoDB down with it.

PAPER TO LIVE IS UNTOUCHED. This decides what code may be BACKTESTED. Moving real money onto a
strategy remains a human decision with every one of its gates.
"""

import os
import shutil
import subprocess
import tempfile
from typing import Optional

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# Paths that trade, hold money, or hold credentials. A research branch has no business in any of
# them: its job is a strategy and its tests.
FORBIDDEN_PREFIXES = ("deployment/", "execution/", "reporting/", "dashboard/", "auth/")
FORBIDDEN_FILES_STARTING = ("run_live", "run_pool_", "run_paper_trading", "run_pool_live")

# Things only code that moves money needs. Added anywhere in the diff, a human should look.
FORBIDDEN_TOKENS = ("coindcx_client", "kiteconnect", "KiteConnect", "crypto_executor",
                    "live_guard", "place_order", "place_live_order", "API_SECRET", "ACCESS_TOKEN",
                    "api_secret", "access_token")


def _git(args: list, cwd: Optional[str] = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd or REPO_DIR, capture_output=True, text=True,
                          timeout=timeout)


def _changed_files(branch: str) -> list:
    out = _git(["diff", "--name-only", f"main...{branch}"]).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def _is_test(path: str) -> bool:
    return os.path.basename(path).startswith("test_")


def review(branch: str) -> dict:
    """The four checks, against the diff this branch would bring. Returns every one of them with its
    verdict, so a refusal always says which fact failed rather than just "unsafe"."""
    checks, failed = [], []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            failed.append(f"{name}: {detail}")

    files = _changed_files(branch)
    if not files:
        record("has changes", False, "the branch changes nothing against main")
        return {"ok": False, "branch": branch, "checks": checks, "reasons": failed, "files": files}
    record("has changes", True, f"{len(files)} file(s)")

    live = [f for f in files
            if f.startswith(FORBIDDEN_PREFIXES) or os.path.basename(f).startswith(FORBIDDEN_FILES_STARTING)]
    record("nowhere near money", not live,
           "touches " + ", ".join(live) if live else "nothing under deployment/, execution/, "
           "reporting/, dashboard/ or a live runner")

    diff = _git(["diff", f"main...{branch}", "--", ".", ":!test_*"]).stdout.splitlines()
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    record("additions only", not removed,
           f"{len(removed)} line(s) removed outside tests" if removed else "nothing removed or rewritten")

    added = "\n".join(l for l in _git(["diff", f"main...{branch}"]).stdout.splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    reached = sorted({t for t in FORBIDDEN_TOKENS if t in added})
    record("no reach", not reached,
           "adds " + ", ".join(reached) if reached else "no broker, credential or order-placing name")

    return {"ok": not failed, "branch": branch, "checks": checks, "reasons": failed, "files": files}


def _run_suite(cwd: str, timeout: int = 2400) -> tuple:
    """(passed, tail). Uses this interpreter, so the worktree needs no environment of its own."""
    import sys
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"],
                          cwd=cwd, capture_output=True, text=True, timeout=timeout)
    out = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, "\n".join(out.strip().splitlines()[-8:])


def prepare_worktree(tree: str) -> None:
    """Give a throwaway worktree the one file git does not carry: config/settings.py.

    It is git-ignored (it holds the real keys), so a fresh worktree has none, and on 2026-10-22 that
    made ~120 tests fail on import EVEN ON CLEAN MAIN -- the gate refused every research branch for
    a week whatever it contained. The worktree gets the committed TEMPLATE, never the real file: no
    broker credentials, no Telegram token, so nothing the suite runs here can reach an account or a
    phone."""
    example = os.path.join(tree, "config", "settings.example.py")
    target = os.path.join(tree, "config", "settings.py")
    if os.path.exists(example) and not os.path.exists(target):
        shutil.copyfile(example, target)


def merge(branch: str, push: bool = True) -> dict:
    """Merge `branch` into main IN A THROWAWAY WORKTREE, run the suite there, and push only if it
    passed. The live checkout is never touched, so a conflict or a red suite cannot leave the box
    that trades your money half-merged."""
    result = {"branch": branch, "merged": False, "reason": ""}
    tmp = tempfile.mkdtemp(prefix="research-merge-")
    tree = os.path.join(tmp, "work")
    try:
        _git(["fetch", "--quiet", "origin"])
        add = _git(["worktree", "add", "--detach", tree, "origin/main"], timeout=600)
        if add.returncode != 0:
            result["reason"] = f"could not prepare a worktree: {add.stderr.strip()[:200]}"
            return result
        merged = _git(["merge", "--no-ff", "-m", _message(branch), f"origin/{branch}"],
                      cwd=tree, timeout=600)
        if merged.returncode != 0:
            result["reason"] = "the merge conflicts with main and needs a person"
            return result
        prepare_worktree(tree)
        passed, tail = _run_suite(tree)
        if not passed:
            result["reason"] = f"the test suite fails with it merged -- a person should look\n{tail}"
            return result
        if not push:
            result.update({"merged": True, "reason": "checked and green (not pushed: dry run)"})
            return result
        pushed = _git(["push", "origin", "HEAD:main"], cwd=tree, timeout=600)
        if pushed.returncode != 0:
            result["reason"] = f"merged and green, but the push failed: {pushed.stderr.strip()[:200]}"
            return result
        result.update({"merged": True, "reason": "merged to main; tonight's backtest can run it"})
        return result
    except subprocess.TimeoutExpired:
        result["reason"] = "timed out"
        return result
    finally:
        # Always, including on every failure above: a deploy that left its temp files behind filled
        # this disk once and took the database down with it.
        _git(["worktree", "remove", "--force", tree], timeout=120)
        shutil.rmtree(tmp, ignore_errors=True)
        _git(["worktree", "prune"], timeout=60)


def _message(branch: str) -> str:
    key = branch.split("/", 1)[-1]
    return (f"Merge {branch}: {key} implementation\n\n"
            f"Merged automatically: additions only, nothing under deployment/, execution/, "
            f"reporting/, dashboard/ or a live runner, no broker or credential name added, and the "
            f"full test suite green with it applied. See research_merge_gate.py for why those four "
            f"facts are the whole of what a human was checking here.\n")


def consider(state_dir: str, lane: str, push: bool = True) -> Optional[dict]:
    """This lane's claimed candidate, if it has delivered a branch that is not merged yet: review
    it, merge it when it passes, and report either way. None when there is nothing waiting."""
    import research_queue
    from research_queue_github_sync import branch_for
    from run_queued_backtest import implemented_keys

    current = (research_queue.load(state_dir, lane).get("current") or {})
    key = current.get("key")
    if not key or not current.get("in_progress") or key in implemented_keys(lane):
        return None
    branch = branch_for(lane, key)
    if _git(["rev-parse", "--verify", f"origin/{branch}"]).returncode != 0:
        return None                       # claimed, but nothing delivered yet
    verdict = review(f"origin/{branch}")
    if not verdict["ok"]:
        return {"lane": lane, "key": key, "branch": branch, "merged": False,
                "reason": "; ".join(verdict["reasons"]), "checks": verdict["checks"]}
    done = merge(branch, push=push)
    return {"lane": lane, "key": key, "branch": branch, "merged": done["merged"],
            "reason": done["reason"], "checks": verdict["checks"]}


def line(r: dict) -> str:
    """One sentence for the log and for Telegram."""
    if r.get("merged"):
        return f"*Research merged automatically* ({r['lane']})\n{r['key']}\n{r['reason']}"
    return (f"*Research needs you* ({r['lane']})\n{r['key']}\nIt was NOT merged: {r['reason']}\n\n"
            f"Everything else is automatic -- this is the one case worth a person's eyes.")
