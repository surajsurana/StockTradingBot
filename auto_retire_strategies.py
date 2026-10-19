"""
Retire the strategies the rules say are finished -- nightly, unattended.

    python auto_retire_strategies.py [--dry-run] [--send]

deployment/retirement.py holds the two rules and why they are those two. Nothing live is ever a
candidate, nothing is sold, and an archived book is frozen exactly as it stands.
"""

import argparse
import re

from deployment.retirement import RULE_A


def _pool_of(key: str) -> str:
    """"Pool A" / "Pool I" / "Pool E" -- the pool whose runner trades this key, or "" if unknown."""
    try:
        from deployment.auto_paper_promotion import paper_runner_for
        m = re.search(r"\((Pool [A-Z0-9]+)", paper_runner_for(key) or "")
        return m.group(1) if m else ""
    except Exception:
        return ""


def retirement_message(r: dict) -> str:
    """One plain line: what was retired, from which pool, and the shortest honest reason."""
    name = r.get("name") or r.get("key") or "?"
    pool = _pool_of(r.get("key") or "")
    who = f"{name} ({pool})" if pool else name
    if r.get("rule") == RULE_A:
        pnl = r.get("pnl") or 0
        sign = "-" if pnl < 0 else ""
        why = (f"{r.get('days')} days, {r.get('trades')} trades, P&L {sign}\u20b9{abs(pnl):,.0f}, "
               f"not qualified for live")
        return f"\U0001F5D1 Retired from paper trading \u2014 {who}: {why}"
    return f"\U0001F5D1 Archived \u2014 {who}: research verdict REJECT, never traded"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    ap.add_argument("--send", action="store_true", help="report to Telegram when something is retired")
    args = ap.parse_args()

    from deployment.retirement import candidates, line, retire
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    found = candidates(STATE_DIR)
    if not found:
        print("Nothing to retire.", flush=True)
        return 0

    done = []
    for c in found:
        if args.dry_run:
            print(f"would retire  {c.get('sid') or c['key']} -- {c['rule']}: {c['reason']}", flush=True)
            continue
        r = retire(c)
        done.append(r)
        print(line(r), flush=True)
        print(f"    {c['reason']}", flush=True)

    retired = [r for r in done if r.get("retired")]
    if args.send and retired:
        from reporting.telegram_notifier import send_telegram_message
        for r in retired:
            # One short line per strategy taken out of PAPER TRADING (2026-10-19, per explicit
            # direction: only research outcomes reach Telegram, and only as a small message). Rule B
            # is housekeeping -- a REJECT that never traded, whose verdict was already announced when
            # its research finished -- so it is logged as muted rather than announced twice.
            send_telegram_message(retirement_message(r), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                  kind="research_outcome" if r.get("rule") == RULE_A else "general",
                                  parse_mode=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
