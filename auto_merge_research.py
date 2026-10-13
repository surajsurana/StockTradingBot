"""
Merge whatever the research routines finished, if it passes the gate -- run nightly, just before the
backtest that needs it.

    python auto_merge_research.py [--lane india|crypto|us] [--send] [--dry-run]

Pairs with run_queued_backtest.py on the same cron line: merge first, then back-test. A strategy
implemented on Sunday is therefore backtested the same night instead of waiting for somebody to read
a diff -- which is what it used to wait for, a cycle at a time. research_merge_gate.py's own
docstring says what is checked and why that is the whole of it.
"""

import argparse

import research_merge_gate
import research_queue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default=None, choices=research_queue.LANES)
    ap.add_argument("--send", action="store_true", help="report to Telegram")
    ap.add_argument("--dry-run", action="store_true", help="check and report, never push")
    args = ap.parse_args()

    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    held = 0
    for lane in ([args.lane] if args.lane else list(research_queue.LANES)):
        try:
            r = research_merge_gate.consider(STATE_DIR, lane, push=not args.dry_run)
        except Exception as e:                       # noqa: BLE001 -- one lane must not stop the rest
            print(f"[{lane}] merge gate failed: {type(e).__name__}: {e}", flush=True)
            continue
        if r is None:
            print(f"[{lane}] nothing waiting to merge.", flush=True)
            continue
        msg = research_merge_gate.line(r)
        print(msg, flush=True)
        for c in r.get("checks", []):
            print(f"    [{'ok ' if c['ok'] else 'NO '}] {c['check']}: {c['detail']}", flush=True)
        if not r["merged"]:
            held += 1
        # Only a refusal is worth interrupting someone for: a merge that went through is the
        # ordinary case and shows up as the verdict a few hours later.
        if args.send and not r["merged"]:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    return 1 if held else 0


if __name__ == "__main__":
    raise SystemExit(main())
