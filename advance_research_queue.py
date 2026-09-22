"""
The weekly research-queue cron: fully deterministic, no LLM call. If nothing is currently
"in research" (research_queue.py's `current`), scores every candidate strategy across every
research lane (swing_research.research_roadmap.build_roadmap(), now generalized to cover
swing/intraday/medium/long_term/crypto -- 2026-09-22, per explicit direction: "a single feeder
agent irrespective of the type of trade"), advances the top-ranked one never attempted before,
and sends a Telegram notification. Does nothing if something is already current (one strategy
researched at a time) or nothing eligible remains.

This script only decides WHAT is next -- it never writes strategy code or runs a backtest.
That's the unattended research routine's job (Phase 2, a scheduled Claude Code cloud agent,
not Python in this repo); it picks up whatever this script (or the dashboard's manual
"start research" button, research_queue.start_now()) has set as `current`.

    python advance_research_queue.py            # print what it would do (safe default)
    python advance_research_queue.py --send      # also send the Telegram notification

Scheduled cron line, Monday morning IST so a new candidate is ready for the routine's
own weekly run shortly after:
    0 9 * * 1  cd .../StockTradingBot && venv/bin/python advance_research_queue.py --send
"""

import argparse

from research_queue import advance


def message(entry: dict, name: str, horizon_lane: str, mechanism: str) -> str:
    return (f"*Research queue*\nNext up: *{name}* ({horizon_lane})\n{mechanism}\n\n"
            "This is queued for research. See the Strategies tab's \"Next up for research\" list to start a different one instead.")


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
        print("Nothing to advance: either something is already in research, or every candidate has been attempted.")
        return
    c = next(s.candidate for s in roadmap["all_scored"] if s.candidate.key == entry["key"])
    msg = message(entry, c.name, c.horizon_lane, c.mechanism.split(".")[0] + ".")
    print(msg)
    if args.send:
        from reporting.telegram_notifier import send_telegram_message
        send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
