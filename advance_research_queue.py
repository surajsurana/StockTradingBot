"""
The research-queue cron: fully deterministic, no LLM call. Scores every candidate strategy across
every research lane (swing_research.research_roadmap.build_roadmap(), generalized 2026-09-22 to
cover swing/intraday/medium/long_term/crypto) and keeps `current` (research_queue.py) pointed at the
best available pick -- picks one if nothing's queued, swaps it for a better-ranked one if the current
pick hasn't actually started yet, and leaves it alone once research is in_progress or once a human
has picked it by hand. Sends a Telegram notification only when the pick actually changes; running
this and finding nothing to do is the normal, quiet case (2026-09-22, per explicit direction:
"interrupting and changing mid week or anytime is ok, only if a research is already ongoing then it
should not interrupt" -- this script is what makes "anytime" real, by running often, not just weekly).

This script only decides WHAT is next -- it never writes strategy code or runs a backtest.
That's the unattended research routine's job (Phase 2, a scheduled Claude Code cloud agent,
not Python in this repo); it picks up whatever this script (or the dashboard's manual
"start research" button, research_queue.start_now()) has set as `current`.

Also runs research_queue_github_sync.py's two jobs every cycle (2026-10-03, replacing the
routine's old direct-HTTP-to-this-VPS design after confirming its cloud sandbox cannot reach
this VPS at all -- see that module's own docstring for the full story): publishes a small
queue snapshot to GitHub (the routine's new read channel, instead of GET /api/state) and polls
GitHub for a `research/<key>` branch/PR (the routine's new write channel -- mark_in_progress()/
resolve() now driven by what THIS script observes on GitHub, instead of the routine POSTing
back over a connection that kept timing out).

    python advance_research_queue.py            # print what it would do (safe default)
    python advance_research_queue.py --send      # also send the Telegram notification

Scheduled cron line, every 6 hours -- cheap and idempotent, so there's no cost to checking often;
a new/better candidate only actually appears when a discovery PR merges or the registry changes,
both infrequent, but this keeps "anytime" honest without needing a write on every dashboard page load:
    0 */6 * * *  cd .../StockTradingBot && venv/bin/python advance_research_queue.py --send
"""

import argparse
import os

from research_queue import advance
from research_queue_github_sync import publish_snapshot, sync_from_github

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def message(entry: dict, name: str, horizon_lane: str, mechanism: str) -> str:
    if entry.get("mode") == "paper_direct":
        how = ("This one can't get a real historical backtest (a genuine data gap), but scores as well as "
               "the strategies that can -- queued to go straight to a paper-trading proposal instead.")
    else:
        how = "This is queued for research."
    return (f"*Research queue*\nNext up: *{name}* ({horizon_lane})\n{mechanism}\n\n{how} "
            "See the Strategies tab's \"Next up for research\" list to start a different one instead.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--state-dir", default=None)
    args = ap.parse_args()
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    state_dir = args.state_dir or STATE_DIR

    from swing_research.research_roadmap import build_roadmap
    roadmap = build_roadmap()
    entry = advance(state_dir, roadmap)
    if entry is None:
        print("Nothing to change: the current pick is already the best available, research is already "
              "under way, it was picked by hand, or nothing eligible remains.")
    else:
        c = next(s.candidate for s in roadmap["all_scored"] if s.candidate.key == entry["key"])
        msg = message(entry, c.name, c.horizon_lane, c.mechanism.split(".")[0] + ".")
        print(msg)
        if args.send:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    github_msg = sync_from_github(state_dir)
    if github_msg:
        print(github_msg)
        if args.send:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(f"*Research queue (from GitHub)*\n{github_msg}", TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    # Re-read the roadmap's view of `current` is unaffected by sync_from_github (it only ever locks/resolves,
    # never changes WHICH candidate is current) -- the same `roadmap` object is still valid for the snapshot.
    if publish_snapshot(REPO_DIR, state_dir, roadmap):
        print("Published updated research_queue_snapshot.json to GitHub.")


if __name__ == "__main__":
    main()
