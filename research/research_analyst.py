"""
Research Analyst -- synthesizes the three specialist agents into one view.

This is the second genuine "AI judgment" agent (after the News Agent). It
does not gather any new information itself -- it takes what the Technical
Agent (strategies/), Fundamental Agent (fundamentals/), and News Agent
(news/) have each independently concluded about a stock, and asks Claude to
weigh them together into a single combined verdict, the way a human
research analyst would combine a chart read, a balance-sheet check, and a
news scan into one recommendation rather than treating each in isolation.

Deliberately conservative design: if the fundamentals check failed outright,
this agent still runs (so you can see how technical + news read on their
own), but the verdict prompt is told to treat a fundamentals failure as a
strong red flag -- a good chart and good news don't rescue a financially
unhealthy company.

This agent does NOT place trades or size positions. It produces one
synthesized opinion per stock that will feed into the Portfolio Manager (a
later piece) alongside opinions on every other candidate stock.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from news.news_agent import call_claude


@dataclass
class ResearchAssessment:
    symbol: str
    verdict: str            # "favorable", "unfavorable", or "neutral"
    confidence: float        # 0.0-1.0
    reasoning: str
    inputs_summary: dict = field(default_factory=dict)


def _describe_technical(technical_signals: dict) -> str:
    """technical_signals: dict of strategy_name -> Signal or None."""
    lines = []
    for strategy_name, signal in technical_signals.items():
        if signal is None:
            lines.append(f"- {strategy_name}: no signal today (not proposing a trade)")
        else:
            lines.append(
                f"- {strategy_name}: proposes {signal.direction} at {signal.entry_price:.2f}, "
                f"stop-loss {signal.stop_loss:.2f}, target {signal.target:.2f} "
                f"(reason: {signal.reason})"
            )
    return "\n".join(lines) if lines else "- No active strategies reported a signal."


def _describe_fundamentals(fundamentals_result) -> str:
    verdict = "PASSED the fundamentals health check" if fundamentals_result.passed else "FAILED the fundamentals health check"
    reasons = "\n".join(f"  - {r}" for r in fundamentals_result.reasons)
    return f"- {verdict}:\n{reasons}"


def _describe_news(news_assessment) -> str:
    return (
        f"- News sentiment: {news_assessment.sentiment} "
        f"(confidence {news_assessment.confidence:.0%})\n"
        f"  Reasoning: {news_assessment.reasoning}"
    )


def _describe_price_action(price_action) -> str:
    """
    price_action: a strategies.price_action.PriceAction, or None if there
    wasn't enough history to compute it (in which case this section is
    omitted from the prompt entirely -- no partial/misleading facts).
    """
    if price_action is None:
        return ""

    def _ma_text(above, label):
        if above is None:
            return f"{label}: insufficient history"
        return f"{label}: {'above' if above else 'BELOW'}"

    lines = [
        f"- Price: Rs.{price_action.price:.2f}, {price_action.pct_off_high:+.1f}% off its "
        f"{price_action.high_lookback_days}-day high",
        f"- {_ma_text(price_action.above_20ma, '20-day MA')}, "
        f"{_ma_text(price_action.above_50ma, '50-day MA')}, "
        f"{_ma_text(price_action.above_200ma, '200-day MA')}",
    ]
    if price_action.volume_ratio is not None:
        direction = "a DOWN day" if price_action.is_down_day else "an up day"
        lines.append(f"- Today's volume is {price_action.volume_ratio:.1f}x the prior 20-day average, on {direction}")
    if price_action.pct_since_entry is not None:
        lines.append(f"- This is an existing HELD position: {price_action.pct_since_entry:+.1f}% since entry")

    return "\n\nPRICE ACTION:\n" + "\n".join(lines)


def build_synthesis_prompt(symbol: str, technical_signals: dict, fundamentals_result, news_assessment,
                            price_action=None) -> str:
    return f"""You are a senior equity research analyst synthesizing independent inputs about {symbol}, an Indian stock, into one overall view.

TECHNICAL ANALYSIS (price chart-based strategies):
{_describe_technical(technical_signals)}

FUNDAMENTALS (company financial health):
{_describe_fundamentals(fundamentals_result)}

NEWS SENTIMENT:
{_describe_news(news_assessment)}
{_describe_price_action(price_action)}

