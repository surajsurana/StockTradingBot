"""
What has happened to the strategies lately, newest first.

WHY IT EXISTS (2026-10-14, per explicit direction: "whenever a strategy test is run and its result
should show in the notifications... if any strategy is pushed to live or paper or retired it should
reflect in notification with date and time stamps"). Every one of these events already happened
somewhere -- a research verdict in an experiment folder, a promotion in the status overlay, a
retirement in the same place -- but each was only visible by opening the thing it happened to. There
was no one page that answered "what has changed recently?", which is the question you ask first.

NOTHING IS RECORDED HERE. This reads what the running system already wrote and lines it up on one
clock; it is a view, not a log. That matters: a second log would be a second thing to keep in step,
and the first time the two disagreed neither would be trustworthy. If an event is missing from this
timeline, the fix is in whatever should have recorded it, not here.

TWO SOURCES, ONE TIMELINE.

  deployment/status_overlay.py -- every status change a strategy has been through, with the reason
  written at the time and a real timestamp. Promoted to paper, promoted to live, retired.

  research_queue.py -- each lane's finished candidates, with the experiment they produced. The
  verdict itself lives in that experiment's verdict.md, which is the same file the Research tab
  reads, so the two can never disagree about what a backtest concluded.

TIME IS REPORTED AS PRECISELY AS IT WAS RECORDED, AND NO MORE. A status change carries a unix
timestamp, so it gets a date and a time. A research result carries only the day it resolved, so it
gets a date and the time is left blank rather than filled with a plausible-looking midnight -- a
timestamp nobody wrote is not information.
"""

import os
from datetime import datetime
from typing import Optional

# to_status -> what to call it and which family of event it belongs to. Anything not here is still
# shown, under its own raw name, rather than being dropped for being unfamiliar.
# "Promoted to ...", not "Started ...": these three are the same kind of event -- a strategy moving
# a stage -- and naming one of them differently made it read as something else going on nearby, so
# the paper promotion was looked for and not found on a page that was showing it.
HIDDEN_QUEUE_OUTCOMES = frozenset({"superseded"})   # queue housekeeping, not shown (2026-10-22)

STATUS_EVENTS = {
    "PAPER_TRADING": ("Promoted to paper trading", "paper"),
    "PILOT_LIVE": ("Promoted to live", "live"),
    "PRODUCTION": ("Promoted to production", "live"),
    "ARCHIVED": ("Retired", "retired"),
    "RESEARCH": ("Moved back to research", "research"),
}


def _when(ts) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _row(at: Optional[datetime], day: str, kind: str, what: str, key: str, meta: dict,
         detail: str = "", experiment: str = "") -> dict:
    """One line of the timeline. `at` when a real timestamp was recorded, `day` when only a date was."""
    return {
        "at": at.isoformat(timespec="seconds") if at else (day or ""),
        "date": at.strftime("%Y-%m-%d") if at else (day or ""),
        "time": at.strftime("%H:%M") if at else "",
        "kind": kind, "what": what, "key": key,
        "sid": meta.get("sid", ""), "name": meta.get("name", key), "pool": meta.get("pool", ""),
        "detail": detail, "experiment": experiment,
    }


def _meta(records) -> dict:
    """{key: {sid, name, pool}} -- what to call each strategy, and where it runs.

    The pool comes from the same helper the Strategies tab uses, so a row cannot say Pool A here and
    Pool E there. A record that does not resolve to one contributes no pool rather than a guess."""
    try:
        from dashboard.state_view import _default_pool_label
    except Exception:                            # noqa: BLE001
        _default_pool_label = None
    out = {}
    for r in records or []:
        pool = getattr(r, "live_pool", "") or ""
        if pool:
            pool = f"Pool {pool}"
        elif _default_pool_label:
            try:
                pool = _default_pool_label(r)
            except Exception:                    # noqa: BLE001
                pool = ""
        out[r.strategy_key] = {"sid": getattr(r, "strategy_id", "") or "",
                               "name": getattr(r, "display_name", r.strategy_key),
                               "pool": pool}
    return out


def _verdict_of(experiment_id: str) -> str:
    """PASS / REJECT / INCONCLUSIVE from the experiment's own verdict.md -- the same file the
    Research tab reads, so the timeline cannot claim a different result from the Results view."""
    if not experiment_id:
        return ""
    from dashboard.state_view import _experiment_verdict
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub in ("swing_research", "research_lab"):
        path = os.path.join(here, sub, "experiments", experiment_id, "verdict.md")
        if os.path.exists(path):
            return _experiment_verdict(path)
    return ""


def timeline(state_dir: str, records=None, limit: int = 120) -> list:
    """[{at, date, time, kind, what, key, sid, name, pool, detail, experiment}], newest first.

    Never raises: a missing or unreadable source contributes nothing rather than blanking the page.
    `records` is the registry (list_strategies()); it is read for names and ids only."""
    if records is None:
        try:
            from deployment.deployment_manager import list_strategies
            records = list_strategies()
        except Exception:                        # noqa: BLE001
            records = []
    meta = _meta(records)
    rows = []

    # 1. Status changes -- promoted, retired, moved back.
    try:
        from deployment.status_overlay import load as load_status
        for key, entry in (load_status(state_dir) or {}).items():
            for h in (entry.get("history") or []):
                to = str(h.get("to_status") or "").split(".")[-1]
                what, kind = STATUS_EVENTS.get(to, (to.replace("_", " ").title(), "status"))
                rows.append(_row(_when(h.get("timestamp")), "", kind, what, key,
                                 meta.get(key, {}), detail=str(h.get("reason") or "")))
    except Exception:                            # noqa: BLE001
        pass

    # 2. Research results -- one per candidate a lane finished with, in any of the three lanes.
    try:
        import research_queue
        for lane in research_queue.LANES:
            q = research_queue.load(state_dir, lane) or {}
            for r in (q.get("history") or []):
                if not r.get("resolved"):
                    continue
                exp = str(r.get("experiment_id") or "")
                verdict = _verdict_of(exp)
                outcome = str(r.get("outcome") or "")
                # "superseded" = the queue swapped a candidate out before any research began. Queue
                # housekeeping, not news -- hidden 2026-10-22 at Suraj's request. A stuck queue is
                # the pipeline-health grid's job now.
                if outcome in HIDDEN_QUEUE_OUTCOMES and not verdict:
                    continue
                # A verdict is the result; an outcome without one means the candidate left the
                # queue without producing a backtest, which is a different kind of news.
                what = (f"Research result: {verdict}" if verdict
                        else f"Research {outcome}" if outcome else "Research finished")
                # The queue's own name where it has one; otherwise the registry's, because a
                # candidate that got as far as a verdict has usually been registered by now and its
                # key is not what anyone calls it. Falling straight back to the key printed
                # "us_short_term_reversal" beside "Short-Term Reversal (US)" on the next line.
                key = str(r.get("key") or "")
                known = meta.get(key, {})
                rows.append(_row(None, str(r.get("resolved")),
                                 "result" if verdict else "research", what, key,
                                 {"sid": known.get("sid", ""),
                                  "name": r.get("name") or known.get("name") or key,
                                  "pool": lane.upper()},
                                 detail=str(r.get("note") or ""), experiment=exp))
    except Exception:                            # noqa: BLE001
        pass

    # Newest first. A date-only row sorts against the start of its day, which puts it before that
    # day's timed events -- the honest order given nobody recorded when in the day it happened.
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows[:limit]
