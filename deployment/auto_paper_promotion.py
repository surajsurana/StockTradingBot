"""
A strategy that passes research goes straight into paper trading, by itself.

WHY THIS EXISTS (2026-10-10, per explicit direction: "paper trading is not real money. its just
another test after the backtesting. so no harm in auto push to paper trading. from paper to live i
can do manually"). Paper trading is the second test, not a deployment, so a verdict good enough to
be worth watching should not wait on somebody remembering to press a button. Promotion to LIVE is
untouched and stays a human decision, with every one of its gates.

WHAT COUNTS AS GOOD ENOUGH. PASS and INCONCLUSIVE both. An inconclusive result is one the criteria
could not call either way -- forward evidence is exactly what settles it, and that is what a paper
book is for. REJECT is not promoted: the criteria did call it.

THE CAPITAL IS ALREADY RIGHT. A fresh book is created by its own runner at
PAPER_TRADING_WINDDOWN_TARGET_CAPITAL -- Rs1,00,000, or that book's currency equivalent for a
foreign pool -- the first time the strategy runs. Nothing here seeds money.

WHAT THIS REFUSES TO DO. Mark a strategy PAPER_TRADING when no runner can actually trade it. Each
pool's strategy map is CODE -- a factory and its extra columns -- and a research PR adds the
BACKTEST runner, not that. Registering a strategy nothing runs is precisely the SW-008 failure
deployment/PROMOTION_CHECKLIST.md was written after: status said paper trading, no runner had an
entry for it, and it silently did nothing for a day with no error anywhere. So a verdict for a
strategy with no runner is recorded and REPORTED, never quietly promoted.
"""

import os
from typing import Optional

from deployment.base import DeploymentStatus, ResearchVerdict
from deployment.deployment_manager import (get_strategy, register_strategy, set_deployment_status,
                                           set_research_verdict)

# Verdicts worth putting in front of a paper book.
PROMOTING_VERDICTS = ("PASS", "INCONCLUSIVE")


def _runner_maps() -> dict:
    """{strategy_key: the runner that would trade it}, across every paper pool.

    Imported lazily and defensively: this is called from the nightly backtest job, and one pool's
    import problem must not stop a verdict being recorded for a different pool's strategy."""
    out = {}
    try:
        from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS
        out.update({s.strategy_key: "run_paper_trading.py (Pool A)" for s in PAPER_TRADING_STRATEGY_SPECS})
    except Exception:
        pass
    for module, attr, label in (("run_pool_i", "POOL_I_STRATEGIES", "run_pool_i.py (Pool I, US)"),
                                ("run_pool_e", "POOL_E_STRATEGIES", "run_pool_e.py (Pool E, crypto)")):
        try:
            out.update({k: label for k in getattr(__import__(module, fromlist=[attr]), attr)})
        except Exception:
            pass
    return out


def paper_runner_for(strategy_key: str) -> Optional[str]:
    """The runner that would trade `strategy_key` in paper, or None if nothing would."""
    return _runner_maps().get(strategy_key)


def _verdict_enum(word: str):
    return {"PASS": ResearchVerdict.PASS, "REJECT": ResearchVerdict.REJECT,
            "INCONCLUSIVE": ResearchVerdict.INCONCLUSIVE}.get(str(word or "").upper())


def promote(strategy_key: str, verdict: str, display_name: str = "", strategy_family: str = "",
            experiment_id: str = "") -> dict:
    """Record the verdict and, where a runner exists and the verdict is good enough, start paper
    trading. Returns what happened and why, in words meant for a log and a Telegram line.

    Never raises: a promotion problem must not take down the job that produced the verdict."""
    word = str(verdict or "").upper()
    result = {"key": strategy_key, "verdict": word, "promoted": False, "reason": ""}
    try:
        if word not in PROMOTING_VERDICTS:
            result["reason"] = f"{word or 'no verdict'} -- only {' and '.join(PROMOTING_VERDICTS)} are promoted"
            return result
        runner = paper_runner_for(strategy_key)
        if not runner:
            result["reason"] = ("no paper-trading runner has an entry for it yet, so marking it "
                                "PAPER_TRADING would leave a strategy nothing actually trades")
            return result

        record = get_strategy(strategy_key)
        source = f"auto-promoted on {experiment_id}" if experiment_id else "auto-promoted"
        if record is None:
            register_strategy(strategy_key=strategy_key,
                              display_name=display_name or strategy_key,
                              strategy_family=strategy_family or "swing_research published strategy")
            record = get_strategy(strategy_key)
        enum = _verdict_enum(word)
        if enum is not None and getattr(record, "research_verdict", None) != enum:
            set_research_verdict(strategy_key, enum, source=source)
        record = get_strategy(strategy_key)
        if record is not None and record.deployment_status == DeploymentStatus.PAPER_TRADING:
            result.update({"promoted": True, "runner": runner,
                           "reason": f"already paper trading; {runner} runs it"})
            return result
        set_deployment_status(strategy_key, DeploymentStatus.PAPER_TRADING,
                              reason=f"Research returned {word} ({experiment_id or 'no experiment id'}). "
                                     f"Paper trading is the next test, not a deployment, so this is "
                                     f"automatic; promotion to live stays a human decision.")
        result.update({"promoted": True, "runner": runner,
                       "reason": f"{runner} will seed its book and start trading it on the next run"})
    except Exception as e:                      # noqa: BLE001 -- a verdict is worth more than this
        result["reason"] = f"could not promote ({type(e).__name__}: {e})"
    return result


def line(result: dict) -> str:
    """One sentence for the log and the Telegram message."""
    key = result.get("key", "?")
    if result.get("promoted"):
        return f"{key}: {result['verdict']} -- now PAPER TRADING. {result.get('reason', '')}".strip()
    return f"{key}: {result.get('verdict') or 'no verdict'} -- not promoted: {result.get('reason', '')}".strip()