Weigh these together into one overall verdict. Important guidance:
- If fundamentals FAILED, treat this as a serious red flag -- a good chart or good news does not make a financially unhealthy company a good trade. Lean toward "unfavorable" or at best "neutral" unless the technical and news signals are exceptionally strong.
- Price action matters even when no strategy fired a signal today: a large recent decline (well off its recent high, trading below its own 20/50/200-day averages), especially on above-average DOWN-day volume, is a genuine warning sign on its own -- distribution/panic selling can be happening well before it shows up in fundamentals or in the day's headlines. Weigh this as its own factor, not just as the absence of a technical buy signal.
- If this is an existing HELD position (pct since entry is shown) that has moved meaningfully against the entry price while also showing the above warning signs, lean toward "unfavorable" even if fundamentals and news alone look fine -- the point of this check is to catch damage a price-only stop-loss hasn't triggered on yet.
- If all inputs agree (all positive or all negative), your confidence should be high.
- If the inputs conflict (e.g. good chart but bad news, or no technical signal at all), be more cautious and lower your confidence, or call it "neutral" if there's no clear case either way.
- A "neutral" verdict is a legitimate, often correct answer when there isn't a clear combined case -- don't force "favorable" or "unfavorable" just to give a decisive-sounding answer.

Respond in EXACTLY this format, nothing else:
VERDICT: <favorable|unfavorable|neutral>
CONFIDENCE: <a number between 0.0 and 1.0>
REASONING: <two or three sentences explaining how you weighed the inputs together>"""


def parse_research_response(symbol: str, raw_response: str, inputs_summary: dict) -> ResearchAssessment:
    """
    Parses Claude's structured response. Falls back to a cautious "neutral,
    low confidence" result if parsing fails, matching the same
    fail-safe philosophy as the News Agent.
    """
    verdict_match = re.search(r"VERDICT:\s*(favorable|unfavorable|neutral)", raw_response, re.IGNORECASE)
    confidence_match = re.search(r"CONFIDENCE:\s*([\d.]+)", raw_response)
    reasoning_match = re.search(r"REASONING:\s*(.+)", raw_response, re.IGNORECASE | re.DOTALL)

    if not verdict_match:
        return ResearchAssessment(
            symbol=symbol, verdict="neutral", confidence=0.0,
            reasoning=f"Could not parse a clear verdict from the model's response: {raw_response[:200]}",
            inputs_summary=inputs_summary,
        )

    verdict = verdict_match.group(1).lower()
    confidence = float(confidence_match.group(1)) if confidence_match else 0.5
    confidence = max(0.0, min(1.0, confidence))
    reasoning = reasoning_match.group(1).strip() if reasoning_match else "(no reasoning provided)"

    return ResearchAssessment(
        symbol=symbol, verdict=verdict, confidence=confidence,
        reasoning=reasoning, inputs_summary=inputs_summary,
    )


def analyze_stock(symbol: str, technical_signals: dict, fundamentals_result, news_assessment,
                   api_key: str, call_fn: Optional[Callable[[str], str]] = None,
                   price_action=None) -> ResearchAssessment:
    """
    Full pipeline: build the synthesis prompt from the agents' outputs, ask
    Claude to weigh them, parse the result.

    technical_signals: dict of strategy_name -> Signal or None (see strategies/base.py)
    fundamentals_result: a FundamentalsResult (see fundamentals/fundamental_agent.py)
    news_assessment: a NewsAssessment (see news/news_agent.py)
    price_action: an optional strategies.price_action.PriceAction -- recent
    move magnitude, position vs moving averages, volume behavior. Omitted
    from the prompt (and inputs_summary) if None, e.g. insufficient history.
    call_fn: optional override for the LLM call, for testing without a real API key.
    """
    prompt = build_synthesis_prompt(symbol, technical_signals, fundamentals_result, news_assessment, price_action)
    call = call_fn or (lambda p: call_claude(p, api_key))
    raw_response = call(prompt)

    inputs_summary = {
        "technical_signals": {k: (v is not None) for k, v in technical_signals.items()},
        "fundamentals_passed": fundamentals_result.passed,
        "news_sentiment": news_assessment.sentiment,
        "news_confidence": news_assessment.confidence,
    }
    if price_action is not None:
        inputs_summary["pct_off_high"] = price_action.pct_off_high
        inputs_summary["above_50ma"] = price_action.above_50ma
        inputs_summary["pct_since_entry"] = price_action.pct_since_entry

    return parse_research_response(symbol, raw_response, inputs_summary)
