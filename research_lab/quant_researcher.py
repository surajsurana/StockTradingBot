"""
Quant Researcher -- the Claude-powered creative role in the research
pipeline. Reuses news/news_agent.py's call_claude (same infra-reuse
principle already established in this project -- macro/macro_strategist.py
does the same) rather than writing a new Claude-calling function.

Its ENTIRE job is proposing hypotheses. It does not backtest, does not
compute a metric, and cannot mark anything "approved" -- a Hypothesis
coming out of propose_hypotheses() carries no weight until the
deterministic Backtesting Engineer has run it and the Statistical Auditor
has passed it (enforced by research_director.py's pipeline ordering, not
by anything in this file).

Reads the Research Knowledge Base's rendered history before proposing, so
it reasons about what's already failed -- across sessions, not just this
one's chat history -- rather than re-proposing the same mechanism.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

from news.news_agent import call_claude, ClaudeAPIError
from research_lab.knowledge_base import render_conclusions_for_prompt, render_for_prompt

RESEARCH_AREAS = [
    "Opening Range Breakout", "Relative Volume", "VWAP", "Previous Day High/Low",
    "Intraday Momentum", "Gap Continuation", "Relative Strength vs Nifty",
    "Sector Strength", "Trend Continuation", "Pullback Entries",
    "Volatility Expansion", "Market Breadth", "Time-of-Day Effects",
]


@dataclass
class Hypothesis:
    name: str
    mechanism: str
    rationale: str
    rules: str
    distinctiveness: str


def build_hypothesis_prompt(knowledge_base_summary: str, n: int = 8, research_conclusions: str = "") -> str:
    areas_text = "\n".join(f"- {a}" for a in RESEARCH_AREAS)
    history_section = (
        f"\n{knowledge_base_summary}\n"
        if knowledge_base_summary
        else "\n(No prior research history yet -- this is the first batch.)\n"
    )
    conclusions_section = (
        f"\n{research_conclusions}\n\nTreat these conclusions as binding constraints, not just "
        f"background -- if a conclusion says an underlying assumption keeps failing, no hypothesis "
        f"in this batch should rely on that assumption even wrapped in a new-looking mechanism.\n"
        if research_conclusions else ""
    )
    return f"""You are the Quant Researcher for an NSE (Indian stock market) cash-equity \
INTRADAY strategy research lab. Cash equity only -- no futures, no options, no leverage \
assumptions. Your job is ONLY to propose hypotheses for further research; you do not decide \
whether any of them work.
{history_section}{conclusions_section}
Research areas to draw from (combine 2-3 per hypothesis rather than using one in isolation):
{areas_text}

Philosophy: prefer a small number of high-quality, selective setups over something that fires \
every day for every stock -- "no trade today" is an acceptable, even desirable, outcome for a \
good hypothesis. Avoid simple RSI/MACD crossover systems; focus on price action, volume, and \
market structure.

Propose exactly {n} DISTINCT hypotheses. Each must:
1. Have a real theoretical/behavioral rationale (why would this pattern actually predict a move -- \
information flow, order flow, a specific type of market participant's behavior -- not just "it's a \
known pattern")
2. Be clearly different from every entry in the prior research history above (do not propose \
something whose core mechanism substantially overlaps with a REJECTed or SEEDED-negative entry)
3. Not rely on any assumption the research conclusions above have flagged as repeatedly failing
4. Be clearly different from the OTHER hypotheses in this same batch (do not propose five variations \
of the same breakout idea)
5. Be naturally selective (fires on a genuine subset of days/stocks, not constantly)

Respond in EXACTLY this format, one block per hypothesis, nothing else:

### HYPOTHESIS 1
NAME: <short name>
MECHANISM: <concrete, specific entry/exit logic in plain language>
RATIONALE: <why this should actually work -- the behavioral/informational reason>
RULES: <precise entry trigger, stop-loss, target, and any filters>
DISTINCTIVENESS: <how this differs from prior research history AND from the other hypotheses below>

### HYPOTHESIS 2
...

(continue through HYPOTHESIS {n})"""


def parse_hypotheses_response(raw_response: str) -> list:
    """
    Splits on '### HYPOTHESIS' blocks and extracts each field. Skips (with
    a warning, not a crash) any block missing a required field -- a
    partially-malformed batch shouldn't lose the well-formed hypotheses in
    it. Raises only if NONE of the blocks parsed, since the pipeline can't
    proceed with zero candidates.
    """
    blocks = re.split(r"###\s*HYPOTHESIS\s*\d+", raw_response)[1:]  # [0] is preamble, if any
    hypotheses = []
    for i, block in enumerate(blocks, start=1):
        name_match = re.search(r"NAME:\s*(.+)", block)
        mechanism_match = re.search(r"MECHANISM:\s*(.+?)(?=\nRATIONALE:|\Z)", block, re.DOTALL)
        rationale_match = re.search(r"RATIONALE:\s*(.+?)(?=\nRULES:|\Z)", block, re.DOTALL)
        rules_match = re.search(r"RULES:\s*(.+?)(?=\nDISTINCTIVENESS:|\Z)", block, re.DOTALL)
        distinctiveness_match = re.search(r"DISTINCTIVENESS:\s*(.+?)(?=\n###|\Z)", block, re.DOTALL)

        if not (name_match and mechanism_match and rationale_match and rules_match):
            print(f"WARNING: could not parse hypothesis block {i} -- skipping it.")
            continue

        hypotheses.append(Hypothesis(
            name=name_match.group(1).strip(),
            mechanism=mechanism_match.group(1).strip(),
            rationale=rationale_match.group(1).strip(),
            rules=rules_match.group(1).strip(),
            distinctiveness=distinctiveness_match.group(1).strip() if distinctiveness_match else "",
        ))

    if not hypotheses:
        raise RuntimeError(f"Could not parse any hypotheses from the response: {raw_response[:300]}")
    return hypotheses


def propose_hypotheses(api_key: str, n: int = 8, call_fn: Optional[Callable[[str], str]] = None,
                        knowledge_base_path: Optional[str] = None,
                        conclusions_path: Optional[str] = None) -> list:
    """Full pipeline: reads the Knowledge Base (both the raw per-experiment
    history AND the Research Director's latest synthesized cross-experiment
    conclusions, if any review has run), builds the prompt, calls Claude,
    parses the response. Raises ClaudeAPIError on API failure -- unlike the
    swing system's Macro Strategist, there's no safe "default" hypothesis
    to fall back to, so a failure here should stop the research run rather
    than silently proceeding with nothing to test."""
    kb_summary = render_for_prompt(knowledge_base_path) if knowledge_base_path else render_for_prompt()
    conclusions = render_conclusions_for_prompt(conclusions_path) if conclusions_path else render_conclusions_for_prompt()
    prompt = build_hypothesis_prompt(kb_summary, n=n, research_conclusions=conclusions)
    # max_tokens raised well above call_claude's 1024 default -- a batch of
    # n detailed, multi-field hypotheses is a much longer expected output
    # than anything else that calls this function (see call_claude's
    # docstring for the real 2026-07-24 incident this fixes).
    call = call_fn or (lambda p: call_claude(p, api_key, max_tokens=4096))
    raw_response = call(prompt)
    return parse_hypotheses_response(raw_response)
