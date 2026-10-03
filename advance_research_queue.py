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
queue snapshot to GitHub (each lane's routine's new read channel, instead of GET /api/state) and
polls GitHub for a claim branch/PR (each lane's routine's new write channel -- mark_in_progress()/
resolve() now driven by what THIS script observes on GitHub, instead of the routine POSTing
back over a connection that kept timing out).

LANES (added 2026-10-03, per explicit direction: "shall we also build this same auto research for
crypto and us equity?... each section runs 1 or 2 strategies research per week at different times"):
runs all three of research_queue.LANES every cycle, each fully independent (its own queue state file,
its own GitHub snapshot/branch prefix, its own Telegram line if its pick changed) -- so a glut of
India candidates can never starve crypto/US of research cadence the way one pooled queue would. Each
lane's own unattended research routine (a separate claude.ai trigger, its own weekly schedule) is what
actually picks up what this script queues; this script only decides WHAT is next per lane.

    python advance_research_queue.py            # print what it would do (safe default)
    python advance_research_queue.py --send      # also send the Telegram notification

Scheduled cron line, every 6 hours -- cheap and idempotent, so there's no cost to checking often;
a new/better candidate only actually appears when a discovery PR merges or the registry changes,
both infrequent, but this keeps "anytime" honest without needing a write on every dashboard page load:
    0 */6 * * *  cd .../StockTradingBot && venv/bin/python advance_research_queue.py --send
"""

import argparse
import os

from research_queue import LANES, advance
from research_queue_github_sync import LANE_SNAPSHOT_PATH, publish_snapshot, sync_from_github

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def message(entry: dict, name: str, horizon_lane: str, mechanism: str, lane: str) -> str:
    if entry.get("mode") == "paper_direct":
        how = ("This one can't get a real historical backtest (a genuine data gap), but scores as well as "
               "the strategies that can -- queued to go straight to a paper-trading proposal instead.")
    else:
        how = "This is queued for research."
    label = "" if lane == "india" else f" ({lane})"
    return (f"*Research queue{label}*\nNext up: *{name}* ({horizon_lane})\n{mechanism}\n\n{how} "
            "See the Strategies tab's \"Next up for research\" list to start a different one instead.")


def run_lane(lane: str, state_dir: str, send: bool, token: str, chat_id: str) -> None:
    from swing_research.research_roadmap import build_roadmap
    roadmap = build_roadmap(lane=lane)
    entry = advance(state_dir, roadmap, lane=lane)
    if entry is None:
        print(f"[{lane}] Nothing to change: the current pick is already the best available, research "
              "is already under way, it was picked by hand, or nothing eligible remains.")
    else:
        c = next(s.candidate for s in roadmap["all_scored"] if s.candidate.key == entry["key"])
        msg = message(entry, c.name, c.horizon_lane, c.mechanism.split(".")[0] + ".", lane)
        print(msg)
        if send:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(msg, token, chat_id)

    github_msg = sync_from_github(state_dir, lane=lane)
    if github_msg:
        print(github_msg)
        if send:
            from reporting.telegram_notifier import send_telegram_message
            send_telegram_message(f"*Research queue (from GitHub)*\n{github_msg}", token, chat_id)

    # Re-read: the roadmap's view of `current` is unaffected by sync_from_github (it only ever
    # locks/resolves, never changes WHICH candidate is current) -- the same `roadmap` object is
    # still valid for the snapshot.
    if publish_snapshot(REPO_DIR, state_dir, roadmap, lane=lane):
        print(f"[{lane}] Published updated {LANE_SNAPSHOT_PATH[lane]} to GitHub.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--lane", default=None, choices=LANES, help="run only this lane (default: all three)")
    args = ap.parse_args()
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    state_dir = args.state_dir or STATE_DIR

    lanes = [args.lane] if args.lane else list(LANES)
    for lane in lanes:
        run_lane(lane, state_dir, args.send, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
