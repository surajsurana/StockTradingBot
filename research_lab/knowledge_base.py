"""
Research Knowledge Base -- a permanent, append-only record of every
hypothesis ever tested (or seeded in from before this framework existed),
independent of any single experiment's detailed folder
(research_lab/experiments/EXP-NNN/). Its job is narrower and more
important than storage: Quant Researcher reads render_for_prompt()'s
output before proposing new hypotheses, so the system doesn't re-propose
the same failed mechanism in a future session just because that session's
own chat history doesn't remember it. This is what makes the learning
genuinely permanent, not just within one conversation.

.jsonl (one JSON object per line) is the storage format specifically
because it makes "append-only" the natural way to use the file, not just
a convention someone has to remember -- record() only ever opens the file
in append mode, and reading a partially-written line off the end doesn't
corrupt the ones before it the way a single big JSON array file could if
a write was ever interrupted.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

KNOWLEDGE_BASE_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.jsonl")


@dataclass
class KnowledgeBaseEntry:
    exp_id: str
    hypothesis_name: str
    mechanism_summary: str
    verdict: str                  # "PASS", "REJECT", or "SEEDED" for pre-framework history
    key_reason: str
    market_conditions: str = ""
    follow_up_ideas: str = ""


def _load_raw(path: str = KNOWLEDGE_BASE_PATH) -> list:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def record(exp_id: str, hypothesis_name: str, mechanism_summary: str, verdict: str,
           key_reason: str, market_conditions: str = "", follow_up_ideas: str = "",
           path: str = KNOWLEDGE_BASE_PATH) -> None:
    """
    Appends one entry. Raises if exp_id already has an entry -- same
    never-overwrite guarantee as experiment_manager.save_experiment(),
    enforced structurally (append-only file, checked before writing) not
    just by convention.
    """
    existing = _load_raw(path)
    if any(e["exp_id"] == exp_id for e in existing):
        raise ValueError(f"Knowledge base already has an entry for {exp_id} -- "
                          f"never overwrite past research history.")

    entry = {
        "exp_id": exp_id, "hypothesis_name": hypothesis_name,
        "mechanism_summary": mechanism_summary, "verdict": verdict,
        "key_reason": key_reason, "market_conditions": market_conditions,
        "follow_up_ideas": follow_up_ideas,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_entries(path: str = KNOWLEDGE_BASE_PATH) -> list:
    """Returns every entry as a list of KnowledgeBaseEntry, oldest first."""
    return [KnowledgeBaseEntry(**raw) for raw in _load_raw(path)]


def render_for_prompt(path: str = KNOWLEDGE_BASE_PATH) -> str:
    """
    Compact text rendering of the full history, meant to be pasted
    directly into Quant Researcher's prompt. Empty string if nothing has
    been recorded yet (first-ever run).
    """
    entries = load_entries(path)
    if not entries:
        return ""

    lines = ["Prior research history (do not propose a hypothesis whose core "
             "mechanism substantially overlaps with a REJECTed entry below):"]
    for e in entries:
        lines.append(
            f"- [{e.exp_id}] {e.hypothesis_name} -- verdict: {e.verdict}. "
            f"Mechanism: {e.mechanism_summary} Reason: {e.key_reason}"
            + (f" Follow-up ideas noted: {e.follow_up_ideas}" if e.follow_up_ideas else "")
        )
    return "\n".join(lines)


def rejected_mechanisms(path: str = KNOWLEDGE_BASE_PATH) -> list:
    """Mechanism summaries of every REJECTed/SEEDED-negative entry -- used
    by the Research Director's deterministic hard filter (see
    research_director.py) to reject near-duplicate hypotheses before any
    LLM ranking happens."""
    return [e.mechanism_summary for e in load_entries(path) if e.verdict in ("REJECT", "SEEDED")]


def seed_orb_history(path: str = KNOWLEDGE_BASE_PATH) -> None:
    """
    One-time seeding of this session's failed Opening Range Breakout
    rounds, run BEFORE this framework existed (via ad hoc scripts,
    2026-07-23) -- so the very first real experiment run through this
    framework already benefits from them. Safe to call multiple times:
    skips any seed ID that's already present.
    """
    seeds = [
        dict(
            exp_id="SEED-ORB-1", hypothesis_name="Opening Range Breakout (baseline)",
            mechanism_summary="15-min opening range, breakout entry, stop at range low, "
                               "target 1.5x range, mandatory EOD square-off, no filters.",
            verdict="SEEDED",
            key_reason="1,819 trades / 180 days / 29 liquid large caps: 46.7% win rate, "
                       "1.12:1 reward:risk, -1.47% return. 64% of trades never hit stop or "
                       "target at all, just got forced-closed at day's end -- target was set "
                       "too far for typical intraday follow-through.",
            follow_up_ideas="Try a smaller target multiple.",
        ),
        dict(
            exp_id="SEED-ORB-2", hypothesis_name="Opening Range Breakout (tuned target multiple)",
            mechanism_summary="Same as SEED-ORB-1 but target multiple swept across "
                               "0.8/1.0/1.2/1.5.",
            verdict="SEEDED",
            key_reason="Win rate improved monotonically as target shrank (46.7% -> 51.0% at "
                       "0.8x) and the full 6-month result turned barely positive (+0.16%). But "
                       "on just the most recent 60 days specifically, the same 0.8x config lost "
                       "-2.64% -- the apparent edge was concentrated in older data and did not "
                       "hold up out-of-sample/on recent conditions.",
            follow_up_ideas="Any future hypothesis must be judged on recent/out-of-sample "
                            "performance, not full-window in-sample performance alone.",
        ),
        dict(
            exp_id="SEED-ORB-3", hypothesis_name="Opening Range Breakout + daily-bar trend filter",
            mechanism_summary="SEED-ORB-2's config, plus only trading a symbol on days it's in "
                               "a daily-bar uptrend (20MA>50MA as of yesterday's close).",
            verdict="SEEDED",
            key_reason="Win rate barely moved (47.6% -> 47.7%). Roughly halved trade count "
                       "(670->325 over 60 days) and proportionally halved the loss -- fewer "
                       "trades taken, not better ones. Did not fix the underlying edge.",
        ),
        dict(
            exp_id="SEED-ORB-4", hypothesis_name="Opening Range Breakout + fundamentals health filter",
            mechanism_summary="SEED-ORB-3's config, plus a one-time fundamentals health check "
                               "on the universe (reusing fundamentals/fundamental_agent.py).",
            verdict="SEEDED",
            key_reason="Cut the universe 29->20 symbols. Made the recent-60-day result WORSE "
                       "(-0.64% -> -2.42%), not better -- the excluded symbols happened to "
                       "include some of the better performers in this window. Fundamentals "
                       "did not add a genuine intraday edge here.",
        ),
        dict(
            exp_id="SEED-ORB-5", hypothesis_name="Opening Range Breakout + volume confirmation sweep",
            mechanism_summary="SEED-ORB-4's config, plus requiring the breakout candle's volume "
                               "to exceed a multiple (1.0/1.5/2.0x) of the opening range's "
                               "average volume.",
            verdict="SEEDED",
            key_reason="Win rate got WORSE at higher thresholds (45.1% -> 38.9%), along with "
                       "reward:risk (1.00 -> 0.73). Sample sizes also became too thin to trust "
                       "(18-29 trades). Every configuration tried across all 5 rounds remained "
                       "net negative on the most recent, most relevant window -- further "
                       "parameter search on this same design risked curve-fitting to a single "
                       "60-day sample rather than finding a real edge.",
            follow_up_ideas="Abandon range-breakout-of-a-computed-range as the core mechanism; "
                            "try a genuinely different signal type (gap continuation, VWAP "
                            "reversion, relative strength vs Nifty, prior day high/low).",
        ),
    ]
    existing_ids = {e.exp_id for e in load_entries(path)}
    for seed in seeds:
        if seed["exp_id"] not in existing_ids:
            record(path=path, **seed)
