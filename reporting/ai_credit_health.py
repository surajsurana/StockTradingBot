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
# A run that worked writes its state and its log seconds apart; one that failed leaves the state a
# whole cron interval behind. An hour is far longer than the first and far shorter than the second.
RECOVERED_WITHIN_SECONDS = 3600


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

    CREDIT IS ONE ACCOUNT-WIDE THING, which is the whole point of this check and what the first
    version got wrong. It asked each book separately -- "have YOU run since YOUR error?" -- so when
    Pool G recovered at 20:30 on 2026-10-13 and proved the balance was back, the bar stayed up for
    Portfolio B and C. They were not short of credit. They had not had a turn yet: they run on
    weekday mornings, and the next one was fourteen hours away.

    So one successful model call ANYWHERE clears it. The newest success across the books is
    compared with the newest refusal across the books, and a success that is more recent settles
    the question for all of them.

    A book's own run still has to be told from a failure, and the two are distinguished by a GAP:
    a run that works writes its state file and its log seconds apart, while a failed one leaves the
    state a whole cron interval behind (a success writes the log LAST, so "the log is newer" is
    true even then, and the warning would never come down at all).
    """
    now = now or datetime.now()
    refused, succeeded, books = [], [], []
    for name, log_name, state_file in AI_BOOKS:
        log_path = os.path.join(logs_dir, log_name)
        state_path = os.path.join(state_dir, state_file)
        try:
            ran_at = os.path.getmtime(state_path)
        except OSError:
            ran_at = 0.0
        if not _refused_recently(_tail(log_path)):
            if ran_at:
                succeeded.append(ran_at)
            continue
        try:
            failed_at = os.path.getmtime(log_path)
        except OSError:
            continue
        if failed_at - ran_at <= RECOVERED_WITHIN_SECONDS:
            succeeded.append(ran_at)      # it has worked since, so the account had credit then
            continue
        refused.append(failed_at)
        books.append({"name": name, "last_error": _when(log_path), "last_ran": _when(state_path)})

    # Anything that worked more recently than the last refusal proves the balance is back; the
    # books still listed are simply waiting for their own next run.
    newest_ok = max(succeeded, default=0.0)
    newest_refusal = max(refused, default=0.0)
    blocked = bool(refused) and newest_ok <= newest_refusal
    return {"blocked": blocked, "books": books if blocked else [],
            "since": min((b["last_error"] for b in books if b["last_error"]), default="") if blocked else "",
            "message": ("The Anthropic account is out of credit, so the AI books have stopped. "
                        "Pool G holds real positions and cannot manage them until this is topped up.")
            if blocked else ""}
