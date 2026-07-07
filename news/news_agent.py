"""
News Agent -- the first genuinely "AI judgment" agent in this system.

Everything built before this (strategies, risk manager, fundamentals filter)
is rule-based: fixed logic that compares numbers against thresholds. This
agent is different -- it reads real news headlines about a company and asks
an LLM (Claude) to form a judgment on whether the news is good or bad for
the stock right now, the same way a human research analyst would skim
headlines and form an opinion. That's a fundamentally different kind of task
than "is this number above or below that number."

News sources combined (see fetch_recent_news):
1. yfinance's built-in news feed (free, no separate subscription)
2. Moneycontrol RSS feeds, filtered to the relevant company (news/rss_sources.py)
3. Economic Times RSS feeds, filtered to the relevant company (news/rss_sources.py)

Then analyze_news() sends the combined headlines to Claude and asks for a
structured verdict: bullish / bearish / neutral, a confidence level, and a
plain-language reason.

Requires your own Anthropic API key (from console.anthropic.com) to actually
run for real -- this is separate from and unrelated to whichever Claude
interface you're using to build this project. Each call costs a small,
pay-per-use amount; nothing runs unless you explicitly provide a key.

This agent does NOT decide trades by itself. It produces one opinion that
will feed into the Research Analyst (a later piece) alongside the Technical
and Fundamental agents' opinions.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import yfinance as yf

from news.rss_sources import fetch_rss_news_for_symbol

NEWS_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news_cache.json")


@dataclass
class NewsAssessment:
    symbol: str
    sentiment: str          # "bullish", "bearish", or "neutral"
    confidence: float        # 0.0-1.0
    reasoning: str
    headlines_considered: list = field(default_factory=list)


def fetch_yfinance_news(symbol: str, max_items: int = 8) -> list:
    """
    Pulls recent news headlines for a symbol via yfinance. Returns a list of
    dicts with 'title' and 'publisher'. Returns an empty list if nothing is
    available -- callers should treat that as "no news signal", not an error.
    """
    ticker = yf.Ticker(symbol)
    raw_news = ticker.news or []

    articles = []
    for item in raw_news[:max_items]:
        # yfinance's news items nest the actual article content under 'content'
        # in some versions and are flat in others -- handle both defensively.
        content = item.get("content", item)
        title = content.get("title")
        provider = content.get("provider")
        publisher = provider.get("displayName") if isinstance(provider, dict) else content.get("publisher")
        if title:
            articles.append({"title": title, "publisher": publisher or "unknown source"})

    return articles


def fetch_recent_news(symbol: str, max_items: int = 8, use_rss_sources: bool = True) -> list:
    """
    Combines yfinance's news feed with Moneycontrol and Economic Times RSS
    articles that mention this symbol's company (see news/rss_sources.py).
    Deduplicates by title so the same story reported by multiple sources
    only counts once.

    Sources are interleaved (one from each, round-robin) rather than
    concatenated-then-truncated -- otherwise a source that alone already
    fills max_items (commonly yfinance) silently squeezes out every other
    source before the final cap is even applied. Returns [] if nothing was
    found anywhere.
    """
    yfinance_articles = fetch_yfinance_news(symbol, max_items=max_items)
    rss_articles = fetch_rss_news_for_symbol(symbol, max_items=max_items) if use_rss_sources else []

    seen_titles = set()
    combined = []

    max_len = max(len(yfinance_articles), len(rss_articles))
    for i in range(max_len):
        for source_list in (yfinance_articles, rss_articles):
            if i < len(source_list):
                article = source_list[i]
                key = article["title"].strip().lower()
                if key not in seen_titles:
                    seen_titles.add(key)
                    combined.append(article)
            if len(combined) >= max_items:
                break
        if len(combined) >= max_items:
            break

    return combined[:max_items]


def build_news_prompt(symbol: str, articles: list) -> str:
    headlines_text = "\n".join(f"- {a['title']} ({a['publisher']})" for a in articles)

    return f"""You are a financial research analyst reviewing recent news headlines about {symbol}, an Indian stock.

Here are the recent headlines:
{headlines_text}

Based only on these headlines, assess whether the recent news is bullish (positive for the stock), bearish (negative), or neutral (no clear directional signal). Be conservative -- if headlines are routine, ambiguous, or unrelated to the company's actual business/financial prospects, call it neutral rather than forcing a bullish or bearish read.

