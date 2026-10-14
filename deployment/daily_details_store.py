"""
A small, shared, cross-portfolio cache of "here's what I would have told
you" text -- added 2026-09-02, per explicit direction ("for so many
strategy im getting so many telegram messages... I only need summary
message and if I ask I can get more details").

Portfolio A's daily run (run_paper_trading.py) previously sent one full
Telegram message PER STRATEGY (up to 6+, every single day, whether or
not anything happened) on top of its own already-existing combined
daily summary. The per-strategy messages are now cached HERE instead of
sent -- the combined summary is still sent as before, and the cached
detail is available on request via portfolio_b/telegram_bot.py's
"Details" button/command, which reads today's entries from here.

One shared JSON file (deployment/state/daily_details.json), keyed by
calendar date then by a human-readable label (a strategy's own
display_name, or "Portfolio B"/"Portfolio C") -- written by three
independent, separately-scheduled cron scripts (run_paper_trading.py,
run_portfolio_c.py, run_portfolio_b.py) that never run concurrently
(staggered cron times), so a plain read-modify-write is safe: no lock
needed.
"""

import json
import os
from datetime import date as date_type

from deployment.settings import STATE_DIR

DAILY_DETAILS_PATH = os.path.join(STATE_DIR, "daily_details.json")


def _load_all() -> dict:
    """The cache, or an empty one. NEVER raises on a damaged file.

    WHAT THIS COST. deployment/state/daily_details.json was left truncated mid-value by an
    interrupted write on 2026-10-07, and json.loads raised on every read after it. save_detail()
    reads before it writes, so every Portfolio B and Portfolio C run from that day on ended in a
    JSONDecodeError -- after the book had already traded and saved itself. The run was fine; the
    note about the run killed the process, nothing could ever rewrite the file to fix it, and the
    tracebacks were read elsewhere as the AI books having stopped.

    This file is a display cache. Losing it costs some text under yesterday's summaries. Taking a
    trading run down to protect it is not a trade worth making, so a file that cannot be parsed is
    moved aside -- kept, in case anyone wants to look -- and the next save starts a clean one."""
    if not os.path.exists(DAILY_DETAILS_PATH):
        return {}
    try:
        with open(DAILY_DETAILS_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return json.loads(content) if content else {}
    except (OSError, ValueError):
        _quarantine()
        return {}


def _quarantine() -> None:
    """Move a damaged cache out of the way so the next save can start clean. Best effort: if even
    this fails, an empty dict is still the right answer."""
    from datetime import datetime
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.replace(DAILY_DETAILS_PATH, f"{DAILY_DETAILS_PATH}.corrupt-{stamp}")
    except OSError:
        pass


def _save_all(data: dict) -> None:
    """Atomic: write a temporary file, then rename it over the real one.

    HOW THE FILE GOT TRUNCATED. The old version opened the real file for writing, which empties it,
    and then serialised into it. Anything that stopped the process in between -- and these runs are
    killed often enough -- left a half-written file that nothing could parse again. The registry has
    been written this way for the same reason; this one was missed."""
    os.makedirs(os.path.dirname(DAILY_DETAILS_PATH), exist_ok=True)
    from deployment.atomic_write import write_json
    write_json(DAILY_DETAILS_PATH, data)


def save_detail(key: str, text: str, as_of_date=None) -> None:
    """key: a display label (e.g. a strategy's display_name, or
    "Portfolio B"). Overwrites any existing entry for the same key on
    the same date -- a strategy that somehow ran twice in one day
    (--force) shows its LATEST result, not a duplicate."""
    as_of_date = as_of_date or date_type.today()
    data = _load_all()
    data.setdefault(as_of_date.isoformat(), {})[key] = text
    _save_all(data)


def load_details(as_of_date=None) -> dict:
    """Returns {key: text} for the given date (today by default), in
    insertion order -- empty dict if nothing has been cached for that
    date yet."""
    as_of_date = as_of_date or date_type.today()
    return _load_all().get(as_of_date.isoformat(), {})
