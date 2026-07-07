"""
Quick standalone test for the News Agent -- run this on its own before it's
wired into any trading decisions, just to confirm the whole pipeline works
end to end with your real Anthropic API key.

Usage:
    python test_news_agent.py
"""

from config import settings
from news.news_agent import analyze_news

TEST_SYMBOL = "RELIANCE.NS"  # change this to test a different stock


def main():
    if not settings.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is empty in config/settings.py -- add your key first.")
        return

    print(f"Analyzing news for {TEST_SYMBOL}...\n")
    result = analyze_news(TEST_SYMBOL, api_key=settings.ANTHROPIC_API_KEY, max_items=settings.NEWS_MAX_ARTICLES)

    print(f"Symbol: {result.symbol}")
    print(f"Sentiment: {result.sentiment}")
    print(f"Confidence: {result.confidence:.0%}")
    print(f"Reasoning: {result.reasoning}\n")

    print(f"Headlines considered ({len(result.headlines_considered)}):")
    for h in result.headlines_considered:
        print(f"  - {h['title']} ({h['publisher']})")


if __name__ == "__main__":
    main()