Respond in EXACTLY this format, nothing else:
SENTIMENT: <bullish|bearish|neutral>
CONFIDENCE: <a number between 0.0 and 1.0>
REASONING: <one or two sentences explaining your assessment>"""


def parse_news_response(symbol: str, raw_response: str, articles: list) -> NewsAssessment:
    """
    Parses the LLM's structured text response. Falls back to a cautious
    "neutral, low confidence" result if the response doesn't match the
    expected format -- we'd rather under-react to a parsing failure than
    accidentally act on a misread signal.
    """
    sentiment_match = re.search(r"SENTIMENT:\s*(bullish|bearish|neutral)", raw_response, re.IGNORECASE)
    confidence_match = re.search(r"CONFIDENCE:\s*([\d.]+)", raw_response)
    reasoning_match = re.search(r"REASONING:\s*(.+)", raw_response, re.IGNORECASE | re.DOTALL)

    if not sentiment_match:
        return NewsAssessment(
            symbol=symbol, sentiment="neutral", confidence=0.0,
            reasoning=f"Could not parse a clear sentiment from the model's response: {raw_response[:200]}",
            headlines_considered=articles,
        )

    sentiment = sentiment_match.group(1).lower()
    confidence = float(confidence_match.group(1)) if confidence_match else 0.5
    confidence = max(0.0, min(1.0, confidence))  # clamp to valid range
    reasoning = reasoning_match.group(1).strip() if reasoning_match else "(no reasoning provided)"

    return NewsAssessment(
        symbol=symbol, sentiment=sentiment, confidence=confidence,
        reasoning=reasoning, headlines_considered=articles,
    )


def call_claude(prompt: str, api_key: str, model: str = "claude-sonnet-5") -> str:
    """
    The actual call to Claude's API. Requires the `anthropic` package
    (pip install anthropic) and your own API key. Kept as its own small
    function so tests/validation can swap in a fake version instead of
    hitting the real API.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    # Claude's response can include non-text blocks before the actual answer
    # (e.g. a "thinking" block when extended reasoning kicks in) -- content[0]
    # isn't reliably the text block, so find every text block and join them,
    # rather than assuming position 0. Raises clearly if no text block exists
    # at all, instead of silently returning something wrong.
    text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    if not text_blocks:
        raise RuntimeError(
            f"Claude's response contained no text block to parse (got block types: "
            f"{[getattr(b, 'type', type(b).__name__) for b in response.content]})."
        )
    return "\n".join(text_blocks)


def disabled_news_assessment(symbol: str) -> NewsAssessment:
    """
    Stand-in for analyze_news() when config.settings.USE_NEWS_AGENT is False --
    lets callers skip the News Agent's Claude call entirely (cost control)
    while still feeding Research Analyst a well-formed, neutral input, the
    same way a "no headlines found" result already does.
    """
    return NewsAssessment(
        symbol=symbol, sentiment="neutral", confidence=0.0,
        reasoning="News Agent disabled (USE_NEWS_AGENT=False in config/settings.py).",
        headlines_considered=[],
    )


def analyze_news(symbol: str, api_key: str, max_items: int = 8,
                  call_fn: Optional[Callable[[str], str]] = None) -> NewsAssessment:
    """
    Full pipeline: fetch headlines (yfinance + Moneycontrol + Economic
    Times), ask Claude for a judgment, parse the result.

    call_fn: optional override for the LLM call (used for testing without a
    real API key/network access -- pass a function that takes a prompt
    string and returns a fake response string).
    """
    articles = fetch_recent_news(symbol, max_items=max_items)

    if not articles:
        return NewsAssessment(
            symbol=symbol, sentiment="neutral", confidence=0.0,
            reasoning="No recent news found for this symbol.",
            headlines_considered=[],
        )

    prompt = build_news_prompt(symbol, articles)
    call = call_fn or (lambda p: call_claude(p, api_key))
    raw_response = call(prompt)

    return parse_news_response(symbol, raw_response, articles)


def _headline_fingerprint(articles: list) -> str:
    """
    Stable fingerprint of a headline set (order-independent) -- used to
    detect whether the news for a symbol has actually changed since the
    last check.
    """
    titles = sorted(a["title"].strip().lower() for a in articles)
    return hashlib.sha256("|".join(titles).encode("utf-8")).hexdigest()


def _load_news_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return json.loads(content) if content else {}


def _save_news_cache(cache: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def analyze_news_cached(symbol: str, api_key: str, max_items: int = 8,
                         cache_path: str = NEWS_CACHE_PATH,
                         call_fn: Optional[Callable[[str], str]] = None) -> NewsAssessment:
    """
    Same as analyze_news(), but skips the Claude call (and its cost) if the
    headline set for this symbol hasn't changed since the last cached check
    -- reuses the previous verdict instead of paying to re-analyze identical
    headlines. Headline fetching itself (yfinance + RSS) is free and always
    happens, so a genuinely new headline is still caught immediately.

    Built for monitor_positions.py, which re-checks the same held symbols
    several times a day -- most checks will find no new headlines since the
    last one. run_daily.py doesn't use this: it only checks each symbol once
    a day, so there's nothing same-day to compare against yet.
    """
    articles = fetch_recent_news(symbol, max_items=max_items)
    fingerprint = _headline_fingerprint(articles)

    cache = _load_news_cache(cache_path)
    cached = cache.get(symbol)
    if cached and cached.get("fingerprint") == fingerprint:
        return NewsAssessment(
            symbol=symbol, sentiment=cached["sentiment"], confidence=cached["confidence"],
            reasoning=f"(cached -- headlines unchanged since last check) {cached['reasoning']}",
            headlines_considered=articles,
        )

    if not articles:
        assessment = NewsAssessment(
            symbol=symbol, sentiment="neutral", confidence=0.0,
            reasoning="No recent news found for this symbol.", headlines_considered=[],
        )
    else:
        prompt = build_news_prompt(symbol, articles)
        call = call_fn or (lambda p: call_claude(p, api_key))
        raw_response = call(prompt)
        assessment = parse_news_response(symbol, raw_response, articles)

    cache[symbol] = {
        "fingerprint": fingerprint, "sentiment": assessment.sentiment,
        "confidence": assessment.confidence, "reasoning": assessment.reasoning,
    }
    _save_news_cache(cache, cache_path)
    return assessment
