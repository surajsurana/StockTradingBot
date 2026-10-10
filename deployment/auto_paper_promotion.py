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
                                           set_primary_experiment_id, set_research_verdict)

# Verdicts worth putting in front of a paper book.
PROMOTING_VERDICTS = ("PASS", "INCONCLUSIVE")


# THE FAMILY IS HOW EACH POOL FINDS ITS OWN. deployment/base.py's is_us_equity_record() and
# is_crypto_record() match on a prefix of strategy_family, and each pool runner GUARDS on them, so
# registering a US strategy under Pool A's family leaves it marked PAPER_TRADING while Pool I skips
# it as "not registered as a US equity strategy" -- traded by nobody, which is the one outcome this
# module exists to prevent.
_POOLS = (("run_paper_trading.py (Pool A)", "run_paper_trading", None,
           "swing_research published strategy"),
          ("run_pool_i.py (Pool I, US)", "run_pool_i", "POOL_I_STRATEGIES", "us_equity"),
          ("run_pool_e.py (Pool E, crypto)", "run_pool_e", "POOL_E_STRATEGIES",
           "crypto research published strategy"))


def _runner_maps() -> dict:
    """{strategy_key: (runner label, the strategy_family that pool recognises)}.

    Imported lazily and defensively: this is called from the nightly backtest job, and one pool's
    import problem must not stop a verdict being recorded for a different pool's strategy."""
    out = {}
    try:
        from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS
        label, family = _POOLS[0][0], _POOLS[0][3]
        out.update({s.strategy_key: (label, family) for s in PAPER_TRADING_STRATEGY_SPECS})
    except Exception:
        pass
    for label, module, attr, family in _POOLS[1:]:
        try:
            out.update({k: (label, family) for k in getattr(__import__(module, fromlist=[attr]), attr)})
        except Exception:
            pass
    return out


def paper_runner_for(strategy_key: str) -> Optional[str]:
    """The runner that would trade `strategy_key` in paper, or None if nothing would."""
    hit = _runner_maps().get(strategy_key)
    return hit[0] if hit else None


def family_for(strategy_key: str) -> Optional[str]:
    """The strategy_family the pool that would run it recognises, or None."""
    hit = _runner_maps().get(strategy_key)
    return hit[1] if hit else None


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
                              strategy_family=strategy_family or family_for(strategy_key)
                              or "swing_research published strategy")
            record = get_strategy(strategy_key)
        enum = _verdict_enum(word)
        if enum is not None and getattr(record, "research_verdict", None) != enum:
            set_research_verdict(strategy_key, enum, source=source)
        # The experiment this verdict came from. Without it the Research tab cannot join the row to
        # its registry record, so the result showed with no id, no pool and no status -- the three
        # things that say what actually happened to it.
        if experiment_id and not getattr(record, "primary_experiment_id", ""):
            set_primary_experiment_id(strategy_key, experiment_id)
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
