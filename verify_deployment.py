"""
Automated deployment verification for a strategy promoted to PAPER_TRADING
(or beyond). Implements deployment/PROMOTION_CHECKLIST.md's items 2-11 as
one runnable, non-destructive check -- it never triggers a paper-trading
run, a git push, or a Telegram send; it only inspects state that should
already exist if the promotion was done correctly.

Written after SW-008 (Short-Term Reversal) was approved and registered
locally on 2026-08-05 but its code never reached the VPS until the next
day -- a full trading day of silently missing signals/Telegram messages
that nothing caught automatically. This script is what should be run
(on whichever machine you're checking -- typically the VPS, since that's
where the checklist actually matters operationally) immediately after any
future PAPER_TRADING promotion, before considering it done.

    python verify_deployment.py --strategy=short_term_reversal

Exits 0 if every check passes, 1 if any fails -- "stop the deployment and
report the failure" per standing governance. Does NOT modify anything:
the frozen deployment/ package, swing_research/, and production code are
only ever read here.
"""

import argparse
import importlib
import json
import os
import subprocess
import sys

from deployment.deployment_manager import get_strategy
from deployment.base import DeploymentStatus, ResearchVerdict
from deployment.settings import REPORTS_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deployment", "state", "paper_trading")


def _check_git_synced_with_remote() -> tuple:
    """True if HEAD matches the tracked upstream branch -- i.e. there is
    nothing local that hasn't been pushed (run on your dev machine) and
    nothing upstream that hasn't been pulled (run on the VPS)."""
    try:
        subprocess.run(["git", "fetch", "--quiet"], check=True, capture_output=True, text=True, timeout=30)
        head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        upstream = subprocess.run(["git", "rev-parse", "@{u}"], check=True, capture_output=True,
                                   text=True).stdout.strip()
        if head == upstream:
            return True, f"HEAD matches upstream ({head[:10]})"
        return False, f"HEAD ({head[:10]}) does not match upstream ({upstream[:10]}) -- push or pull needed"
    except subprocess.CalledProcessError as e:
        return False, f"git check failed: {e.stderr.strip() if e.stderr else e}"
    except Exception as e:
        return False, f"git check failed: {type(e).__name__}: {e}"


def _check_strategy_file_exists(strategy_key: str) -> tuple:
    path = os.path.join("swing_research", "strategies", f"{strategy_key}.py")
    if os.path.isfile(path):
        return True, f"{path} exists"
    return False, f"{path} does not exist"


def _check_registry(strategy_key: str) -> tuple:
    record = get_strategy(strategy_key)
    if record is None:
        return False, "not registered in deployment/state/strategy_registry.json"
    problems = []
    if record.research_verdict != ResearchVerdict.PASS:
        problems.append(f"research_verdict is {record.research_verdict.value}, not PASS")
    if record.deployment_status != DeploymentStatus.PAPER_TRADING:
        problems.append(f"deployment_status is {record.deployment_status.value}, not PAPER_TRADING")
    if problems:
        return False, "; ".join(problems)
    return True, f"strategy_id={record.strategy_id}, verdict=PASS, status=PAPER_TRADING"


def _check_factory(strategy_key: str) -> tuple:
    try:
        rpt = importlib.import_module("run_paper_trading")
    except Exception as e:
        return False, f"could not import run_paper_trading.py: {type(e).__name__}: {e}"
    if strategy_key not in rpt._STRATEGY_FACTORIES:
        return False, "not present in run_paper_trading.py's _STRATEGY_FACTORIES"
    try:
        rpt._STRATEGY_FACTORIES[strategy_key]["strategy_factory"]()
    except Exception as e:
        return False, f"strategy_factory() failed: {type(e).__name__}: {e}"
    return True, "present in _STRATEGY_FACTORIES, strategy_factory() constructs cleanly"


def _check_scheduler(strategy_key: str) -> tuple:
    from deployment.scheduler import is_due_now
    record = get_strategy(strategy_key)
    if record is None:
        return False, "cannot check scheduler -- strategy not registered"
    try:
        due, reason = is_due_now(record)
    except Exception as e:
        return False, f"is_due_now() raised: {type(e).__name__}: {e}"
    return True, f"is_due_now() -> due={due} ({reason})"


def _check_first_run(strategy_key: str) -> tuple:
    portfolio_path = os.path.join(STATE_DIR, strategy_key, "portfolio.json")
    if not os.path.isfile(portfolio_path):
        return False, f"{portfolio_path} does not exist -- no paper-trading run has ever completed"
    with open(portfolio_path) as f:
        portfolio = json.load(f)
    last_date = portfolio.get("last_processed_date")
    if not last_date:
        return False, "portfolio.json exists but has no last_processed_date"
    return True, f"last_processed_date={last_date}"


def _check_telegram_configured() -> tuple:
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        return True, "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are both configured"
    return False, "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are blank -- sends would silently print instead"


def _check_report(strategy_key: str) -> tuple:
    latest_path = os.path.join(REPORTS_DIR, strategy_key, "LATEST.md")
    if not os.path.isfile(latest_path):
        return False, f"{latest_path} does not exist"
    return True, f"{latest_path} exists"


CHECKS = [
    ("Git synced with remote", lambda key: _check_git_synced_with_remote()),
    ("Strategy file exists", _check_strategy_file_exists),
    ("Registry updated", _check_registry),
    ("Factory updated", _check_factory),
    ("Scheduler recognises strategy", _check_scheduler),
    ("First paper-trading run completed", _check_first_run),
    ("Telegram configured", lambda key: _check_telegram_configured()),
    ("Report generated", _check_report),
]


def verify(strategy_key: str) -> bool:
    """Runs every check for strategy_key, prints a checklist-style report,
    and returns True only if all of them passed."""
    print(f"Deployment verification for '{strategy_key}'")
    print("=" * 60)
    all_passed = True
    for label, check_fn in CHECKS:
        try:
            passed, detail = check_fn(strategy_key)
        except Exception as e:
            passed, detail = False, f"check raised {type(e).__name__}: {e}"
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {label} -- {detail}")
        if not passed:
            all_passed = False
    print("=" * 60)
    if all_passed:
        print(f"All checks passed -- '{strategy_key}' deployment verified complete.")
    else:
        print(f"STOP: one or more checks failed for '{strategy_key}'. "
              f"Do not consider this deployment complete until every item passes.")
    return all_passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", type=str, required=True,
                         help="strategy_key to verify, e.g. short_term_reversal")
    args = parser.parse_args()
    passed = verify(args.strategy)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
