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

import json
import os
import re
from datetime import datetime
from pathlib import Path
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


_LANE_CURRENCY = {"india": "₹", "us": "$", "crypto": "$"}


def _amount(sym: str, value: float) -> str:
    """₹2,91,708 / $1,501 -- rupees grouped the Indian way, since that is how Suraj reads them."""
    whole = round(abs(value))
    n = f"{whole:,}"
    if sym == "₹" and whole >= 100000:
        head, tail = str(whole)[:-3], str(whole)[-3:]
        n = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1]) + "," + tail
    return f"{sym}{n}"


def _money_line(m: dict, sym: str) -> str:
    """'Made ₹2,91,708 on ₹1,00,000 (May 2017 – Oct 2026) · most recent unseen period: +₹1,53,461'.
    Added 2026-10-29, Suraj: "i should also be able to see how much money was made or lost in
    testing period." Starting money is total_pnl / return_on_capital, which is how both were made."""
    pnl, roc = m.get("total_pnl"), m.get("return_on_capital_pct")
    if pnl is None:
        return ""
    pnl = float(pnl)
    line = f"{'Made' if pnl >= 0 else 'Lost'} {_amount(sym, pnl)}"
    if roc:
        # The percentage is stored to two decimals, so this lands a hair off the real starting money
        # (₹99,999.4 for ₹1,00,000); books start on round hundreds, so round to one.
        raw = pnl * 100.0 / float(roc)
        capital = round(raw, -2) or raw
        line += f" on {_amount(sym, capital)}"
        if pnl < 0 and -pnl > capital:
            line += " (more than the whole book)"
    months = sorted(m.get("monthly_returns_pct") or {})
    if months:
        def label(ym):
            try:
                return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
            except ValueError:
                return ym
        line += f" ({label(months[0])} – {label(months[-1])})"
    oos = (m.get("out_of_sample_metrics") or {}).get("total_pnl")
    if oos is not None:
        line += f" · most recent unseen period: {'+' if float(oos) >= 0 else '−'}{_amount(sym, float(oos))}"
    return line


def _plain_reasons(verdict_text: str) -> list:
    """The auditor's reasons (research_lab/statistical_auditor.py wrote them into verdict.md) in a
    few plain words each. Suraj reads this on his phone; the full text stays in the Research tab."""
    out = []
    m = re.search(r"Only (\d+) total trades.*?minimum of (\d+)", verdict_text)
    if m:
        out.append(f"too few trades ({m.group(1)}; needs {m.group(2)})")
    m = re.search(r"holdout only has (\d+) trade", verdict_text)
    if m:
        out.append(f"too few trades in the most recent period ({m.group(1)})")
    m = re.search(r"Only (\d+)/(\d+) walk-forward windows", verdict_text)
    if m:
        out.append(f"made money in only {m.group(1)} of {m.group(2)} test periods")
    if re.search(r"Out-of-sample expectancy is -|Out-of-sample expectancy is \+0\.00", verdict_text):
        out.append("lost money in the most recent period it had never seen")
    return out


def research_summary(experiment_id: str, lane: str, verdict: str, roots: Optional[list] = None) -> dict:
    """{"stats": "...", "why": "..."} for one research result -- the backtest in one line and the
    verdict's reason in one more (2026-10-26, Suraj: "can i see somethings like backtest data showed?
    example holding period, winning and losing trades, avg profit margin. short and where i understand
    the reasons for reject in brief"). Empty dict when the experiment's files are missing."""
    if not experiment_id:
        return {}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = roots or [os.path.join(here, sub, "experiments") for sub in ("swing_research", "research_lab")]
    folder = next((os.path.join(root, experiment_id) for root in roots
                   if os.path.exists(os.path.join(root, experiment_id, "metrics.json"))), None)
    if folder is None:
        return {}
    try:                       # read-only, like everything in this module (it records nothing)
        m = json.loads(Path(folder, "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    parts = []
    trades, win = m.get("total_trades"), m.get("win_rate")
    if trades:
        if win is not None:
            won = round(float(win) * trades)
            parts.append(f"{trades:,} trades ({won:,} won, {trades - won:,} lost)")
        else:
            parts.append(f"{trades:,} trades")
    if m.get("avg_holding_period_days") is not None:
        parts.append(f"held ~{float(m['avg_holding_period_days']):.0f} days")
    if m.get("expectancy") is not None:
        sym, e = _LANE_CURRENCY.get(lane, ""), float(m["expectancy"])
        parts.append(f"avg {'+' if e >= 0 else '−'}{sym}{abs(e):,.0f} per trade")
    # A backtest that keeps trading a fixed size after its book is gone records -100% a year and a
    # drop past 100%; said as a percentage that reads like a bug, so it is said in words.
    if m.get("cagr") is not None:
        c = float(m["cagr"])
        parts.append("lost the whole book" if c <= -99.5 else f"{'+' if c >= 0 else '−'}{abs(c):.0f}% a year")
    if m.get("max_drawdown_pct") is not None:
        d = abs(float(m["max_drawdown_pct"]))
        parts.append("worst drop: more than the whole book" if d > 100 else f"worst drop −{d:.0f}%")
    try:
        text = Path(folder, "verdict.md").read_text(encoding="utf-8")
    except OSError:
        text = ""
    reasons = _plain_reasons(text)
    if verdict == "PASS":
        why = "Passed: made money across the test periods and in the most recent one it had never seen."
    elif reasons:
        why = "Why rejected: " + ", and ".join(reasons) + "."
    elif verdict:
        body = " ".join(l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#"))
        why = (body[:157] + "...") if len(body) > 160 else body
    else:
        why = ""
    return {"money": _money_line(m, _LANE_CURRENCY.get(lane, "")), "stats": " · ".join(parts), "why": why}


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
                row = _row(None, str(r.get("resolved")),
                           "result" if verdict else "research", what, key,
                           {"sid": known.get("sid", ""),
                            "name": r.get("name") or known.get("name") or key,
                            "pool": lane.upper()},
                           detail=str(r.get("note") or ""), experiment=exp)
                if verdict:
                    row.update(research_summary(exp, lane, verdict))
                rows.append(row)
    except Exception:                            # noqa: BLE001
        pass

    # 3. A live broker refusing our key or login, and the all-clear once it works again.
    try:
        from deployment.broker_auth_watch import timeline_rows
        for e in timeline_rows(state_dir):
            rows.append(_row(e["at"], "", e["kind"], e["what"], "", {"name": e["name"]},
                             detail=e["detail"]))
    except Exception:                            # noqa: BLE001
        pass

    # Newest first. A date-only row sorts against the start of its day, which puts it before that
    # day's timed events -- the honest order given nobody recorded when in the day it happened.
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows[:limit]
