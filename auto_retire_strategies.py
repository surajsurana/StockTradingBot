"""
Retire the strategies the rules say are finished -- nightly, unattended.

    python auto_retire_strategies.py [--dry-run] [--send]

deployment/retirement.py holds the two rules and why they are those two. Nothing live is ever a
candidate, nothing is sold, and an archived book is frozen exactly as it stands.
"""

import argparse


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
        body = "\n".join(f"- {r.get('sid') or r['key']} {r.get('name') or ''} ({r['rule']})"
                         for r in retired)
        send_telegram_message(
            f"*{len(retired)} strateg{'y' if len(retired) == 1 else 'ies'} retired*\n{body}\n\n"
            f"Archived automatically, nothing sold -- each book is frozen as it stands and still "
            f"readable under Retired on the Strategies tab.",
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
