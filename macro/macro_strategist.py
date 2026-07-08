"""
Macro Strategist -- the one agent that looks beyond individual stocks.

Every other judgment agent in this system (News Agent, Technical Agent,
Fundamental Agent, Research Analyst) reasons about ONE company at a time.
This one reads general market/world headlines -- geopolitics, wars or
ceasefires, natural disasters, central bank moves, oil/commodity shocks --
and produces a single daily read on whether today's broader conditions
warrant trading smaller, or not opening new positions at all.

Runs once per day (in run_daily.py, before the Nifty 500 scan), not
per-stock -- cost is fixed and small regardless of how many candidates
Stage 1 would otherwise find. This is deliberately separate from Chief
Investment AI (cio/chief_investment_ai.py): CIO answers "how much should we
generally risk this month" on a slow, reviewed, monthly cadence; Macro
Strategist answers "does today specifically call for trading smaller than
that" and never persists its read anywhere -- it's a same-day-only
adjustment layered on top of whatever CIO already decided.

Reuses the same general RSS feeds news/rss_sources.py already fetches for
per-stock news (Moneycontrol, Economic Times, Zerodha Pulse) -- just
without the per-symbol keyword filter, since a geopolitical or macro story
usually won't mention any single company by name. No new, unverified feed
sources added.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from news.news_agent import call_claude
from news.rss_sources import (
    fetch_moneycontrol_articles, fetch_economic_times_articles, fetch_zerodha_pulse_articles,
)

RISK_LEVELS = {"normal", "elevated", "high"}


@dataclass
class MacroAssessment:
    risk_level: str          # "normal", "elevated", or "high"
    reasoning: str
    headlines_considered: list = field(default_factory=list)


def fetch_general_headlines(max_items: int = 20) -> list:
    """
    Unfiltered general market/world headlines -- the same RSS sources used
    for per-stock news, but without the per-symbol keyword filter, since a
    macro or geopolitical story won't usually mention any single company.
    Deduplicates by title (a story often gets picked up by more than one
    source), same approach as news_agent.fetch_recent_news.
    """
    articles = (
        fetch_moneycontrol_articles()
        + fetch_economic_times_articles()
        + fetch_zerodha_pulse_articles()
    )

    seen_titles = set()
    deduped = []
    for article in articles:
        key = article["title"].strip().lower()
        if key and key not in seen_titles:
            seen_titles.add(key)
            deduped.append({"title": article["title"], "publisher": article["publisher"]})

    return deduped[:max_items]


def build_macro_prompt(articles: list) -> str:
    headlines_text = "\n".join(f"- {a['title']} ({a['publisher']})" for a in articles)

    return f"""You are the Macro Strategist for an Indian (NSE) swing-trading system. You do not look at any single stock -- your job is to judge whether today's broader news (geopolitics, wars or ceasefires, natural disasters, central bank decisions, oil/commodity shocks, global market moves) materially raises the risk of opening NEW Indian equity positions right now.

Here are today's general market/business headlines:
{headlines_text}

Assess the risk level for opening new equity positions today:
- normal: nothing unusual, ordinary market conditions
- elevated: a specific development (e.g. an escalating conflict, a surprise policy move, a natural disaster with real economic impact) that warrants caution, but not a full stop
- high: a serious, acute shock (e.g. a major war escalation, a market-wide crisis) where new positions should probably wait

Be conservative about calling "elevated" or "high" -- routine headlines, ordinary volatility, and stories unrelated to markets should be "normal". Only flag something a professional macro desk would actually act on.

Respond in EXACTLY this format, nothing else:
RISK_LEVEL: <normal|elevated|high>
REASONING: <one or two sentences>"""


def parse_macro_response(raw_response: str, articles: list) -> MacroAssessment:
    """
    Parses the LLM's structured response. Falls back to "normal" (never
    "high") if the response doesn't match the expected format -- an
    unattended system that fails to understand its own risk model should
    not silently stop trading over a parsing bug. The existing
    stop-loss/GTT/position-sizing layers are the real safety net; this
    agent is an extra layer of judgment on top, not the last line of
    defense, so erring toward availability here is deliberate.
    """
    level_match = re.search(r"RISK_LEVEL:\s*(normal|elevated|high)", raw_response, re.IGNORECASE)
    reasoning_match = re.search(r"REASONING:\s*(.+)", raw_response, re.IGNORECASE | re.DOTALL)

    if not level_match:
        return MacroAssessment(
            risk_level="normal",
            reasoning=(f"Could not parse a clear risk level from the model's response -- "
                       f"defaulting to normal rather than blocking trades on a parsing failure: "
                       f"{raw_response[:200]}"),
            headlines_considered=articles,
        )

    reasoning = reasoning_match.group(1).strip() if reasoning_match else "(no reasoning provided)"

    return MacroAssessment(
        risk_level=level_match.group(1).lower(), reasoning=reasoning, headlines_considered=articles,
    )


def assess_macro_conditions(api_key: str, max_items: int = 20,
                             call_fn: Optional[Callable[[str], str]] = None) -> MacroAssessment:
    """Full pipeline: fetch general headlines, ask Claude for a risk read, parse."""
    articles = fetch_general_headlines(max_items=max_items)

    if not articles:
        return MacroAssessment(
            risk_level="normal", reasoning="No general headlines available today -- defaulting to normal.",
            headlines_considered=[],
        )

    prompt = build_macro_prompt(articles)
    call = call_fn or (lambda p: call_claude(p, api_key))
    raw_response = call(prompt)

    return parse_macro_response(raw_response, articles)
