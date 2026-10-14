"""
Taking a strategy out of paper trading when the forward evidence has had long enough to speak.

WHY THIS EXISTS (2026-10-14, per explicit direction). Auto-promotion adds a strategy to paper every
time research returns a PASS or an INCONCLUSIVE, and nothing ever removed one. A book that has lost
money for four months otherwise keeps a slot, a notional Rs1,00,000 and a row on the page for ever,
and the list stops being a list of things worth watching.

TWO RULES, both decided by the user:

  A. STILL PAPER TRADING, 120 days old, the live gate says NOT QUALIFIED, and it has not made money.
     120 days is deliberately double the gate's own 60-day floor: this is a verdict on a strategy,
     not a reading of a bad fortnight.

  B. NOT TRADING AT ALL and the research verdict is REJECT. Housekeeping -- these were decided
     against and never deployed, so there is nothing to stop, only a list to tidy.

WHAT "NOT QUALIFIED" MEANS, AND WHY IT IS SAFE TO LEAN ON. Until Gate E was wired into the gate it
only tested the research verdict, the status, and the day and trade floors -- so at 120 days it
could only ever mean "the verdict was REJECT", and a losing PASS strategy was immune. The gate now
also requires positive P&L, and "waiting" now means the evidence is still arriving rather than
"nothing is wrong". Without both of those changes this rule would retire the wrong strategies, or
none.

WHAT IT WILL NOT DO. Touch anything live -- PILOT_LIVE and PRODUCTION are never candidates, whatever
their numbers say; real money is moved by a person. It also places no orders and sells nothing: an
archived book is frozen exactly as it stands, open positions included, and its record stays readable
on the Strategies tab under Retired.
"""

import os
from typing import Optional

from deployment.base import DeploymentStatus, ResearchVerdict

# Double the live gate's own 60-day floor: a verdict on a strategy, not a reading of a bad fortnight.
MIN_PAPER_DAYS_BEFORE_RETIRING = 120

RULE_A = "paper, 120 days, not qualified, no profit"
RULE_B = "not trading and the research verdict is REJECT"


def _status(record) -> str:
    return str(getattr(record.deployment_status, "value", record.deployment_status)).split(".")[-1]


def _verdict(record) -> str:
    return str(getattr(record.research_verdict, "value", record.research_verdict)).split(".")[-1]


def candidates(state_dir: str, records: Optional[list] = None, now=None) -> list:
    """[{key, sid, name, rule, reason, days, trades, pnl}] -- everything the rules would retire.

    Reads each strategy's own books off disk through the same gate the promote endpoint uses, so
    retirement and promotion can never disagree about the same strategy on the same day."""
    from dashboard.state_view import pilot_gate_for_key
    from deployment.deployment_manager import list_strategies

    out = []
    for record in (records if records is not None else list_strategies()):
        status = _status(record)
        # Live is never touched here. Real money moves when a person says so.
        if status in ("PILOT_LIVE", "PRODUCTION", "ARCHIVED"):
            continue
        verdict = _verdict(record)

        if status != "PAPER_TRADING":
            if verdict == "REJECT":
                out.append({"key": record.strategy_key, "sid": record.strategy_id or "",
                            "name": getattr(record, "display_name", record.strategy_key),
                            "rule": RULE_B, "days": None, "trades": None, "pnl": None,
                            "reason": (f"Not trading ({status}) and the research verdict is REJECT. "
                                       f"Archived as housekeeping; nothing was running to stop.")})
            continue

        gate = pilot_gate_for_key(record, state_dir, now=now) or {}
        days, trades = gate.get("days") or 0, gate.get("trades") or 0
        pnl = _gross_pnl(state_dir, record.strategy_key)
        if days < MIN_PAPER_DAYS_BEFORE_RETIRING:
            continue
        if gate.get("state") != "not_qualified":
            continue
        if pnl > 0:
            continue
        out.append({"key": record.strategy_key, "sid": record.strategy_id or "",
                    "name": getattr(record, "display_name", record.strategy_key),
                    "rule": RULE_A, "days": days, "trades": trades, "pnl": pnl,
                    "reason": (f"{days} days of paper trading over {trades} closed trades, P&L "
                               f"{pnl:,.0f} before charges, and the live gate says not qualified: "
                               f"{' '.join(gate.get('reasons') or [])}".strip())})
    return out


def _gross_pnl(state_dir: str, key: str) -> float:
    """What every book of this strategy has made, before charges -- the same figure the gate's own
    server-side recheck uses, so the two always agree."""
    import glob

    from dashboard.state_view import _read_jsonl
    total = 0.0
    for book in glob.glob(os.path.join(state_dir, "*", key)):
        total += sum(float(t.get("pnl") or 0) for t in _read_jsonl(os.path.join(book, "trades.jsonl")))
    return round(total, 2)


def retire(candidate: dict) -> dict:
    """Archive one candidate. Never raises: one strategy's problem must not stop the rest."""
    from deployment.deployment_manager import set_deployment_status
    result = {**candidate, "retired": False, "error": ""}
    try:
        set_deployment_status(candidate["key"], DeploymentStatus.ARCHIVED,
                              reason=f"Retired automatically -- {candidate['rule']}. {candidate['reason']}")
        result["retired"] = True
    except Exception as e:                      # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def line(r: dict) -> str:
    """One line for the log and for Telegram."""
    who = f"{r.get('sid') or r['key']} {r.get('name') or ''}".strip()
    if r.get("retired"):
        return f"RETIRED  {who} -- {r['rule']}"
    return f"not retired  {who} -- {r.get('error') or 'no reason recorded'}"
