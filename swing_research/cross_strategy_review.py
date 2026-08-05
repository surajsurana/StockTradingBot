"""
Cross-strategy research review -- added 2026-08-04, per explicit
direction: "After completing every three published strategies, produce a
cross-strategy research review that identifies common patterns, recurring
strengths or weaknesses, and any implications for the research framework
itself before moving further down the roadmap."

Reuses the SAME "structured facts in, Claude narrative out" pattern as
research_lab.performance_analyst.explain() -- the objective inputs (each
completed strategy's own recorded conclusion, from
swing_research/research_conclusions.jsonl) are prepared by ordinary code
and are what gets saved/trusted; Claude is asked to synthesize a narrative
ON TOP of them, never to invent or alter the underlying facts.

Does not touch swing_research/acceptance_criteria.py's decision logic at
all -- a cross-strategy review is a separate, retrospective document, not
a verdict and not an input to any individual experiment's verdict.
"""

import json
import os
import time
from typing import Callable, Optional

REVIEW_CADENCE = 3  # produce one review after every N completed strategies

REVIEWS_DIR = os.path.join(os.path.dirname(__file__), "cross_strategy_reviews")


def _load_conclusions(conclusions_path: str) -> list:
    if not os.path.exists(conclusions_path):
        return []
    conclusions = []
    with open(conclusions_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                conclusions.append(json.loads(line))
    return conclusions


def strategies_completed_count(conclusions_path: str) -> int:
    """One research_conclusions.jsonl entry == one completed strategy's
    final, synthesized outcome (see research_director.record_strategy_conclusion())
    -- NOT one experiment (a single strategy typically has several
    experiments: base run, recent-period check, robustness sub-periods,
    all folded into one conclusion at the end)."""
    return len(_load_conclusions(conclusions_path))


def is_review_due(conclusions_path: str, already_reviewed_count: int = 0) -> bool:
    """
    True exactly when the number of completed strategies since the last
    review is a positive multiple of REVIEW_CADENCE. already_reviewed_count:
    how many of the completed strategies have already been covered by a
    prior review (pass 0 for "review due after every 3rd strategy from the
    very start"; pass e.g. 3 after the first review has been produced, so
    the next one is due at 6, not double-counted at 3 again).
    """
    total = strategies_completed_count(conclusions_path)
    pending = total - already_reviewed_count
    return pending > 0 and pending % REVIEW_CADENCE == 0


def build_review_prompt(conclusions: list) -> str:
    sections = []
    for c in conclusions:
        exp_ids = ", ".join(c.get("based_on_exp_ids", []))
        sections.append(f"--- Strategy conclusion (based on {exp_ids}) ---\n{c['conclusion_text']}")
    joined = "\n\n".join(sections)
    return f"""You are reviewing {len(conclusions)} completed strategy research conclusions from an \
NSE Cash equities swing-trading research program. Each conclusion below is the FINAL, already-decided \
outcome for one published, faithfully-implemented strategy (verdict, key metrics, and lessons learned) \
-- your job is NOT to re-judge any individual strategy's verdict, but to synthesize ACROSS them.

{joined}

Write a cross-strategy research review covering exactly these three things, and nothing else:
1. Common patterns across the strategies tested so far (e.g. shared strengths, shared failure modes,
   recurring data/methodology issues).
2. Recurring strengths or weaknesses -- of the strategies themselves, OR of how this research program
   tests them.
3. Implications for the research framework itself (acceptance criteria, evidence-quality scoring,
   walk-forward methodology, benchmark comparison approach) -- concrete, only if the evidence above
   actually supports a specific implication, not speculative.

Do not propose implementing any specific new strategy. Do not second-guess or attempt to change any
individual strategy's already-recorded verdict. Be concise and evidence-grounded -- cite the specific
strategy/finding behind each claim you make."""


def generate_cross_strategy_review(conclusions_path: str, output_dir: str = REVIEWS_DIR,
                                    already_reviewed_count: int = 0,
                                    api_key: str = "", call_fn: Optional[Callable[[str], str]] = None) -> str:
    """
    Generates and saves a cross-strategy review covering every completed-
    strategy conclusion since already_reviewed_count, IF is_review_due()
    (raises if called when not due, to avoid an accidental off-cadence
    review being mistaken for the real one). Returns the path to the saved
    markdown file.
    """
    if not is_review_due(conclusions_path, already_reviewed_count):
        raise ValueError(
            f"Review not due: {strategies_completed_count(conclusions_path)} strategies completed, "
            f"{already_reviewed_count} already reviewed -- next review due at a multiple of "
            f"{REVIEW_CADENCE} pending strategies."
        )

    conclusions = _load_conclusions(conclusions_path)[already_reviewed_count:]
    prompt = build_review_prompt(conclusions)

    if call_fn is not None:
        call = call_fn
    else:
        from news.news_agent import call_claude
        call = lambda p: call_claude(p, api_key, max_tokens=4096)

    review_text = call(prompt)

    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    strategy_names = [c["conclusion_text"].split(":")[0].strip() for c in conclusions]
    filename = f"{timestamp}_review_after_{already_reviewed_count + len(conclusions)}_strategies.md"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Cross-Strategy Research Review\n\n")
        f.write(f"Generated: {timestamp}\n\n")
        f.write(f"Strategies covered by this review: {', '.join(strategy_names)}\n\n")
        f.write("---\n\n")
        f.write(review_text)
    return path
