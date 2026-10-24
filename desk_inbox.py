"""
The desk inbox: questions Suraj sends the Telegram bot in plain words, answered later by a Claude
Code session (2026-10-24, per explicit direction: "i dont want immedieate answer. i just want answer
even if its late. so when i ask question, it saves and replies whenever this session is on").

WHY THIS SHAPE. Answering on the server would bill API credits; Suraj's Claude Pro plan only runs
inside his own Claude Code sessions. So the bot that already listens 24/7 (portfolio_b/telegram_bot)
just SAVES anything that is not one of its watchlist commands, says it got it, and the next time a
StockTradingBot session is open it reads this inbox, works out each answer from the live system, and
sends it back through the same bot. Late answers are fine by design; nothing is lost while the Mac is
off, because the question waits here on the server.

    python desk_inbox.py list                       # unanswered questions, oldest first, as JSON
    python desk_inbox.py answer <id> --file a.txt   # send the answer to Telegram and mark it done
    python desk_inbox.py answer <id> --text "..."
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Optional

from deployment.atomic_write import write_json
from deployment.settings import STATE_DIR

INBOX_FILE = "desk_inbox.json"
TELEGRAM_LIMIT = 4000          # Telegram's hard limit is 4096 characters a message
ACK_TEXT = "📥 Got it — I'll answer when the desk is next open."


def _path(state_dir: Optional[str] = None) -> str:
    return os.path.join(state_dir or STATE_DIR, INBOX_FILE)


def load(state_dir: Optional[str] = None) -> list:
    try:
        with open(_path(state_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add_question(text: str, state_dir: Optional[str] = None, now: Optional[datetime] = None) -> int:
    """Saves one question and returns its id. Ids only ever grow, so an answer can never land on
    the wrong question."""
    items = load(state_dir)
    qid = max((int(i.get("id", 0)) for i in items), default=0) + 1
    items.append({"id": qid, "at": (now or datetime.now()).isoformat(timespec="seconds"),
                  "text": text.strip(), "answered_at": None, "answer": None})
    write_json(_path(state_dir), items)
    return qid


def pending(state_dir: Optional[str] = None) -> list:
    return [i for i in load(state_dir) if not i.get("answered_at")]


def _chunks(text: str, limit: int = TELEGRAM_LIMIT) -> list:
    """Splits on paragraph, then line, boundaries so a long answer arrives as readable pieces."""
    text = text.strip()
    out = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def format_answer(question: dict, answer: str) -> list:
    """The reply quotes the question, because a late answer to an unquoted question is a riddle."""
    asked = str(question.get("text", ""))
    quote = asked if len(asked) <= 120 else asked[:117] + "..."
    return _chunks(f"💬 Re: “{quote}”\n\n{answer.strip()}")


def answer(qid: int, text: str, state_dir: Optional[str] = None, send=None, now: Optional[datetime] = None) -> dict:
    """Sends the answer through the bot and marks the question answered -- only after a send that
    did not fail, so a network error leaves it in the inbox to be answered again."""
    items = load(state_dir)
    q = next((i for i in items if int(i.get("id", 0)) == int(qid)), None)
    if q is None:
        raise ValueError(f"no question {qid} in the inbox")
    if q.get("answered_at"):
        raise ValueError(f"question {qid} was already answered at {q['answered_at']}")
    if send is None:
        from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        from reporting.telegram_notifier import send_telegram_message

        def send(part):
            return send_telegram_message(part, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                         kind="desk_answer", parse_mode=None)
    for part in format_answer(q, text):
        result = send(part) or {}
        if result.get("ok") is not True:      # Telegram's own {"ok": true} is the only success
            raise RuntimeError(f"Telegram did not take the answer: {result}")
    q["answered_at"] = (now or datetime.now()).isoformat(timespec="seconds")
    q["answer"] = text.strip()
    write_json(_path(state_dir), items)
    return q


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("answer")
    a.add_argument("id", type=int)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        print(json.dumps(pending(), indent=2, ensure_ascii=False))
        return 0
    text = args.text if args.text is not None else open(args.file, encoding="utf-8").read()
    q = answer(args.id, text)
    print(f"answered {q['id']} at {q['answered_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
