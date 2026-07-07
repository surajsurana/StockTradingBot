"""
End-to-end test: ties the Technical, Fundamental, and News agents together
into one live Research Analyst verdict for a single symbol.

Run this on your own machine (not in this sandbox) once you've added your
Anthropic API key to config/settings.py:

    python test_research_analyst.py

What it does, step by step:
1. Fetches real price history for TEST_SYMBOL (yfinance) and the Nifty 50
   index, and builds today's market-regime reading (bullish/bearish).
2. Runs every strategy in STRATEGY_REGISTRY against that price history to
   get each one's signal for TODAY specifically (None if a strategy doesn't
   want to trade today) -- applying the regime filter only to strategies
   that opt into it, same rule main.py's backtest uses.
3. Runs the fundamentals health check on the symbol (fundamentals/fundamental_agent.py).
4. Runs the News Agent on the symbol (news/news_agent.py) -- this calls
   Claude for a real bullish/bearish/neutral read on current headlines.
5. Feeds all three into the Research Analyst (research/research_analyst.py),
   which calls Claude again to synthesize one combined verdict.

This is a REAL run: it hits yfinance, RSS feeds, and the Anthropic API twice
(once for news, once for the synthesis). It costs a small, pay-per-use
Anthropic API amount and does not place any trades.
"""

from config import settings
from data.fetch_historical import fetch_daily_candles, fetch_nifty
from strategies.market_regime import build_regime_series
from strategies.technical_agent import get_technical_signals
from fundamentals.fundamental_agent import fetch_fundamentals, check_health
from news.news_agent import analyze_news
from research.research_analyst import analyze_stock

TEST_SYMBOL = "RELIANCE.NS"


def main():
    print(f"Researching {TEST_SYMBOL}...\n")

    print("1. Fetching price history + Nifty regime...")
    price_history = fetch_daily_candles(TEST_SYMBOL, period="2y")
    nifty = fetch_nifty(period="2y")
    regime_series = build_regime_series(nifty)

    print("2. Running technical strategies for today's signal...")
    technical_signals = get_technical_signals(TEST_SYMBOL, price_history, regime_series)
    for name, sig in technical_signals.items():
        print(f"   - {name}: {'no signal today' if sig is None else sig.reason}")

    print("\n3. Running fundamentals health check...")
    metrics = fetch_fundamentals(TEST_SYMBOL)
    fundamentals_result = check_health(TEST_SYMBOL, metrics, settings.FUNDAMENTALS_CRITERIA)
    print(f"   Passed: {fundamentals_result.passed}")
    for r in fundamentals_result.reasons:
        print(f"   - {r}")

    print("\n4. Running News Agent (this calls Claude)...")
    news_assessment = analyze_news(TEST_SYMBOL, api_key=settings.ANTHROPIC_API_KEY,
                                    max_items=settings.NEWS_MAX_ARTICLES)
    print(f"   Sentiment: {news_assessment.sentiment} (confidence {news_assessment.confidence:.0%})")
    print(f"   Reasoning: {news_assessment.reasoning}")

    print("\n5. Running Research Analyst synthesis (this calls Claude again)...")
    research_result = analyze_stock(
        TEST_SYMBOL, technical_signals, fundamentals_result, news_assessment,
        api_key=settings.ANTHROPIC_API_KEY,
    )

    print("\n" + "=" * 60)
    print(f"RESEARCH ANALYST VERDICT: {TEST_SYMBOL}")
    print("=" * 60)
    print(f"Verdict:    {research_result.verdict.upper()}")
    print(f"Confidence: {research_result.confidence:.0%}")
    print(f"Reasoning:  {research_result.reasoning}")


if __name__ == "__main__":
    main()
