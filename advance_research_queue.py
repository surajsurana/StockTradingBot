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
"start research" button, research_queue.start_now()) has set as `current`, and is the only thing
that locks it (research_queue.mark_in_progress, called over the internet the instant it commits).

    python advance_research_queue.py            # print what it would do (safe default)
    python advance_research_queue.py --send      # also send the Telegram notification

Scheduled cron line, every 6 hours -- cheap and idempotent, so there's no cost to checking often;
a new/better candidate only actually appears when a discovery PR merges or the registry changes,
both infrequent, but this keeps "anytime" honest without needing a write on every dashboard page load:
    0 */6 * * *  cd .../StockTradingBot && venv/bin/python advance_research_queue.py --send
"""

import argparse

from research_queue import advance


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
        return
    c = next(s.candidate for s in roadmap["all_scored"] if s.candidate.key == entry["key"])
    msg = message(entry, c.name, c.horizon_lane, c.mechanism.split(".")[0] + ".")
    print(msg)
    if args.send:
        from reporting.telegram_notifier import send_telegram_message
        send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
