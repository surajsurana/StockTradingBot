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


def _last_credit_error(text: str) -> Optional[str]:
    """The date stamp nearest above the last credit refusal, or "" when there is one with no date."""
    at = text.rfind(CREDIT_MESSAGE)
    if at < 0:
        return None
    stamps = re.findall(r"20\d\d-\d\d-\d\d", text[:at])
    return stamps[-1] if stamps else ""


def check(state_dir: str, logs_dir: str, now: Optional[datetime] = None) -> dict:
    """{"blocked": bool, "books": [{"name", "last_error", "last_ran"}], "since": "YYYY-MM-DD"}.

    A book counts as blocked when its log's last credit refusal is NEWER than the last time its
    state file was written -- i.e. it has not managed a run since."""
    now = now or datetime.now()
    blocked = []
    for name, log_name, state_file in AI_BOOKS:
        err_date = _last_credit_error(_tail(os.path.join(logs_dir, log_name)))
        if err_date is None:
            continue
        path = os.path.join(state_dir, state_file)
        try:
            ran = datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat()
        except OSError:
            ran = ""
        # No date on the error is still an error: without one, the state file's own age decides.
        if err_date and ran and ran > err_date:
            continue      # it has run since, so whatever that was, it is over
        blocked.append({"name": name, "last_error": err_date, "last_ran": ran})
    return {"blocked": bool(blocked), "books": blocked,
            "since": min((b["last_error"] for b in blocked if b["last_error"]), default=""),
            "message": ("The Anthropic account is out of credit, so the AI books have stopped. "
                        "Pool G holds real positions and cannot manage them until this is topped up.")
            if blocked else ""}
