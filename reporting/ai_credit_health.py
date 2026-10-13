"""
Has an AI book stopped because the Anthropic account ran out of credit?

WHY IT IS DETECTED RATHER THAN PREDICTED. Reading the balance would need the Admin API, which this
account's plan does not include -- established 2026-09-05, when an automatic "$2 left" alert was
looked at and found impossible. So there is no number to watch and no warning before the fact. What
there IS, the instant it matters, is the API's own refusal:

    anthropic.BadRequestError: 400 -- Your credit balance is too low to access the Anthropic API.

That line lands in the log of whichever book was running, and the book stops. This reads the tail of
each AI book's log for it, and treats it as current only if nothing has run successfully since.

WHY IT MATTERS MORE THAN AN ORDINARY ERROR. Pool G is a LIVE book. When it cannot call the model it
does not trade badly -- it does not trade at all, so open positions sit unmanaged: no exits, no stop
checks, nothing. On 2026-10-13 that had been true since the 11th and nothing on the page said so.
"""

import os
import re
from datetime import datetime
from typing import Optional

# The books that cannot run without the model, and the log each one writes.
AI_BOOKS = (("Pool G (AI judgment, crypto)", "pool_g.log", os.path.join("pool_g", "portfolio.json")),
            ("Portfolio B (AI watchlist)", "portfolio_b.log", os.path.join("portfolio_b", "portfolio.json")),
            ("Portfolio C (AI overlay)", "portfolio_c.log", os.path.join("portfolio_c", "portfolio.json")))

CREDIT_MESSAGE = "credit balance is too low"
TAIL_BYTES = 60_000          # plenty for the last few runs, trivial to read on every refresh


def _tail(path: str) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
            return f.read()
    except OSError:
        return ""


def _refused_recently(text: str) -> bool:
    """Is the API's credit refusal in this book's most recent output?"""
    return CREDIT_MESSAGE in text


def _when(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def check(state_dir: str, logs_dir: str, now: Optional[datetime] = None) -> dict:
    """{"blocked": bool, "books": [{"name", "last_error", "last_ran"}], "since": "YYYY-MM-DD HH:MM"}.

    A book counts as blocked when the credit refusal is in its recent output AND its LOG has been
    written more recently than its STATE -- it has failed since it last got anything done.

    Compared by FILE TIME, not by dates read out of the log. The first version parsed the newest
    date stamp above the error, and Pool G does not print one near its traceback, so it matched a
    stamp from two runs earlier and the live book read as healthy while it was down.
    """
    now = now or datetime.now()
    blocked = []
    for name, log_name, state_file in AI_BOOKS:
        log_path = os.path.join(logs_dir, log_name)
        if not _refused_recently(_tail(log_path)):
            continue
        state_path = os.path.join(state_dir, state_file)
        try:
            failed_at = os.path.getmtime(log_path)
        except OSError:
            continue
        try:
            ran_at = os.path.getmtime(state_path)
        except OSError:
            ran_at = 0.0
        if ran_at > failed_at:
            continue      # it has got something done since, so whatever that was, it is over
        blocked.append({"name": name, "last_error": _when(log_path), "last_ran": _when(state_path)})
    return {"blocked": bool(blocked), "books": blocked,
            "since": min((b["last_error"] for b in blocked if b["last_error"]), default=""),
            "message": ("The Anthropic account is out of credit, so the AI books have stopped. "
                        "Pool G holds real positions and cannot manage them until this is topped up.")
            if blocked else ""}
